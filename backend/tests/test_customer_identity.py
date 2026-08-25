from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import UUID, uuid4

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault(
    "AIBOS_AUTH_SECRET_KEY",
    "x" * 32,
)

from app.exceptions.operations import (  # noqa: E402
    OperationsConflictError,
    OperationsValidationError,
)
from app.models.audit_log import AuditLog  # noqa: E402
from app.models.customer import Customer  # noqa: E402
from app.services.customer_identity import (  # noqa: E402
    normalize_customer_email,
    normalize_customer_phone,
    resolve_customer_identity,
)


BUSINESS_ID = uuid4()
OTHER_BUSINESS_ID = uuid4()
USER_ID = uuid4()
NOW = datetime(2026, 8, 25, tzinfo=UTC)


class CustomerIdentityTests(unittest.IsolatedAsyncioTestCase):
    def test_email_normalization_is_case_insensitive(self) -> None:
        self.assertEqual(
            normalize_customer_email(
                "  QA.Customer@Example.COM  "
            ),
            "qa.customer@example.com",
        )

    def test_blank_email_remains_missing(self) -> None:
        self.assertIsNone(
            normalize_customer_email("   ")
        )

    def test_email_with_whitespace_is_rejected(self) -> None:
        with self.assertRaises(
            OperationsValidationError
        ):
            normalize_customer_email(
                "qa customer@example.com"
            )

    def test_phone_normalization_ignores_formatting(self) -> None:
        self.assertEqual(
            normalize_customer_phone(
                "+92 300-1234567"
            ),
            "923001234567",
        )

    def test_too_short_phone_is_rejected(self) -> None:
        with self.assertRaises(
            OperationsValidationError
        ):
            normalize_customer_phone("123")

    def test_too_long_phone_identity_is_rejected(self) -> None:
        with self.assertRaises(
            OperationsValidationError
        ):
            normalize_customer_phone(
                "+1234567890123456"
            )

    async def test_matches_existing_customer_by_email(self) -> None:
        customer = _customer(
            email="qa.customer@example.com",
            phone=None,
        )

        session = _IdentitySession(
            matches=[customer]
        )

        result = await resolve_customer_identity(
            session,
            business_id=BUSINESS_ID,
            display_name="QA Customer",
            email="QA.Customer@Example.COM",
            phone=None,
            source="website_chatbot",
            create_if_missing=True,
        )

        self.assertIs(
            result.customer,
            customer,
        )
        self.assertFalse(result.created)
        self.assertEqual(
            result.matched_by,
            "email",
        )

    async def test_matches_existing_customer_by_phone(self) -> None:
        customer = _customer(
            email=None,
            phone="+92 300 1234567",
        )

        session = _IdentitySession(
            matches=[customer]
        )

        result = await resolve_customer_identity(
            session,
            business_id=BUSINESS_ID,
            display_name="QA Customer",
            email=None,
            phone="+92-300-1234567",
            source="whatsapp",
            create_if_missing=True,
        )

        self.assertIs(
            result.customer,
            customer,
        )
        self.assertFalse(result.created)
        self.assertEqual(
            result.matched_by,
            "phone",
        )

    async def test_matches_existing_customer_by_email_and_phone(
        self,
    ) -> None:
        customer = _customer(
            email="qa.customer@example.com",
            phone="+92 300 1234567",
        )

        session = _IdentitySession(
            matches=[customer]
        )

        result = await resolve_customer_identity(
            session,
            business_id=BUSINESS_ID,
            display_name="QA Customer",
            email="QA.CUSTOMER@example.com",
            phone="+92-300-1234567",
            source="website_chatbot",
            create_if_missing=True,
        )

        self.assertFalse(result.created)
        self.assertEqual(
            result.matched_by,
            "email_and_phone",
        )

    async def test_zero_matches_can_create_customer(
        self,
    ) -> None:
        session = _IdentitySession(
            matches=[]
        )

        with patch(
            "app.services.customer_identity."
            "record_automation_event"
        ) as automation_event:
            result = await resolve_customer_identity(
                session,
                business_id=BUSINESS_ID,
                display_name="QA Customer Ali",
                email="QA.Customer.Ali@Example.com",
                phone="+92 300 1234567",
                source="website_chatbot",
                create_if_missing=True,
                tags=[
                    "Website chatbot",
                    "QA",
                    "qa",
                ],
                company="QA Retail Store",
                notes=(
                    "Production acceptance customer."
                ),
            )

        self.assertTrue(result.created)
        self.assertEqual(
            result.matched_by,
            "created",
        )
        self.assertIsNotNone(result.customer)

        customer = result.customer

        assert customer is not None

        self.assertEqual(
            customer.business_id,
            BUSINESS_ID,
        )
        self.assertEqual(
            customer.display_name,
            "QA Customer Ali",
        )
        self.assertEqual(
            customer.email,
            "qa.customer.ali@example.com",
        )
        self.assertEqual(
            customer.phone,
            "+92 300 1234567",
        )
        self.assertEqual(
            customer.source,
            "website_chatbot",
        )
        self.assertEqual(
            customer.tags,
            [
                "Website chatbot",
                "QA",
            ],
        )
        self.assertEqual(
            customer.company,
            "QA Retail Store",
        )
        self.assertTrue(customer.active)

        audit_entries = [
            item
            for item in session.added
            if isinstance(item, AuditLog)
        ]

        self.assertEqual(
            len(audit_entries),
            1,
        )
        self.assertEqual(
            audit_entries[0].event_type,
            "customer.created",
        )
        self.assertEqual(
            audit_entries[0].business_id,
            BUSINESS_ID,
        )
        self.assertEqual(
            audit_entries[0].entity_id,
            customer.id,
        )

        automation_event.assert_called_once()

        kwargs = automation_event.call_args.kwargs

        self.assertEqual(
            kwargs["business_id"],
            BUSINESS_ID,
        )
        self.assertEqual(
            kwargs["event_type"],
            "customer_created",
        )
        self.assertEqual(
            kwargs["entity_type"],
            "customer",
        )
        self.assertEqual(
            kwargs["entity_id"],
            customer.id,
        )
        self.assertEqual(
            kwargs["payload"]["source"],
            "website_chatbot",
        )

    async def test_missing_display_name_uses_email_fallback(
        self,
    ) -> None:
        session = _IdentitySession(
            matches=[]
        )

        with patch(
            "app.services.customer_identity."
            "record_automation_event"
        ):
            result = await resolve_customer_identity(
                session,
                business_id=BUSINESS_ID,
                display_name=None,
                email="qa@example.com",
                phone=None,
                source="email",
                create_if_missing=True,
            )

        assert result.customer is not None

        self.assertEqual(
            result.customer.display_name,
            "qa@example.com",
        )

    async def test_anonymous_identity_is_not_created(
        self,
    ) -> None:
        session = _IdentitySession(
            matches=[]
        )

        result = await resolve_customer_identity(
            session,
            business_id=BUSINESS_ID,
            display_name="Anonymous Visitor",
            email=None,
            phone=None,
            source="website_chatbot",
            create_if_missing=False,
        )

        self.assertIsNone(result.customer)
        self.assertFalse(result.created)
        self.assertEqual(
            result.matched_by,
            "none",
        )
        self.assertEqual(
            session.added,
            [],
        )

    async def test_anonymous_auto_creation_is_rejected(
        self,
    ) -> None:
        session = _IdentitySession(
            matches=[]
        )

        with self.assertRaises(
            OperationsValidationError
        ):
            await resolve_customer_identity(
                session,
                business_id=BUSINESS_ID,
                display_name="Anonymous Visitor",
                email=None,
                phone=None,
                source="website_chatbot",
                create_if_missing=True,
            )

    async def test_ambiguous_identity_fails_closed(
        self,
    ) -> None:
        session = _IdentitySession(
            matches=[
                _customer(
                    email="qa@example.com",
                    phone=None,
                ),
                _customer(
                    email=None,
                    phone="+92 300 1234567",
                ),
            ]
        )

        with self.assertRaises(
            OperationsConflictError
        ):
            await resolve_customer_identity(
                session,
                business_id=BUSINESS_ID,
                display_name="QA Customer",
                email="qa@example.com",
                phone="+92 300 1234567",
                source="website_chatbot",
                create_if_missing=True,
            )

        self.assertFalse(
            any(
                isinstance(item, Customer)
                for item in session.added
            )
        )

    async def test_no_match_does_not_create_when_disabled(
        self,
    ) -> None:
        session = _IdentitySession(
            matches=[]
        )

        result = await resolve_customer_identity(
            session,
            business_id=BUSINESS_ID,
            display_name="QA Customer",
            email="qa@example.com",
            phone=None,
            source="whatsapp",
            create_if_missing=False,
        )

        self.assertIsNone(result.customer)
        self.assertFalse(result.created)
        self.assertEqual(
            result.matched_by,
            "none",
        )

    async def test_customer_lookup_is_tenant_scoped(
        self,
    ) -> None:
        session = _IdentitySession(
            matches=[]
        )

        await resolve_customer_identity(
            session,
            business_id=BUSINESS_ID,
            display_name="QA Customer",
            email="qa@example.com",
            phone=None,
            source="website_chatbot",
            create_if_missing=False,
        )

        self.assertEqual(
            len(session.statements),
            1,
        )

        statement = session.statements[0]
        sql = str(statement)

        self.assertIn(
            "customers.business_id",
            sql,
        )
        self.assertIn(
            "customers.status !=",
            sql,
        )

        compiled = statement.compile()
        values = list(
            compiled.params.values()
        )

        self.assertIn(
            BUSINESS_ID,
            values,
        )

    async def test_invalid_source_is_rejected_before_database_lookup(
        self,
    ) -> None:
        session = _IdentitySession(
            matches=[]
        )

        with self.assertRaises(
            OperationsValidationError
        ):
            await resolve_customer_identity(
                session,
                business_id=BUSINESS_ID,
                display_name="QA Customer",
                email="qa@example.com",
                phone=None,
                source="Website Chatbot",
                create_if_missing=True,
            )

        self.assertEqual(
            session.statements,
            [],
        )


class _ScalarCollection:
    def __init__(
        self,
        values: list[Customer],
    ) -> None:
        self.values = values

    def all(self) -> list[Customer]:
        return self.values


class _IdentitySession:
    def __init__(
        self,
        *,
        matches: list[Customer],
    ) -> None:
        self.matches = matches
        self.added: list[object] = []
        self.statements: list[object] = []
        self.flush_calls = 0

    async def scalars(
        self,
        statement,
    ) -> _ScalarCollection:
        self.statements.append(statement)

        return _ScalarCollection(
            self.matches
        )

    def add(
        self,
        value: object,
    ) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flush_calls += 1

        for value in self.added:
            if (
                isinstance(value, Customer)
                and value.id is None
            ):
                value.id = uuid4()


def _customer(
    *,
    email: str | None,
    phone: str | None,
    business_id: UUID = BUSINESS_ID,
) -> Customer:
    return Customer(
        id=uuid4(),
        business_id=business_id,
        display_name="Existing QA Customer",
        first_name=None,
        last_name=None,
        email=email,
        phone=phone,
        status="active",
        source="manual",
        tags=[],
        company=None,
        notes=None,
        active=True,
        created_at=NOW,
        updated_at=NOW,
    )


if __name__ == "__main__":
    unittest.main()