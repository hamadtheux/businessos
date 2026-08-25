from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions.operations import (
    OperationsConflictError,
    OperationsPersistenceError,
    OperationsValidationError,
)
from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.services.automation_events import record_automation_event


CustomerIdentityMatch = Literal[
    "email",
    "phone",
    "email_and_phone",
    "created",
    "none",
]

_SOURCE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_EMAIL_MAX_LENGTH = 320
_PHONE_MAX_LENGTH = 32
_MIN_PHONE_DIGITS = 7
_MAX_PHONE_DIGITS = 15
_MAX_DISPLAY_NAME_LENGTH = 160
_MAX_COMPANY_LENGTH = 160
_MAX_NOTES_LENGTH = 4_000
_MAX_TAGS = 20
_MAX_TAG_LENGTH = 40


@dataclass(frozen=True, slots=True)
class CustomerIdentityResolution:
    customer: Customer | None
    created: bool
    matched_by: CustomerIdentityMatch


def normalize_customer_email(value: str | None) -> str | None:
    """
    Normalize an email for deterministic tenant-scoped identity matching.

    This intentionally performs normalization rather than broad email
    deliverability validation. Provider-specific validation belongs at the
    ingestion boundary.
    """
    if value is None:
        return None

    normalized = value.strip().casefold()

    if not normalized:
        return None

    if not 3 <= len(normalized) <= _EMAIL_MAX_LENGTH:
        raise OperationsValidationError("Invalid customer email")

    if any(character.isspace() for character in normalized):
        raise OperationsValidationError("Invalid customer email")

    return normalized


def normalize_customer_phone(value: str | None) -> str | None:
    """
    Produce the canonical phone identity used for matching.

    Formatting characters are deliberately ignored so values such as:

      +92 300 1234567
      +92-300-1234567

    resolve to the same identity.

    The canonical identity is digits-only. The Customer record itself may
    continue storing a user-friendly phone representation.
    """
    if value is None:
        return None

    raw = value.strip()

    if not raw:
        return None

    if len(raw) > _PHONE_MAX_LENGTH:
        raise OperationsValidationError("Invalid customer phone")

    digits = "".join(character for character in raw if character.isdigit())

    if not _MIN_PHONE_DIGITS <= len(digits) <= _MAX_PHONE_DIGITS:
        raise OperationsValidationError("Invalid customer phone")

    return digits


async def resolve_customer_identity(
    session: AsyncSession,
    *,
    business_id: UUID,
    display_name: str | None,
    email: str | None,
    phone: str | None,
    source: str,
    create_if_missing: bool,
    actor_user_id: UUID | None = None,
    tags: list[str] | tuple[str, ...] | None = None,
    company: str | None = None,
    notes: str | None = None,
) -> CustomerIdentityResolution:
    """
    Resolve one customer identity inside exactly one business.

    Identity rules:

    - Email matching is case-insensitive.
    - Phone matching ignores formatting characters.
    - Name/company similarity is NEVER used as identity authority.
    - Zero matches may create a customer only when explicitly allowed.
    - Multiple matches are considered ambiguous and never guessed.
    - Conflicting email/phone identities fail closed.
    - Anonymous conversations without email/phone remain anonymous.

    This service is intended to become the canonical identity boundary for:

    - website chatbot
    - WhatsApp
    - Facebook / Instagram messaging
    - email
    - future store integrations
    - other verified inbound connectors

    Manual customer creation remains available through the normal Operations
    API and does not need to pass through this automatic identity resolver.
    """
    normalized_email = normalize_customer_email(email)
    normalized_phone = normalize_customer_phone(phone)

    _validate_source(source)

    if normalized_email is None and normalized_phone is None:
        if create_if_missing:
            raise OperationsValidationError(
                "Automatic customer creation requires an email or phone identity"
            )

        return CustomerIdentityResolution(
            customer=None,
            created=False,
            matched_by="none",
        )

    matches = await _find_customer_matches(
        session,
        business_id=business_id,
        normalized_email=normalized_email,
        normalized_phone=normalized_phone,
    )

    if len(matches) > 1:
        raise OperationsConflictError("Customer identity is ambiguous")

    if len(matches) == 1:
        customer = matches[0]

        matched_by = _matched_by(
            customer,
            normalized_email=normalized_email,
            normalized_phone=normalized_phone,
        )

        return CustomerIdentityResolution(
            customer=customer,
            created=False,
            matched_by=matched_by,
        )

    if not create_if_missing:
        return CustomerIdentityResolution(
            customer=None,
            created=False,
            matched_by="none",
        )

    customer = await _create_customer_from_identity(
        session,
        business_id=business_id,
        display_name=display_name,
        email=normalized_email,
        original_phone=phone,
        normalized_phone=normalized_phone,
        source=source,
        actor_user_id=actor_user_id,
        tags=tags,
        company=company,
        notes=notes,
    )

    return CustomerIdentityResolution(
        customer=customer,
        created=True,
        matched_by="created",
    )


async def _find_customer_matches(
    session: AsyncSession,
    *,
    business_id: UUID,
    normalized_email: str | None,
    normalized_phone: str | None,
) -> list[Customer]:
    conditions = []

    if normalized_email is not None:
        conditions.append(
            func.lower(Customer.email) == normalized_email
        )

    if normalized_phone is not None:
        conditions.append(
            func.regexp_replace(
                Customer.phone,
                "[^0-9]",
                "",
                "g",
            )
            == normalized_phone
        )

    if not conditions:
        return []

    statement = (
        select(Customer)
        .where(
            Customer.business_id == business_id,
            Customer.status != "archived",
            or_(*conditions),
        )
        .order_by(
            Customer.updated_at.desc(),
            Customer.id.desc(),
        )
        .limit(3)
    )

    try:
        result = await session.scalars(statement)
        customers = list(result.all())
    except SQLAlchemyError:
        raise OperationsPersistenceError(
            "Unable to resolve customer identity"
        ) from None

    unique = {
        customer.id: customer
        for customer in customers
    }

    return list(unique.values())


def _matched_by(
    customer: Customer,
    *,
    normalized_email: str | None,
    normalized_phone: str | None,
) -> CustomerIdentityMatch:
    email_matches = (
        normalized_email is not None
        and customer.email is not None
        and customer.email.strip().casefold() == normalized_email
    )

    stored_phone = None

    if customer.phone:
        stored_phone = "".join(
            character
            for character in customer.phone
            if character.isdigit()
        )

    phone_matches = (
        normalized_phone is not None
        and stored_phone == normalized_phone
    )

    if email_matches and phone_matches:
        return "email_and_phone"

    if email_matches:
        return "email"

    if phone_matches:
        return "phone"

    raise OperationsConflictError(
        "Resolved customer does not match the supplied identity"
    )


async def _create_customer_from_identity(
    session: AsyncSession,
    *,
    business_id: UUID,
    display_name: str | None,
    email: str | None,
    original_phone: str | None,
    normalized_phone: str | None,
    source: str,
    actor_user_id: UUID | None,
    tags: list[str] | tuple[str, ...] | None,
    company: str | None,
    notes: str | None,
) -> Customer:
    """
    Create a customer only after deterministic identity resolution returned
    zero matches.

    This deliberately does not infer names, companies, or other attributes
    using AI.
    """
    normalized_name = _normalize_display_name(
        display_name,
        email=email,
        normalized_phone=normalized_phone,
    )

    normalized_tags = _normalize_tags(tags)
    normalized_company = _normalize_optional_text(
        company,
        max_length=_MAX_COMPANY_LENGTH,
        field_name="company",
    )
    normalized_notes = _normalize_optional_text(
        notes,
        max_length=_MAX_NOTES_LENGTH,
        field_name="notes",
    )

    stored_phone = _normalize_phone_for_storage(
        original_phone,
        normalized_phone,
    )

    customer = Customer(
        business_id=business_id,
        display_name=normalized_name,
        first_name=None,
        last_name=None,
        email=email,
        phone=stored_phone,
        status="active",
        source=source,
        tags=normalized_tags,
        company=normalized_company,
        notes=normalized_notes,
        active=True,
    )

    session.add(customer)

    try:
        await session.flush()
    except SQLAlchemyError:
        raise OperationsPersistenceError(
            "Unable to create customer from identity"
        ) from None

    _record_customer_created_audit(
        session,
        business_id=business_id,
        customer=customer,
        actor_user_id=actor_user_id,
    )

    record_automation_event(
        session,
        business_id=business_id,
        event_type="customer_created",
        entity_type="customer",
        entity_id=customer.id,
        payload={
            "status": customer.status,
            "source": customer.source,
            "tags": customer.tags,
        },
    )

    return customer


def _normalize_display_name(
    value: str | None,
    *,
    email: str | None,
    normalized_phone: str | None,
) -> str:
    if value is not None:
        normalized = value.strip()

        if normalized:
            if len(normalized) > _MAX_DISPLAY_NAME_LENGTH:
                raise OperationsValidationError(
                    "Customer display name is too long"
                )

            return normalized

    # These fallbacks are deterministic identifiers, not AI/name inference.
    if email is not None:
        return email

    if normalized_phone is not None:
        return f"+{normalized_phone}"

    raise OperationsValidationError(
        "Customer display name is required"
    )


def _normalize_phone_for_storage(
    original_phone: str | None,
    normalized_phone: str | None,
) -> str | None:
    if normalized_phone is None:
        return None

    if original_phone:
        value = original_phone.strip()

        if value and len(value) <= _PHONE_MAX_LENGTH:
            return value

    return f"+{normalized_phone}"


def _normalize_tags(
    values: list[str] | tuple[str, ...] | None,
) -> list[str]:
    if not values:
        return []

    if len(values) > _MAX_TAGS:
        raise OperationsValidationError(
            "Too many customer tags"
        )

    result: list[str] = []
    seen: set[str] = set()

    for raw in values:
        if not isinstance(raw, str):
            raise OperationsValidationError(
                "Invalid customer tag"
            )

        value = raw.strip()

        if not value or len(value) > _MAX_TAG_LENGTH:
            raise OperationsValidationError(
                "Invalid customer tag"
            )

        key = value.casefold()

        if key in seen:
            continue

        seen.add(key)
        result.append(value)

    return result


def _normalize_optional_text(
    value: str | None,
    *,
    max_length: int,
    field_name: str,
) -> str | None:
    if value is None:
        return None

    normalized = value.strip()

    if not normalized:
        return None

    if len(normalized) > max_length:
        raise OperationsValidationError(
            f"Customer {field_name} is too long"
        )

    return normalized


def _validate_source(source: str) -> None:
    if (
        not isinstance(source, str)
        or _SOURCE_PATTERN.fullmatch(source.strip()) is None
    ):
        raise OperationsValidationError(
            "Invalid customer source"
        )


def _record_customer_created_audit(
    session: AsyncSession,
    *,
    business_id: UUID,
    customer: Customer,
    actor_user_id: UUID | None,
) -> None:
    entry = AuditLog(
        business_id=business_id,
        actor_user_id=actor_user_id,
        actor_type="user" if actor_user_id else "system",
        event_type="customer.created",
        entity_type="customer",
        entity_id=customer.id,
        summary=f"Created customer {customer.display_name}."[:1000],
        before_value=None,
        after_value=None,
        status="completed",
    )

    session.add(entry)