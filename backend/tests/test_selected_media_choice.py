from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.dialects import postgresql

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.exceptions.marketing import MarketingValidationError  # noqa: E402
from app.services.marketing import _attach_media_to_package  # noqa: E402
from app.services.marketing_actions import _ready_social_media_asset  # noqa: E402


class _Session:
    def __init__(self, values: list[object]) -> None:
        self.values = list(values)
        self.statements: list[object] = []

    async def scalar(self, statement):
        self.statements.append(statement)
        return self.values.pop(0) if self.values else None


class SelectedMediaChoiceTests(unittest.IsolatedAsyncioTestCase):
    def test_initial_package_media_is_recorded_on_canonical_owner(self) -> None:
        media = SimpleNamespace(
            id=uuid4(),
            content_id=None,
        )
        instagram = SimpleNamespace(
            id=uuid4(),
            platform_fields={
                "platform": "instagram",
                "media_disabled": False,
            },
        )
        facebook = SimpleNamespace(
            id=uuid4(),
            platform_fields={
                "platform": "facebook",
                "media_disabled": False,
            },
        )

        _attach_media_to_package(
            media,
            [facebook, instagram],
        )

        self.assertEqual(
            media.content_id,
            instagram.id,
        )
        self.assertEqual(
            instagram.platform_fields[
                "selected_media_asset_id"
            ],
            str(media.id),
        )
        self.assertFalse(
            instagram.platform_fields["media_disabled"]
        )
        self.assertNotIn(
            "selected_media_asset_id",
            facebook.platform_fields,
        )

    async def test_explicit_old_asset_wins_over_newest_fallback(self) -> None:
        business_id = uuid4()
        package_id = uuid4()
        selected_id = uuid4()

        current = SimpleNamespace(
            id=uuid4(),
            root_content_id=uuid4(),
            proposal_key=f"create-publish:{package_id}:facebook",
            platform_fields={
                "platform": "facebook",
                "media_disabled": False,
            },
        )

        owner = SimpleNamespace(
            id=uuid4(),
            proposal_key=f"create-publish:{package_id}:instagram:v3",
            platform_fields={
                "platform": "instagram",
                "media_disabled": False,
                "selected_media_asset_id": str(selected_id),
            },
        )

        selected_asset = SimpleNamespace(
            id=selected_id,
            media_type="image",
        )

        session = _Session(
            [
                owner,
                selected_asset,
            ]
        )

        result = await _ready_social_media_asset(
            session,  # type: ignore[arg-type]
            business_id=business_id,
            content=current,
        )

        self.assertIs(
            result,
            selected_asset,
        )

        asset_statement = session.statements[-1]
        compiled = asset_statement.compile(
            dialect=postgresql.dialect()
        )

        self.assertIn(
            business_id,
            compiled.params.values(),
        )
        self.assertIn(
            selected_id,
            compiled.params.values(),
        )
        self.assertIn(
            f"create-publish:{package_id}:",
            compiled.params.values(),
        )

    async def test_disabled_canonical_owner_returns_no_media(self) -> None:
        business_id = uuid4()
        package_id = uuid4()

        current = SimpleNamespace(
            id=uuid4(),
            root_content_id=uuid4(),
            proposal_key=f"create-publish:{package_id}:facebook",
            platform_fields={
                "platform": "facebook",
                "media_disabled": False,
            },
        )

        owner = SimpleNamespace(
            id=uuid4(),
            proposal_key=f"create-publish:{package_id}:instagram",
            platform_fields={
                "platform": "instagram",
                "media_disabled": True,
                "selected_media_asset_id": None,
            },
        )

        session = _Session([owner])

        result = await _ready_social_media_asset(
            session,  # type: ignore[arg-type]
            business_id=business_id,
            content=current,
        )

        self.assertIsNone(result)
        self.assertEqual(
            len(session.statements),
            1,
        )

    async def test_missing_explicit_asset_never_substitutes_newest(self) -> None:
        business_id = uuid4()
        package_id = uuid4()
        selected_id = uuid4()

        current = SimpleNamespace(
            id=uuid4(),
            root_content_id=uuid4(),
            proposal_key=f"create-publish:{package_id}:facebook",
            platform_fields={
                "platform": "facebook",
            },
        )

        owner = SimpleNamespace(
            id=uuid4(),
            proposal_key=f"create-publish:{package_id}:instagram",
            platform_fields={
                "platform": "instagram",
                "media_disabled": False,
                "selected_media_asset_id": str(selected_id),
            },
        )

        with self.assertRaisesRegex(
            MarketingValidationError,
            "selected_media_asset_unavailable",
        ):
            await _ready_social_media_asset(
                _Session([owner, None]),  # type: ignore[arg-type]
                business_id=business_id,
                content=current,
            )

    async def test_malformed_selected_media_identity_fails_closed(self) -> None:
        business_id = uuid4()
        package_id = uuid4()

        current = SimpleNamespace(
            id=uuid4(),
            root_content_id=uuid4(),
            proposal_key=f"create-publish:{package_id}:facebook",
            platform_fields={
                "platform": "facebook",
            },
        )

        owner = SimpleNamespace(
            id=uuid4(),
            proposal_key=f"create-publish:{package_id}:instagram",
            platform_fields={
                "platform": "instagram",
                "media_disabled": False,
                "selected_media_asset_id": "not-a-uuid",
            },
        )

        with self.assertRaisesRegex(
            MarketingValidationError,
            "selected_media_asset_invalid",
        ):
            await _ready_social_media_asset(
                _Session([owner]),  # type: ignore[arg-type]
                business_id=business_id,
                content=current,
            )


if __name__ == "__main__":
    unittest.main()
