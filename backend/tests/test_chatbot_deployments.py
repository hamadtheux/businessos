from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.models.automation_intelligence import ChatbotDeployment  # noqa: E402
from app.services.chatbot import install_hosted_deployment, list_deployment_targets  # noqa: E402


class ChatbotDeploymentTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_embed_is_advanced_and_platform_states_are_truthful(self) -> None:
        business = SimpleNamespace(id=uuid4())
        config = SimpleNamespace(id=uuid4(), widget_public_id="w_opaque_public_id")
        session = _Session()
        with patch(
            "app.services.chatbot.get_or_create_config", new=AsyncMock(return_value=config),
        ):
            result = await list_deployment_targets(session, business=business)
        targets = {item.target_type: item for item in result.targets}
        self.assertTrue(targets["hosted"].automatic_install)
        self.assertEqual(targets["manual_embed"].state, "needs_manual_step")
        self.assertIn("Advanced", targets["manual_embed"].display_name)
        for platform in ("shopify", "wordpress", "wix", "webflow", "squarespace"):
            self.assertFalse(targets[platform].automatic_install)
            self.assertNotEqual(targets[platform].state, "installed")
        self.assertIn("data-widget-id", result.advanced_embed_snippet)
        self.assertNotIn(str(business.id), result.advanced_embed_snippet)

    async def test_hosted_install_is_real_platform_state_not_external_claim(self) -> None:
        business = SimpleNamespace(id=uuid4())
        config = SimpleNamespace(id=uuid4(), widget_public_id="w_opaque_public_id", enabled=False)
        session = _Session()
        with patch(
            "app.services.chatbot.get_or_create_config", new=AsyncMock(return_value=config),
        ):
            result = await install_hosted_deployment(
                session, business=business, actor_user_id=uuid4(),
            )
        deployment = next(item for item in session.added if isinstance(item, ChatbotDeployment))
        self.assertTrue(config.enabled)
        self.assertEqual(result.state, "installed")
        self.assertEqual(result.provider_key, "aibos_hosted")
        self.assertEqual(deployment.verification_status, "healthy")
        self.assertIn("hosted.html?widget=", result.hosted_url or "")


class _Scalars:
    def all(self):
        return []


class _Session:
    def __init__(self) -> None:
        self.added: list[object] = []

    async def scalar(self, _statement):
        return None

    async def scalars(self, _statement):
        return _Scalars()

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        for value in self.added:
            if getattr(value, "id", None) is None:
                value.id = uuid4()
