from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint

os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.exceptions.operations import OperationsNotFoundError  # noqa: E402
from app.models.conversation import Conversation, ConversationMessage, CustomerChannelIdentity  # noqa: E402
from app.models.support_case import SupportCase  # noqa: E402
from app.schemas.operations import SupportCaseCreate, SupportCaseUpdate  # noqa: E402
from app.services import operations as operations_service  # noqa: E402
from app.services import support as support_service  # noqa: E402


BUSINESS_ID = UUID("e1000000-0000-4000-8000-000000000001")
OTHER_BUSINESS_ID = UUID("e2000000-0000-4000-8000-000000000002")
USER_ID = UUID("e3000000-0000-4000-8000-000000000003")
NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)


def _conversation(*, business_id: UUID = BUSINESS_ID, handling_state: str = "ai_active") -> Conversation:
    return Conversation(
        id=uuid4(),
        business_id=business_id,
        customer_id=None,
        customer_channel_identity_id=None,
        integration_connection_id=None,
        channel="website",
        external_reference="website-session-1",
        external_resource_reference=None,
        status="open",
        handling_state=handling_state,
        unread_count=1,
        assigned_user_id=None,
        last_activity_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )


class _Session:
    def __init__(self, scalar_results=None) -> None:
        self.scalar_results = list(scalar_results or [])
        self.added: list[object] = []

    async def scalar(self, _statement):
        return self.scalar_results.pop(0) if self.scalar_results else None

    def add(self, value: object) -> None:
        if getattr(value, "id", None) is None:
            value.id = uuid4()  # type: ignore[attr-defined]
        self.added.append(value)

    async def flush(self) -> None:
        return None


class CustomerSupportModelTests(unittest.TestCase):
    def test_support_and_provider_identity_models_are_tenant_bound(self) -> None:
        for model in (SupportCase, CustomerChannelIdentity):
            with self.subTest(model=model.__name__):
                self.assertIn("business_id", model.__table__.columns)
                self.assertTrue(any(
                    isinstance(item, ForeignKeyConstraint) and len(item.column_keys) == 2
                    for item in model.__table__.constraints
                ))
                self.assertTrue(any(isinstance(item, CheckConstraint) for item in model.__table__.constraints))

        self.assertTrue(any(
            isinstance(item, UniqueConstraint)
            and set(item.columns.keys()) == {
                "business_id",
                "provider",
                "external_resource_reference",
                "external_user_reference",
            }
            for item in CustomerChannelIdentity.__table__.constraints
        ))
        active_case_index = next(
            item for item in SupportCase.__table__.indexes
            if isinstance(item, Index) and item.name == "uq_support_cases_active_conversation"
        )
        self.assertTrue(active_case_index.unique)


    def test_conversation_thread_uniqueness_covers_nullable_resource_boundaries(self) -> None:
        indexes = {
            item.name: item
            for item in Conversation.__table__.indexes
        }

        expected = {
            "uq_conversations_provider_thread",
            "uq_conversations_provider_thread_without_resource",
            "uq_conversations_local_thread",
        }

        self.assertTrue(expected.issubset(indexes))

        for name in expected:
            self.assertTrue(indexes[name].unique)

        provider_where = str(
            indexes["uq_conversations_provider_thread"]
            .dialect_options["postgresql"]["where"]
        )
        legacy_where = str(
            indexes["uq_conversations_provider_thread_without_resource"]
            .dialect_options["postgresql"]["where"]
        )
        local_where = str(
            indexes["uq_conversations_local_thread"]
            .dialect_options["postgresql"]["where"]
        )

        self.assertIn(
            "external_resource_reference IS NOT NULL",
            provider_where,
        )
        self.assertIn(
            "external_resource_reference IS NULL",
            legacy_where,
        )
        self.assertIn(
            "integration_connection_id IS NULL",
            local_where,
        )


class CustomerSupportServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_case_creation_links_the_existing_canonical_conversation(self) -> None:
        conversation = _conversation()
        session = _Session([conversation, None])

        case = await support_service.create_support_case(
            session,  # type: ignore[arg-type]
            business_id=BUSINESS_ID,
            actor_user_id=USER_ID,
            data=SupportCaseCreate(
                conversation_id=conversation.id,
                category="delivery",
                priority="high",
                issue_summary="Customer reports a damaged delivery.",
            ),
        )

        self.assertEqual(case.business_id, BUSINESS_ID)
        self.assertEqual(case.conversation_id, conversation.id)
        self.assertEqual(case.status, "open")
        self.assertEqual(case.assigned_ai_role, "support")
        self.assertTrue(any(isinstance(item, SupportCase) for item in session.added))
        self.assertFalse(any(
            isinstance(item, ConversationMessage)
            for item in session.added
        ))

    async def test_manual_escalated_case_creation_notifies_business(self) -> None:
        conversation = _conversation()
        session = _Session([conversation, None])

        case = await support_service.create_support_case(
            session,  # type: ignore[arg-type]
            business_id=BUSINESS_ID,
            actor_user_id=USER_ID,
            data=SupportCaseCreate(
                conversation_id=conversation.id,
                category="complaint",
                priority="high",
                issue_summary="Customer requires human help.",
                escalation_reason="Escalated manually by the business.",
            ),
        )

        self.assertEqual(case.status, "escalated")
        self.assertEqual(conversation.status, "escalated")
        self.assertEqual(conversation.handling_state, "escalated")
        self.assertEqual(
            len([
                item
                for item in session.added
                if item.__class__.__name__ == "Notification"
            ]),
            1,
        )

    async def test_escalation_creates_one_active_case_and_notification(self) -> None:
        conversation = _conversation()
        session = _Session([conversation, None])

        case = await support_service.upsert_escalated_case(
            session,  # type: ignore[arg-type]
            business_id=BUSINESS_ID,
            conversation=conversation,
            reason="Customer requested a human after repeated uncertainty.",
            actor_user_id=None,
            issue_summary="Damaged item complaint",
        )

        self.assertEqual(case.status, "escalated")
        self.assertEqual(case.category, "complaint")
        self.assertEqual(conversation.status, "escalated")
        self.assertEqual(conversation.handling_state, "escalated")
        self.assertEqual(
            len([item for item in session.added if item.__class__.__name__ == "Notification"]),
            1,
        )

    async def test_resolve_requires_summary_and_updates_same_conversation(self) -> None:
        conversation = _conversation(handling_state="human_takeover")
        case = SupportCase(
            id=uuid4(), business_id=BUSINESS_ID, case_number="SUP-ONE",
            customer_id=None, conversation_id=conversation.id,
            integration_connection_id=None, assigned_user_id=USER_ID,
            assigned_ai_role="support", status="open", priority="medium",
            category="general", issue_summary="Customer needs help.",
            escalation_reason=None, resolution_summary=None, source="website",
            related_order_id=None, related_product_id=None, related_lead_id=None,
            opened_at=NOW, last_activity_at=NOW, escalated_at=None,
            resolved_at=None, closed_at=None, created_at=NOW, updated_at=NOW,
        )
        session = _Session([case, conversation, case])

        updated = await support_service.update_support_case(
            session,  # type: ignore[arg-type]
            business_id=BUSINESS_ID,
            case_id=case.id,
            actor_user_id=USER_ID,
            data=SupportCaseUpdate(status="resolved", resolution_summary="Replacement dispatched."),
        )

        self.assertEqual(updated.status, "resolved")
        self.assertEqual(updated.resolution_summary, "Replacement dispatched.")
        self.assertEqual(conversation.status, "resolved")
        self.assertEqual(conversation.handling_state, "ai_paused")

    async def test_cross_tenant_case_creation_fails_without_fallback(self) -> None:
        with self.assertRaises(OperationsNotFoundError):
            await support_service.create_support_case(
                _Session([None]),  # type: ignore[arg-type]
                business_id=OTHER_BUSINESS_ID,
                actor_user_id=USER_ID,
                data=SupportCaseCreate(
                    conversation_id=uuid4(),
                    issue_summary="Must not cross tenant boundary.",
                ),
            )

    async def test_conversation_control_synchronizes_existing_support_case(self) -> None:
        conversation = _conversation()

        case = SupportCase(
            id=uuid4(),
            business_id=BUSINESS_ID,
            case_number="SUP-SYNC",
            customer_id=None,
            conversation_id=conversation.id,
            integration_connection_id=None,
            assigned_user_id=None,
            assigned_ai_role="support",
            status="escalated",
            priority="high",
            category="general",
            issue_summary="Needs human assistance.",
            escalation_reason="AI escalated.",
            resolution_summary=None,
            source="website",
            related_order_id=None,
            related_product_id=None,
            related_lead_id=None,
            opened_at=NOW,
            last_activity_at=NOW,
            escalated_at=NOW,
            resolved_at=None,
            closed_at=None,
            created_at=NOW,
            updated_at=NOW,
        )

        takeover_session = _Session([conversation, case])

        await operations_service.control_conversation(
            takeover_session,  # type: ignore[arg-type]
            business_id=BUSINESS_ID,
            conversation_id=conversation.id,
            actor_user_id=USER_ID,
            action="take_over",
        )

        self.assertEqual(conversation.handling_state, "human_takeover")
        self.assertEqual(conversation.assigned_user_id, USER_ID)
        self.assertEqual(case.status, "open")
        self.assertEqual(case.assigned_user_id, USER_ID)
        self.assertIsNone(case.assigned_ai_role)

        resume_session = _Session([conversation, case])

        await operations_service.control_conversation(
            resume_session,  # type: ignore[arg-type]
            business_id=BUSINESS_ID,
            conversation_id=conversation.id,
            actor_user_id=USER_ID,
            action="resume_ai",
        )

        self.assertEqual(conversation.handling_state, "ai_active")
        self.assertIsNone(conversation.assigned_user_id)
        self.assertEqual(case.status, "ai_handling")
        self.assertIsNone(case.assigned_user_id)
        self.assertEqual(case.assigned_ai_role, "support")


    async def test_human_takeover_and_ai_resume_are_backend_enforced(self) -> None:
        conversation = _conversation()
        takeover_session = _Session([conversation])
        await operations_service.control_conversation(
            takeover_session,  # type: ignore[arg-type]
            business_id=BUSINESS_ID,
            conversation_id=conversation.id,
            actor_user_id=USER_ID,
            action="take_over",
        )
        self.assertEqual(conversation.handling_state, "human_takeover")
        self.assertEqual(conversation.assigned_user_id, USER_ID)
        self.assertEqual(
            next(item for item in takeover_session.added if isinstance(item, ConversationMessage)).sender_type,
            "system",
        )

        resume_session = _Session([conversation])
        await operations_service.control_conversation(
            resume_session,  # type: ignore[arg-type]
            business_id=BUSINESS_ID,
            conversation_id=conversation.id,
            actor_user_id=USER_ID,
            action="resume_ai",
        )
        self.assertEqual(conversation.handling_state, "ai_active")
        self.assertEqual(conversation.status, "open")
        self.assertIsNone(conversation.assigned_user_id)


if __name__ == "__main__":
    unittest.main()
