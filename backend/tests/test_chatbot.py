from __future__ import annotations

import os
import unittest
from uuid import uuid4

from pydantic import ValidationError

os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.domain.chatbot import (  # noqa: E402
    PUBLIC_CHATBOT_CAPABILITIES,
    available_public_capabilities,
    create_public_session_token,
    create_widget_public_id,
    hash_public_session_token,
    normalize_allowed_hostname,
    normalize_allowed_hostnames,
    public_reference,
    request_origin,
    resolve_public_capabilities,
)
from app.exceptions.chatbot import ChatbotRateLimitError  # noqa: E402
from app.models.chatbot import ChatbotConfig, ChatbotSession  # noqa: E402
from app.schemas.chatbot import (  # noqa: E402
    ChatbotConfigUpdate,
    PublicChatMessageRequest,
    PublicLeadCaptureRequest,
    PublicOrderLookupRequest,
)
from app.services.chatbot_rate_limit import InMemoryChatbotRateLimiter  # noqa: E402


class ChatbotDomainTests(unittest.TestCase):
    def test_public_registry_is_small_immutable_and_excludes_internal_capabilities(self) -> None:
        self.assertIsInstance(PUBLIC_CHATBOT_CAPABILITIES, frozenset)
        self.assertEqual(len(PUBLIC_CHATBOT_CAPABILITIES), 8)
        for forbidden in (
            "read_customers", "read_analytics", "read_audit", "launch_ads",
            "change_crm_stage", "modify_workflows", "send_email",
        ):
            self.assertNotIn(forbidden, PUBLIC_CHATBOT_CAPABILITIES)

    def test_scheduling_capabilities_are_centrally_gated_to_dental(self) -> None:
        dental = available_public_capabilities("dental")
        ecommerce = available_public_capabilities("e-commerce")
        self.assertIn("book_appointment", dental)
        self.assertIn("lookup_available_appointments", dental)
        self.assertNotIn("book_appointment", ecommerce)
        with self.assertRaises(ValueError):
            resolve_public_capabilities(["book_appointment"], "retail")

    def test_domains_normalize_idna_case_and_trailing_dot(self) -> None:
        self.assertEqual(normalize_allowed_hostname("WWW.Example.COM."), "www.example.com")
        self.assertEqual(
            normalize_allowed_hostnames(["example.com", "EXAMPLE.com", "shop.example.com"]),
            ["example.com", "shop.example.com"],
        )
        self.assertTrue(normalize_allowed_hostname("bücher.example").startswith("xn--"))

    def test_domains_reject_urls_paths_credentials_ports_wildcards_and_bare_names(self) -> None:
        for value in (
            "https://example.com", "example.com/path", "user@example.com",
            "example.com:8443", "*.example.com", "javascript:alert(1)", "example",
            "127.0.0.1", "2001:db8::1",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_allowed_hostname(value)

    def test_request_origin_returns_exact_serialized_origin_and_normalized_host(self) -> None:
        self.assertEqual(
            request_origin("https://WWW.Example.com", None),
            ("www.example.com", "https://www.example.com"),
        )
        self.assertEqual(
            request_origin(None, "https://shop.example.com/products/1"),
            ("shop.example.com", "https://shop.example.com"),
        )
        with self.assertRaises(ValueError):
            request_origin(None, None)
        with self.assertRaises(ValueError):
            request_origin("https://example.com/not-an-origin", None)

    def test_widget_and_session_credentials_are_high_entropy_and_hash_only(self) -> None:
        widget_ids = {create_widget_public_id() for _ in range(100)}
        tokens = {create_public_session_token() for _ in range(100)}
        self.assertEqual(len(widget_ids), 100)
        self.assertEqual(len(tokens), 100)
        self.assertTrue(all(len(value) >= 40 for value in widget_ids))
        token = next(iter(tokens))
        digest = hash_public_session_token(token)
        self.assertEqual(len(digest), 64)
        self.assertNotIn(token, digest)

    def test_public_reference_is_stable_scoped_and_does_not_expose_uuid(self) -> None:
        value = uuid4()
        first = public_reference(b"secret" * 8, "catalog", value)
        self.assertEqual(first, public_reference(b"secret" * 8, "catalog", value))
        self.assertNotEqual(first, public_reference(b"secret" * 8, "provider", value))
        self.assertNotIn(str(value), first)

    def test_configuration_validates_consent_domains_and_privacy_url(self) -> None:
        data = self._config()
        self.assertEqual(data.allowed_domains, ["example.com"])
        hosted = self._config(enabled=True, allowed_domains=[])
        self.assertEqual(hosted.allowed_domains, [])
        with self.assertRaises(ValidationError):
            self._config(privacy_policy_url="http://example.com/privacy")
        with self.assertRaises(ValidationError):
            self._config(require_lead_consent=True, consent_text=None)

    def test_public_inputs_are_bounded_and_require_identity(self) -> None:
        with self.assertRaises(ValidationError):
            PublicChatMessageRequest(message="x" * 2001)
        with self.assertRaises(ValidationError):
            PublicChatMessageRequest(message="hello\x00world")
        with self.assertRaises(ValidationError):
            PublicLeadCaptureRequest(name="Visitor", email=None, phone=None)
        with self.assertRaises(ValidationError):
            PublicOrderLookupRequest(order_reference="A-1", email=None, phone=None)

    def test_models_never_store_raw_public_session_token(self) -> None:
        self.assertNotIn("session_token", ChatbotSession.__table__.columns)
        self.assertIn("session_token_hash", ChatbotSession.__table__.columns)
        self.assertIn(
            "uq_chatbot_configs_widget_public_id",
            {constraint.name for constraint in ChatbotConfig.__table__.constraints},
        )

    @staticmethod
    def _config(**overrides: object) -> ChatbotConfigUpdate:
        values: dict[str, object] = {
            "enabled": True,
            "display_name": "Acme AI",
            "welcome_message": "How can we help?",
            "placeholder_text": "Ask a question",
            "tone": "friendly",
            "theme": "light",
            "position": "bottom_right",
            "launcher_style": "bubble",
            "allowed_capabilities": ["answer_business_questions"],
            "allowed_domains": ["Example.com"],
            "privacy_policy_url": "https://example.com/privacy",
            "consent_text": None,
            "require_lead_consent": False,
            "default_locale": "en",
            "border_radius": 18,
        }
        values.update(overrides)
        return ChatbotConfigUpdate.model_validate(values)


class RateLimiterTests(unittest.IsolatedAsyncioTestCase):
    async def test_enforces_bucket_key_window_and_recovers_after_window(self) -> None:
        now = 100.0
        limiter = InMemoryChatbotRateLimiter(clock=lambda: now)
        await limiter.enforce(bucket="message", key="session-a", limit=2, window_seconds=60)
        await limiter.enforce(bucket="message", key="session-a", limit=2, window_seconds=60)
        with self.assertRaises(ChatbotRateLimitError):
            await limiter.enforce(bucket="message", key="session-a", limit=2, window_seconds=60)
        await limiter.enforce(bucket="message", key="session-b", limit=2, window_seconds=60)
        now += 61
        await limiter.enforce(bucket="message", key="session-a", limit=2, window_seconds=60)


if __name__ == "__main__":
    unittest.main()
