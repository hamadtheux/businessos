from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions.operations import (
    OperationsConflictError,
    OperationsNotFoundError,
    OperationsPersistenceError,
    OperationsStateError,
    OperationsValidationError,
)
from app.models.catalog_item import CatalogItem
from app.models.conversation import Conversation, CustomerChannelIdentity
from app.models.crm_lead import CRMLead
from app.models.customer import Customer
from app.models.notification import Notification
from app.models.order import Order
from app.models.support_case import SupportCase
from app.schemas.operations import (
    SupportCaseCreate,
    SupportCaseResponse,
    SupportCaseUpdate,
    SupportMetricsResponse,
)
from app.services.automation_events import record_automation_event
from app.services.operations import conversation_response, record_audit


_ACTIVE_STATUSES = ("new", "open", "ai_handling", "waiting_for_customer", "waiting_for_business", "escalated")
_TRANSITIONS = {
    "new": {"open", "ai_handling", "escalated", "resolved", "closed"},
    "open": {"ai_handling", "waiting_for_customer", "waiting_for_business", "escalated", "resolved", "closed"},
    "ai_handling": {"open", "waiting_for_customer", "waiting_for_business", "escalated", "resolved", "closed"},
    "waiting_for_customer": {"open", "ai_handling", "escalated", "resolved", "closed"},
    "waiting_for_business": {"open", "ai_handling", "escalated", "resolved", "closed"},
    "escalated": {"open", "waiting_for_customer", "waiting_for_business", "resolved", "closed"},
    "resolved": {"open", "closed"},
    "closed": {"open"},
}


async def list_support_cases(
    session: AsyncSession,
    *,
    business_id: UUID,
    page: int,
    page_size: int,
    search: str | None,
    status: str | None,
    priority: str | None,
    channel: str | None,
) -> tuple[list[SupportCase], int]:
    if page < 1 or page_size < 1 or page_size > 100:
        raise OperationsValidationError
    statement = (
        select(SupportCase)
        .join(
            Conversation,
            and_(
                Conversation.id == SupportCase.conversation_id,
                Conversation.business_id == SupportCase.business_id,
            ),
        )
        .outerjoin(
            Customer,
            and_(
                Customer.id == SupportCase.customer_id,
                Customer.business_id == SupportCase.business_id,
            ),
        )
        .outerjoin(
            Order,
            and_(
                Order.id == SupportCase.related_order_id,
                Order.business_id == SupportCase.business_id,
            ),
        )
        .where(SupportCase.business_id == business_id)
    )
    term = search.strip() if search else None
    if term:
        if len(term) > 100:
            raise OperationsValidationError
        statement = statement.where(or_(
            SupportCase.case_number.icontains(term, autoescape=True),
            SupportCase.issue_summary.icontains(term, autoescape=True),
            Customer.display_name.icontains(term, autoescape=True),
            Order.order_number.icontains(term, autoescape=True),
        ))
    if status:
        statement = statement.where(SupportCase.status == status)
    if priority:
        statement = statement.where(SupportCase.priority == priority)
    if channel:
        statement = statement.where(Conversation.channel == channel)
    try:
        total = int(await session.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0)
        values = list((await session.scalars(
            statement.order_by(SupportCase.last_activity_at.desc(), SupportCase.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )).all())
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    return values, total


async def get_support_case(
    session: AsyncSession,
    *,
    business_id: UUID,
    case_id: UUID,
    for_update: bool = False,
) -> SupportCase:
    statement = select(SupportCase).where(
        SupportCase.id == case_id,
        SupportCase.business_id == business_id,
    )
    if for_update:
        statement = statement.with_for_update()
    try:
        case = await session.scalar(statement)
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    if case is None:
        raise OperationsNotFoundError
    return case


async def support_case_response(
    session: AsyncSession,
    case: SupportCase,
    *,
    include_conversation: bool,
) -> SupportCaseResponse:
    conversation = await session.scalar(select(Conversation).where(
        Conversation.id == case.conversation_id,
        Conversation.business_id == case.business_id,
    ))
    if conversation is None:
        raise OperationsPersistenceError
    try:
        customer_row = (await session.execute(select(Customer.display_name, Customer.email, Customer.phone).where(
            Customer.id == case.customer_id,
            Customer.business_id == case.business_id,
        ))).one_or_none() if case.customer_id else None
        identity_name = await session.scalar(select(CustomerChannelIdentity.display_name).where(
            CustomerChannelIdentity.id == conversation.customer_channel_identity_id,
            CustomerChannelIdentity.business_id == case.business_id,
        )) if conversation.customer_channel_identity_id else None
        order_number = await session.scalar(select(Order.order_number).where(Order.id == case.related_order_id, Order.business_id == case.business_id)) if case.related_order_id else None
        product_name = await session.scalar(select(CatalogItem.name).where(CatalogItem.id == case.related_product_id, CatalogItem.business_id == case.business_id)) if case.related_product_id else None
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    customer_name, customer_email, customer_phone = customer_row or (None, None, None)
    return SupportCaseResponse(
        id=case.id,
        business_id=case.business_id,
        case_number=case.case_number,
        customer_id=case.customer_id,
        customer_display_name=customer_name or identity_name,
        customer_email=customer_email,
        customer_phone=customer_phone,
        conversation_id=case.conversation_id,
        integration_connection_id=case.integration_connection_id,
        channel=conversation.channel,
        assigned_user_id=case.assigned_user_id,
        assigned_ai_role=case.assigned_ai_role,
        status=case.status,
        priority=case.priority,
        category=case.category,
        issue_summary=case.issue_summary,
        escalation_reason=case.escalation_reason,
        resolution_summary=case.resolution_summary,
        source=case.source,
        related_order_id=case.related_order_id,
        related_order_number=order_number,
        related_product_id=case.related_product_id,
        related_product_name=product_name,
        related_lead_id=case.related_lead_id,
        opened_at=case.opened_at,
        last_activity_at=case.last_activity_at,
        escalated_at=case.escalated_at,
        resolved_at=case.resolved_at,
        closed_at=case.closed_at,
        conversation=(
            await conversation_response(session, conversation, include_messages=True)
            if include_conversation
            else None
        ),
        created_at=case.created_at,
        updated_at=case.updated_at,
    )


async def support_case_responses(
    session: AsyncSession,
    cases: list[SupportCase],
) -> list[SupportCaseResponse]:
    if not cases:
        return []
    business_id = cases[0].business_id
    conversation_ids = {item.conversation_id for item in cases}
    customer_ids = {item.customer_id for item in cases if item.customer_id}
    order_ids = {item.related_order_id for item in cases if item.related_order_id}
    product_ids = {item.related_product_id for item in cases if item.related_product_id}
    try:
        conversations = list((await session.scalars(select(Conversation).where(Conversation.business_id == business_id, Conversation.id.in_(conversation_ids)))).all())
        identity_ids = {item.customer_channel_identity_id for item in conversations if item.customer_channel_identity_id}
        customer_rows = (await session.execute(select(Customer.id, Customer.display_name, Customer.email, Customer.phone).where(Customer.business_id == business_id, Customer.id.in_(customer_ids)))).all() if customer_ids else []
        identity_rows = (await session.execute(select(CustomerChannelIdentity.id, CustomerChannelIdentity.display_name).where(CustomerChannelIdentity.business_id == business_id, CustomerChannelIdentity.id.in_(identity_ids)))).all() if identity_ids else []
        order_rows = (await session.execute(select(Order.id, Order.order_number).where(Order.business_id == business_id, Order.id.in_(order_ids)))).all() if order_ids else []
        product_rows = (await session.execute(select(CatalogItem.id, CatalogItem.name).where(CatalogItem.business_id == business_id, CatalogItem.id.in_(product_ids)))).all() if product_ids else []
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    conversation_by_id = {item.id: item for item in conversations}
    customer_by_id = {row[0]: (row[1], row[2], row[3]) for row in customer_rows}
    identity_by_id = {row[0]: row[1] for row in identity_rows}
    order_by_id = dict(order_rows)
    product_by_id = dict(product_rows)
    responses: list[SupportCaseResponse] = []
    for case in cases:
        conversation = conversation_by_id.get(case.conversation_id)
        if conversation is None:
            raise OperationsPersistenceError
        customer_name, customer_email, customer_phone = customer_by_id.get(case.customer_id, (None, None, None))
        responses.append(SupportCaseResponse(
            id=case.id, business_id=case.business_id, case_number=case.case_number,
            customer_id=case.customer_id,
            customer_display_name=customer_name or identity_by_id.get(conversation.customer_channel_identity_id),
            customer_email=customer_email, customer_phone=customer_phone,
            conversation_id=case.conversation_id,
            integration_connection_id=case.integration_connection_id,
            channel=conversation.channel, assigned_user_id=case.assigned_user_id,
            assigned_ai_role=case.assigned_ai_role, status=case.status,
            priority=case.priority, category=case.category,
            issue_summary=case.issue_summary, escalation_reason=case.escalation_reason,
            resolution_summary=case.resolution_summary, source=case.source,
            related_order_id=case.related_order_id,
            related_order_number=order_by_id.get(case.related_order_id),
            related_product_id=case.related_product_id,
            related_product_name=product_by_id.get(case.related_product_id),
            related_lead_id=case.related_lead_id,
            opened_at=case.opened_at, last_activity_at=case.last_activity_at,
            escalated_at=case.escalated_at, resolved_at=case.resolved_at,
            closed_at=case.closed_at, conversation=None,
            created_at=case.created_at, updated_at=case.updated_at,
        ))
    return responses


async def create_support_case(
    session: AsyncSession,
    *,
    business_id: UUID,
    actor_user_id: UUID,
    data: SupportCaseCreate,
) -> SupportCase:
    conversation = await _owned_conversation(session, business_id, data.conversation_id)
    await _validate_related(session, business_id=business_id, order_id=data.related_order_id, product_id=data.related_product_id, lead_id=data.related_lead_id)
    existing = await session.scalar(select(SupportCase).where(
        SupportCase.business_id == business_id,
        SupportCase.conversation_id == conversation.id,
        SupportCase.status.in_(_ACTIVE_STATUSES),
    ).with_for_update())
    if existing is not None:
        raise OperationsConflictError
    instant = datetime.now(UTC)
    case_id = uuid4()
    case = SupportCase(
        id=case_id, business_id=business_id,
        case_number=f"SUP-{case_id.hex[:10].upper()}",
        customer_id=conversation.customer_id,
        conversation_id=conversation.id,
        integration_connection_id=conversation.integration_connection_id,
        assigned_user_id=conversation.assigned_user_id,
        assigned_ai_role="support",
        status="escalated" if data.escalation_reason else "open",
        priority=data.priority, category=data.category,
        issue_summary=data.issue_summary,
        escalation_reason=data.escalation_reason,
        source=conversation.channel,
        related_order_id=data.related_order_id,
        related_product_id=data.related_product_id,
        related_lead_id=data.related_lead_id,
        opened_at=instant, last_activity_at=instant,
        escalated_at=instant if data.escalation_reason else None,
    )
    session.add(case)
    if data.escalation_reason:
        conversation.status = "escalated"
        conversation.handling_state = "escalated"
        _notify_escalation(session, case)
    try:
        await session.flush()
    except IntegrityError:
        raise OperationsConflictError from None
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    _record_case_event(session, case=case, actor_user_id=actor_user_id, event_type="support_case_created", summary=f"Created support case {case.case_number}.")
    return case


async def update_support_case(
    session: AsyncSession,
    *,
    business_id: UUID,
    case_id: UUID,
    actor_user_id: UUID,
    data: SupportCaseUpdate,
) -> SupportCase:
    # All support writes lock the canonical conversation before the case.
    # This keeps AI escalation, human updates, creation, and resolution on one
    # deterministic lock order and avoids conversation/case deadlocks.
    case_snapshot = await get_support_case(
        session,
        business_id=business_id,
        case_id=case_id,
        for_update=False,
    )
    conversation = await _owned_conversation(
        session,
        business_id,
        case_snapshot.conversation_id,
    )
    case = await get_support_case(
        session,
        business_id=business_id,
        case_id=case_id,
        for_update=True,
    )
    if case.conversation_id != conversation.id:
        raise OperationsPersistenceError

    values = data.model_dump(exclude_unset=True)
    next_status = values.get("status")
    if next_status and next_status != case.status and next_status not in _TRANSITIONS[case.status]:
        raise OperationsStateError
    await _validate_related(
        session, business_id=business_id,
        order_id=values.get("related_order_id"),
        product_id=values.get("related_product_id"),
        lead_id=values.get("related_lead_id"),
        member_id=values.get("assigned_user_id"),
    )
    before = case.status
    instant = datetime.now(UTC)
    for key, value in values.items():
        setattr(case, key, value)
    case.last_activity_at = instant
    if next_status == "escalated":
        case.escalated_at = case.escalated_at or instant
        _notify_escalation(session, case)
    if next_status == "resolved":
        if not case.resolution_summary:
            raise OperationsValidationError
        case.resolved_at = instant
    elif next_status == "closed":
        case.closed_at = instant
    elif next_status == "open" and before in {"resolved", "closed"}:
        case.resolved_at = None
        case.closed_at = None
    if next_status == "escalated":
        conversation.status = "escalated"
        conversation.handling_state = "escalated"
        if case.assigned_user_id is not None:
            conversation.assigned_user_id = case.assigned_user_id

    elif next_status in {"resolved", "closed"}:
        conversation.status = "resolved"
        conversation.handling_state = "ai_paused"

    elif next_status == "ai_handling":
        conversation.status = "open"
        conversation.handling_state = "ai_active"
        # AI has control of the conversation again. Support-case ownership may
        # remain assigned, but the conversation itself is no longer human-owned.
        conversation.assigned_user_id = None

    elif next_status in {
        "open",
        "waiting_for_customer",
        "waiting_for_business",
    }:
        conversation.status = "open"
        if case.assigned_user_id is not None:
            conversation.handling_state = "human_takeover"
            conversation.assigned_user_id = case.assigned_user_id
        else:
            conversation.handling_state = "ai_paused"
            conversation.assigned_user_id = None
    try:
        await session.flush()
    except IntegrityError:
        raise OperationsConflictError from None
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    event_type = "support_case_escalated" if next_status == "escalated" else "support_case_resolved" if next_status == "resolved" else "support_case_updated"
    _record_case_event(session, case=case, actor_user_id=actor_user_id, event_type=event_type, summary=f"Updated support case {case.case_number} from {before} to {case.status}.")
    return case


async def upsert_escalated_case(
    session: AsyncSession,
    *,
    business_id: UUID,
    conversation: Conversation,
    reason: str,
    actor_user_id: UUID | None,
    issue_summary: str | None = None,
) -> SupportCase:
    conversation = await _owned_conversation(
        session,
        business_id,
        conversation.id,
    )
    case = await session.scalar(select(SupportCase).where(
        SupportCase.business_id == business_id,
        SupportCase.conversation_id == conversation.id,
        SupportCase.status.in_(_ACTIVE_STATUSES),
    ).with_for_update())
    instant = datetime.now(UTC)
    should_notify = case is None or case.status != "escalated"
    if case is None:
        case_id = uuid4()
        case = SupportCase(
            id=case_id, business_id=business_id,
            case_number=f"SUP-{case_id.hex[:10].upper()}",
            customer_id=conversation.customer_id,
            conversation_id=conversation.id,
            integration_connection_id=conversation.integration_connection_id,
            assigned_user_id=conversation.assigned_user_id,
            assigned_ai_role="support", status="escalated", priority="high",
            category=_support_category(issue_summary or reason),
            issue_summary=(issue_summary or "Customer conversation needs human assistance.")[:500],
            escalation_reason=reason[:1000], source=conversation.channel,
            opened_at=instant, last_activity_at=instant, escalated_at=instant,
        )
        session.add(case)
        event_type = "support_case_created"
    else:
        case.status = "escalated"
        case.priority = "high" if case.priority in {"low", "medium"} else case.priority
        case.escalation_reason = reason[:1000]
        case.last_activity_at = instant
        case.escalated_at = case.escalated_at or instant
        event_type = "support_case_escalated"
    conversation.status = "escalated"
    conversation.handling_state = "escalated"
    if should_notify:
        _notify_escalation(session, case)
    await session.flush()
    _record_case_event(session, case=case, actor_user_id=actor_user_id, event_type=event_type, summary=f"Escalated support case {case.case_number}.")
    return case


async def sync_case_for_conversation_control(
    session: AsyncSession,
    *,
    business_id: UUID,
    conversation: Conversation,
    action: str,
    actor_user_id: UUID,
) -> SupportCase | None:
    """Keep support-case ownership/state aligned with conversation controls."""
    if action not in {"take_over", "resume_ai", "pause_ai", "reopen"}:
        return None

    case = await session.scalar(
        select(SupportCase)
        .where(
            SupportCase.business_id == business_id,
            SupportCase.conversation_id == conversation.id,
            SupportCase.status.in_(_ACTIVE_STATUSES),
        )
        .with_for_update()
    )

    # Reopening a resolved conversation should reopen the most recent
    # resolved/closed case rather than creating a second support workflow.
    if action == "reopen" and case is None:
        case = await session.scalar(
            select(SupportCase)
            .where(
                SupportCase.business_id == business_id,
                SupportCase.conversation_id == conversation.id,
                SupportCase.status.in_(("resolved", "closed")),
            )
            .order_by(
                SupportCase.last_activity_at.desc(),
                SupportCase.id.desc(),
            )
            .limit(1)
            .with_for_update()
        )

    if case is None:
        return None

    before = case.status
    instant = datetime.now(UTC)

    if action == "take_over":
        case.status = "open"
        case.assigned_user_id = actor_user_id
        case.assigned_ai_role = None

    elif action == "resume_ai":
        case.status = "ai_handling"
        case.assigned_user_id = None
        case.assigned_ai_role = "support"

    elif action == "pause_ai":
        case.status = "open"
        case.assigned_user_id = None
        case.assigned_ai_role = None

    elif action == "reopen":
        case.status = "open"
        case.assigned_user_id = actor_user_id
        case.assigned_ai_role = None
        case.resolved_at = None
        case.closed_at = None
        case.resolution_summary = None

    case.last_activity_at = instant

    _record_case_event(
        session,
        case=case,
        actor_user_id=actor_user_id,
        event_type="support_case_updated",
        summary=(
            f"Synchronized support case {case.case_number} "
            f"from {before} to {case.status} after conversation {action}."
        ),
    )
    return case


async def resolve_active_case_for_conversation(
    session: AsyncSession,
    *,
    business_id: UUID,
    conversation: Conversation,
    resolution_summary: str,
    actor_user_id: UUID,
) -> SupportCase | None:
    conversation = await _owned_conversation(
        session,
        business_id,
        conversation.id,
    )
    case = await session.scalar(select(SupportCase).where(
        SupportCase.business_id == business_id,
        SupportCase.conversation_id == conversation.id,
        SupportCase.status.in_(_ACTIVE_STATUSES),
    ).with_for_update())
    if case is None:
        return None
    case.status = "resolved"
    case.resolution_summary = resolution_summary[:2000]
    case.resolved_at = datetime.now(UTC)
    case.last_activity_at = case.resolved_at
    _record_case_event(session, case=case, actor_user_id=actor_user_id, event_type="support_case_resolved", summary=f"Resolved support case {case.case_number}.")
    return case


async def support_metrics(session: AsyncSession, *, business_id: UUID) -> SupportMetricsResponse:
    today = datetime.now(UTC).date()
    try:
        rows = (await session.execute(select(SupportCase.status, func.count()).where(
            SupportCase.business_id == business_id,
        ).group_by(SupportCase.status))).all()
        resolved_today = int(await session.scalar(select(func.count()).select_from(SupportCase).where(
            SupportCase.business_id == business_id,
            SupportCase.resolved_at.is_not(None),
            func.date(SupportCase.resolved_at) == today,
        )) or 0)
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    counts = dict(rows)
    return SupportMetricsResponse(
        open_issues=sum(int(counts.get(item, 0)) for item in _ACTIVE_STATUSES),
        ai_handling=int(counts.get("ai_handling", 0)),
        escalated=int(counts.get("escalated", 0)),
        waiting_for_customer=int(counts.get("waiting_for_customer", 0)),
        resolved_today=resolved_today,
    )


async def _owned_conversation(session: AsyncSession, business_id: UUID, conversation_id: UUID) -> Conversation:
    conversation = await session.scalar(select(Conversation).where(Conversation.id == conversation_id, Conversation.business_id == business_id).with_for_update())
    if conversation is None:
        raise OperationsNotFoundError
    return conversation


async def _validate_related(
    session: AsyncSession,
    *,
    business_id: UUID,
    order_id: UUID | None,
    product_id: UUID | None,
    lead_id: UUID | None,
    member_id: UUID | None = None,
) -> None:
    from app.models.business_membership import BusinessMembership

    checks = (
        (Order, order_id), (CatalogItem, product_id), (CRMLead, lead_id),
    )
    for model, value in checks:
        if value is not None and not await session.scalar(select(model.id).where(model.id == value, model.business_id == business_id)):
            raise OperationsValidationError
    if member_id is not None and not await session.scalar(select(BusinessMembership.id).where(
        BusinessMembership.business_id == business_id,
        BusinessMembership.user_id == member_id,
        BusinessMembership.status == "active",
    )):
        raise OperationsValidationError


def _notify_escalation(session: AsyncSession, case: SupportCase) -> None:
    session.add(Notification(
        business_id=case.business_id, recipient_user_id=case.assigned_user_id,
        category="support_escalation", title=f"Support case {case.case_number} escalated",
        message="A customer conversation requires human attention.", priority="high",
        related_entity_type="support_case", related_entity_id=case.id, read=False,
    ))


def _record_case_event(
    session: AsyncSession,
    *,
    case: SupportCase,
    actor_user_id: UUID | None,
    event_type: str,
    summary: str,
) -> None:
    record_automation_event(session, business_id=case.business_id, event_type=event_type, entity_type="support_case", entity_id=case.id, payload={"status": case.status, "priority": case.priority, "category": case.category})
    record_audit(session, business_id=case.business_id, actor_user_id=actor_user_id, event_type=event_type.replace("_", ".", 1), entity_type="support_case", entity_id=case.id, summary=summary)


def _support_category(value: str) -> str:
    text = value.casefold()
    rules = (
        ("refund", "refund"), ("return", "return"), ("cancel", "order"),
        ("deliver", "delivery"), ("missing", "delivery"), ("damage", "complaint"),
        ("payment", "payment"), ("invoice", "payment"), ("appointment", "appointment"),
        ("account", "account"), ("technical", "technical"), ("product", "product"),
    )
    return next((category for keyword, category in rules if keyword in text), "general")
