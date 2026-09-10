from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault(
    "AIBOS_AUTH_SECRET_KEY",
    "x" * 32,
)

from app.core.config import settings  # noqa: E402
from app.integrations.action_adapters import (  # noqa: E402
    ConnectorRequestNotSentError,
)
from app.integrations.credentials import CredentialMaterial  # noqa: E402
from app.integrations.provider_action_adapters import (  # noqa: E402
    ProviderConnectorActionAdapter,
)
from app.schemas.ai_action_payload import (  # noqa: E402
    PublishSocialPostPayload,
)
from app.services.marketing_actions import _connector_state  # noqa: E402


TOKEN = CredentialMaterial(
    values={"access_token": "server-only-system-user-token"}
)


class _ScriptedHttp:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    async def request_json(
        self,
        method: str,
        url: str,
        **kwargs,
    ):
        self.calls.append(
            {
                "method": method,
                "url": url,
                **kwargs,
            }
        )
        if not self.responses:
            raise AssertionError(
                f"Unexpected provider request: {method} {url}"
            )

        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _ScalarSession:
    def __init__(self, value: object) -> None:
        self.value = value

    async def scalar(self, _statement):
        return self.value


class MetaPublishProviderTests(unittest.IsolatedAsyncioTestCase):
    def _adapter(
        self,
        connector_type: str,
        http: _ScriptedHttp,
    ) -> ProviderConnectorActionAdapter:
        return ProviderConnectorActionAdapter(
            connector_type=connector_type,
            configuration=settings.model_copy(
                update={
                    "meta_graph_api_version": "v26.0",
                }
            ),
            http=http,
        )

    async def test_facebook_text_uses_selected_page_access_token(self) -> None:
        http = _ScriptedHttp(
            [
                {
                    "id": "page-one",
                    "access_token": "page-access-token",
                },
                {"id": "page-one_post-one"},
            ]
        )

        result = await self._adapter(
            "facebook",
            http,
        ).execute(
            credentials=TOKEN,
            action_type="publish_social_post",
            payload=PublishSocialPostPayload(
                platform="facebook",
                content="A real customer-facing post.",
                media_refs=[],
            ),
            selected_resources=(
                {
                    "resource_type": "facebook_page",
                    "external_reference": "page-one",
                },
            ),
            delivery_target=None,
            idempotency_key="publish:test:text",
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(
            result.external_reference_id,
            "page-one_post-one",
        )
        self.assertEqual(len(http.calls), 2)

        token_call, publish_call = http.calls

        self.assertEqual(token_call["method"], "GET")
        self.assertTrue(
            str(token_call["url"]).endswith(
                "/v26.0/page-one"
            )
        )
        self.assertEqual(
            token_call["params"],
            {
                "fields": "access_token",
                "access_token": "server-only-system-user-token",
            },
        )

        self.assertEqual(publish_call["method"], "POST")
        self.assertTrue(
            str(publish_call["url"]).endswith(
                "/v26.0/page-one/feed"
            )
        )
        self.assertEqual(
            publish_call["headers"]["Authorization"],
            "Bearer page-access-token",
        )
        self.assertEqual(
            publish_call["data"],
            {
                "message": "A real customer-facing post.",
            },
        )

    async def test_facebook_image_posts_the_exact_dispatch_url(self) -> None:
        signed_url = (
            "https://media.example.test/"
            "businesses/tenant/marketing/image.png"
            "?X-Amz-Signature=safe-test-signature"
        )

        http = _ScriptedHttp(
            [
                {
                    "id": "page-one",
                    "access_token": "page-access-token",
                },
                {"id": "photo-one"},
            ]
        )

        result = await self._adapter(
            "facebook",
            http,
        ).execute(
            credentials=TOKEN,
            action_type="publish_social_post",
            payload=PublishSocialPostPayload(
                platform="facebook",
                content="Caption stays attached to the selected image.",
                media_refs=[signed_url],
                media_type="image",
            ),
            selected_resources=(
                {
                    "resource_type": "facebook_page",
                    "external_reference": "page-one",
                },
            ),
            delivery_target=None,
            idempotency_key="publish:test:image",
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(
            result.external_reference_id,
            "photo-one",
        )
        self.assertEqual(len(http.calls), 2)

        publish_call = http.calls[1]
        self.assertEqual(
            publish_call["method"],
            "POST",
        )
        self.assertTrue(
            str(publish_call["url"]).endswith(
                "/v26.0/page-one/photos"
            )
        )
        self.assertEqual(
            publish_call["headers"]["Authorization"],
            "Bearer page-access-token",
        )
        self.assertEqual(
            publish_call["data"],
            {
                "url": signed_url,
                "caption": (
                    "Caption stays attached to the selected image."
                ),
            },
        )

    async def test_facebook_video_fails_before_any_provider_request(
        self,
    ) -> None:
        http = _ScriptedHttp([])

        with self.assertRaisesRegex(
            ConnectorRequestNotSentError,
            "facebook_video_publishing_not_supported",
        ):
            await self._adapter(
                "facebook",
                http,
            ).execute(
                credentials=TOKEN,
                action_type="publish_social_post",
                payload=PublishSocialPostPayload(
                    platform="facebook",
                    content="Video caption",
                    media_refs=[
                        "https://media.example.test/video.mp4"
                    ],
                    media_type="video",
                ),
                selected_resources=(
                    {
                        "resource_type": "facebook_page",
                        "external_reference": "page-one",
                    },
                ),
                delivery_target=None,
                idempotency_key="publish:test:facebook-video",
            )

        self.assertEqual(http.calls, [])

    async def test_instagram_video_fails_before_any_provider_request(
        self,
    ) -> None:
        http = _ScriptedHttp([])

        with self.assertRaisesRegex(
            ConnectorRequestNotSentError,
            "instagram_video_publishing_not_supported",
        ):
            await self._adapter(
                "instagram",
                http,
            ).execute(
                credentials=TOKEN,
                action_type="publish_social_post",
                payload=PublishSocialPostPayload(
                    platform="instagram",
                    content="Reel caption",
                    media_refs=[
                        "https://media.example.test/video.mp4"
                    ],
                    media_type="video",
                ),
                selected_resources=(
                    {
                        "resource_type": "instagram_account",
                        "external_reference": "instagram-one",
                    },
                ),
                delivery_target=None,
                idempotency_key="publish:test:instagram-video",
            )

        self.assertEqual(http.calls, [])

    async def test_connector_state_requires_social_write_scope(
        self,
    ) -> None:
        missing_scope = SimpleNamespace(
            scopes_granted=[
                "pages_show_list",
                "pages_read_engagement",
            ],
        )

        with patch(
            "app.services.marketing_actions."
            "connector_action_adapters.supports",
            return_value=True,
        ):
            state = await _connector_state(
                _ScalarSession(missing_scope),
                business_id=__import__("uuid").uuid4(),
                connector_type="facebook",
                action_type="publish_social_post",
            )

        self.assertEqual(
            state["connector_state"],
            "connection_required",
        )
        self.assertIn(
            "Reconnect facebook",
            str(state["connector_message"]),
        )

    async def test_connector_state_accepts_real_social_write_scope(
        self,
    ) -> None:
        ready = SimpleNamespace(
            scopes_granted=[
                "pages_show_list",
                "pages_read_engagement",
                "pages_manage_posts",
            ],
        )

        with patch(
            "app.services.marketing_actions."
            "connector_action_adapters.supports",
            return_value=True,
        ):
            state = await _connector_state(
                _ScalarSession(ready),
                business_id=__import__("uuid").uuid4(),
                connector_type="facebook",
                action_type="publish_social_post",
            )

        self.assertEqual(
            state["connector_state"],
            "ready_after_approval",
        )


if __name__ == "__main__":
    unittest.main()
