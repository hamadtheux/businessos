from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.automation import TRIGGER_TYPES
from app.exceptions.automation import AutomationPersistenceError, AutomationValidationError
from app.models.automation import AutomationEvent


SAFE_EVENT_FIELDS = frozenset({
    "status", "previous_status", "stage", "previous_stage", "priority", "source",
    "channel", "category", "objective", "total", "estimated_value", "tags",
    "provider_id", "appointment_type_id", "starts_at", "scheduled_for", "name",
    "connector_type", "health",
    "reason", "delivery_status",
})


def record_automation_event(
    session: AsyncSession,
    *,
    business_id: UUID,
    event_type: str,
    entity_type: str,
    entity_id: UUID | None,
    payload: dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
) -> AutomationEvent:
    """Append a bounded outbox event in the caller's transaction (no commit)."""
    if event_type not in TRIGGER_TYPES or not entity_type or len(entity_type) > 64:
        raise AutomationValidationError("event_invalid")
    safe_payload = _safe_payload(payload or {})
    event = AutomationEvent(
        id=uuid4(),
        business_id=business_id,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=safe_payload,
        occurred_at=occurred_at or datetime.now(UTC),
        status="pending",
        processed_at=None,
        failure_code=None,
    )
    # Unit-level domain fakes are intentionally not treated as a durable
    # outbox. Production callers always pass a real AsyncSession.
    if isinstance(session, AsyncSession):
        session.add(event)
    return event


async def list_automation_events(
    session: AsyncSession, *, business_id: UUID, page: int, page_size: int
) -> tuple[list[AutomationEvent], int]:
    if page < 1 or page_size < 1 or page_size > 100:
        raise AutomationValidationError("pagination_invalid")
    where = AutomationEvent.business_id == business_id
    try:
        total = int(await session.scalar(select(func.count()).select_from(AutomationEvent).where(where)) or 0)
        values = list((await session.scalars(
            select(AutomationEvent).where(where).order_by(
                AutomationEvent.occurred_at.desc(), AutomationEvent.id.desc()
            ).offset((page - 1) * page_size).limit(page_size)
        )).all())
    except SQLAlchemyError:
        raise AutomationPersistenceError("Unable to list automation events") from None
    return values, total


def _safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in payload.items():
        if key not in SAFE_EVENT_FIELDS:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[key] = value[:500] if isinstance(value, str) else value
        elif key == "tags" and isinstance(value, list):
            result[key] = [str(item)[:80] for item in value[:50]]
    if len(str(result)) > 10_000:
        raise AutomationValidationError("event_payload_too_large")
    return result
