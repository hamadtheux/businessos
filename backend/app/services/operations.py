from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.operations import LEAD_STAGE_TRANSITIONS, ORDER_STATUS_TRANSITIONS, OPPORTUNITY_STATUS_TRANSITIONS
from app.exceptions.operations import (
    OperationsConflictError,
    OperationsNotFoundError,
    OperationsPersistenceError,
    OperationsStateError,
    OperationsValidationError,
)
from app.models.ai_action import AIAction
from app.models.ai_agent_execution import AIAgentExecution
from app.models.appointment import Appointment
from app.models.audit_log import AuditLog
from app.models.business_membership import BusinessMembership
from app.models.business import Business
from app.models.catalog_item import CatalogItem
from app.models.conversation import Conversation, ConversationMessage
from app.models.crm_lead import CRMLead
from app.models.customer import Customer
from app.models.notification import Notification
from app.models.opportunity import Opportunity
from app.models.order import Order, OrderLineItem
from app.models.report import BusinessReport
from app.models.service_provider import ServiceProvider
from app.schemas.operations import (
    AnalyticsPoint,
    ConversationCreate,
    ConversationResponse,
    ConversationUpdate,
    CoreAnalyticsResponse,
    CustomerCreate,
    CustomerUpdate,
    LeadCreate,
    LeadUpdate,
    MessageCreate,
    MessageResponse,
    NotificationCreate,
    OpportunityCreate,
    OpportunityUpdate,
    OrderCreate,
    OrderLineResponse,
    OrderResponse,
    ReportGenerateRequest,
)
from app.services.automation_events import record_automation_event


def _page(page: int, page_size: int) -> tuple[int, int]:
    if page < 1 or page_size < 1 or page_size > 100:
        raise OperationsValidationError
    return (page - 1) * page_size, page_size


def _search_term(search: str | None) -> str | None:
    if search is None:
        return None
    value = search.strip()
    if not value:
        return None
    if len(value) > 100:
        raise OperationsValidationError
    return value


async def _paged(session: AsyncSession, statement: Select, page: int, page_size: int):
    offset, limit = _page(page, page_size)
    try:
        total = int(await session.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0)
        result = await session.scalars(statement.offset(offset).limit(limit))
        return list(result.all()), total
    except SQLAlchemyError:
        raise OperationsPersistenceError from None


async def _member_exists(session: AsyncSession, business_id: UUID, user_id: UUID | None) -> bool:
    if user_id is None:
        return True
    return bool(await session.scalar(select(BusinessMembership.id).where(
        BusinessMembership.business_id == business_id,
        BusinessMembership.user_id == user_id,
        BusinessMembership.status == "active",
    )))


async def _customer_exists(session: AsyncSession, business_id: UUID, customer_id: UUID | None) -> bool:
    if customer_id is None:
        return True
    return bool(await session.scalar(select(Customer.id).where(Customer.business_id == business_id, Customer.id == customer_id)))


def record_audit(
    session: AsyncSession,
    *,
    business_id: UUID,
    actor_user_id: UUID | None,
    event_type: str,
    entity_type: str,
    entity_id: UUID | None,
    summary: str,
    before_value: str | None = None,
    after_value: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        business_id=business_id,
        actor_user_id=actor_user_id,
        actor_type="user" if actor_user_id else "system",
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        summary=summary[:1000],
        before_value=before_value[:500] if before_value else None,
        after_value=after_value[:500] if after_value else None,
        status="completed",
    )
    session.add(entry)
    return entry


async def list_customers(session: AsyncSession, *, business_id: UUID, page: int, page_size: int, search: str | None, status: str | None):
    statement = select(Customer).where(Customer.business_id == business_id)
    term = _search_term(search)
    if term:
        statement = statement.where(or_(Customer.display_name.icontains(term, autoescape=True), Customer.email.icontains(term, autoescape=True), Customer.phone.icontains(term, autoescape=True), Customer.company.icontains(term, autoescape=True)))
    if status:
        statement = statement.where(Customer.status == status)
    return await _paged(session, statement.order_by(Customer.updated_at.desc(), Customer.id.desc()), page, page_size)


async def get_customer(session: AsyncSession, *, business_id: UUID, customer_id: UUID) -> Customer:
    try:
        value = await session.scalar(select(Customer).where(Customer.business_id == business_id, Customer.id == customer_id))
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    if value is None:
        raise OperationsNotFoundError
    return value


async def create_customer(session: AsyncSession, *, business_id: UUID, actor_user_id: UUID, data: CustomerCreate) -> Customer:
    customer = Customer(business_id=business_id, active=data.status == "active", **data.model_dump())
    session.add(customer)
    try:
        await session.flush()
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="customer.created", entity_type="customer", entity_id=customer.id, summary=f"Created customer {customer.display_name}.")
    record_automation_event(session, business_id=business_id, event_type="customer_created", entity_type="customer", entity_id=customer.id, payload={"status": customer.status, "source": customer.source, "tags": customer.tags})
    return customer


async def update_customer(session: AsyncSession, *, business_id: UUID, customer_id: UUID, actor_user_id: UUID, data: CustomerUpdate) -> Customer:
    customer = await get_customer(session, business_id=business_id, customer_id=customer_id)
    values = data.model_dump(exclude_unset=True)
    before = customer.status
    for key, value in values.items():
        setattr(customer, key, value)
    if "status" in values:
        customer.active = values["status"] == "active"
    try:
        await session.flush()
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="customer.updated", entity_type="customer", entity_id=customer.id, summary=f"Updated customer {customer.display_name}.", before_value=f"status={before}", after_value=f"status={customer.status}")
    record_automation_event(session, business_id=business_id, event_type="customer_updated", entity_type="customer", entity_id=customer.id, payload={"status": customer.status, "previous_status": before, "source": customer.source, "tags": customer.tags})
    return customer


async def list_leads(session: AsyncSession, *, business_id: UUID, page: int, page_size: int, search: str | None, stage: str | None):
    statement = select(CRMLead).where(CRMLead.business_id == business_id)
    term = _search_term(search)
    if term:
        statement = statement.where(or_(CRMLead.display_name.icontains(term, autoescape=True), CRMLead.email.icontains(term, autoescape=True), CRMLead.company.icontains(term, autoescape=True)))
    if stage:
        statement = statement.where(CRMLead.stage == stage)
    return await _paged(session, statement.order_by(CRMLead.updated_at.desc(), CRMLead.id.desc()), page, page_size)


async def get_lead(session: AsyncSession, *, business_id: UUID, lead_id: UUID) -> CRMLead:
    try:
        value = await session.scalar(select(CRMLead).where(CRMLead.business_id == business_id, CRMLead.id == lead_id))
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    if value is None:
        raise OperationsNotFoundError
    return value


async def create_lead(session: AsyncSession, *, business_id: UUID, actor_user_id: UUID, data: LeadCreate) -> CRMLead:
    try:
        if not await _customer_exists(session, business_id, data.customer_id) or not await _member_exists(session, business_id, data.owner_user_id):
            raise OperationsValidationError
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    lead = CRMLead(business_id=business_id, **data.model_dump())
    session.add(lead)
    try:
        await session.flush()
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="crm_lead.created", entity_type="crm_lead", entity_id=lead.id, summary=f"Created lead {lead.display_name}.")
    record_automation_event(session, business_id=business_id, event_type="lead_created", entity_type="lead", entity_id=lead.id, payload={"stage": lead.stage, "priority": lead.priority, "source": lead.source, "estimated_value": float(lead.estimated_value) if lead.estimated_value is not None else None})
    return lead


async def update_lead(session: AsyncSession, *, business_id: UUID, lead_id: UUID, actor_user_id: UUID, data: LeadUpdate) -> CRMLead:
    lead = await get_lead(session, business_id=business_id, lead_id=lead_id)
    values = data.model_dump(exclude_unset=True)
    try:
        if "customer_id" in values and not await _customer_exists(session, business_id, values["customer_id"]):
            raise OperationsValidationError
        if "owner_user_id" in values and not await _member_exists(session, business_id, values["owner_user_id"]):
            raise OperationsValidationError
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    for key, value in values.items():
        setattr(lead, key, value)
    try:
        await session.flush()
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="crm_lead.updated", entity_type="crm_lead", entity_id=lead.id, summary=f"Updated lead {lead.display_name}.")
    return lead


async def change_lead_state(session: AsyncSession, *, business_id: UUID, lead_id: UUID, actor_user_id: UUID, field: str, value: str) -> CRMLead:
    lead = await get_lead(session, business_id=business_id, lead_id=lead_id)
    before = getattr(lead, field)
    if field == "stage" and value not in LEAD_STAGE_TRANSITIONS.get(before, frozenset()):
        raise OperationsStateError
    setattr(lead, field, value)
    try:
        await session.flush()
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type=f"crm_lead.{field}_changed", entity_type="crm_lead", entity_id=lead.id, summary=f"Changed lead {field}.", before_value=str(before), after_value=value)
    if field == "stage":
        record_automation_event(session, business_id=business_id, event_type="lead_stage_changed", entity_type="lead", entity_id=lead.id, payload={"stage": lead.stage, "previous_stage": before, "priority": lead.priority, "estimated_value": float(lead.estimated_value) if lead.estimated_value is not None else None})
        stage_event = {"qualified": "lead_qualified", "won": "lead_won", "lost": "lead_lost"}.get(value)
        if stage_event:
            record_automation_event(session, business_id=business_id, event_type=stage_event, entity_type="lead", entity_id=lead.id, payload={"stage": lead.stage, "previous_stage": before, "priority": lead.priority})
    return lead


async def list_orders(session: AsyncSession, *, business_id: UUID, page: int, page_size: int, search: str | None, status: str | None):
    statement = select(Order).join(Customer, and_(Customer.id == Order.customer_id, Customer.business_id == Order.business_id)).where(Order.business_id == business_id)
    term = _search_term(search)
    if term:
        statement = statement.where(or_(Order.order_number.icontains(term, autoescape=True), Customer.display_name.icontains(term, autoescape=True)))
    if status:
        statement = statement.where(Order.status == status)
    return await _paged(session, statement.order_by(Order.created_at.desc(), Order.id.desc()), page, page_size)


async def get_order(session: AsyncSession, *, business_id: UUID, order_id: UUID) -> Order:
    try:
        order = await session.scalar(select(Order).where(Order.business_id == business_id, Order.id == order_id))
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    if order is None:
        raise OperationsNotFoundError
    return order


async def order_response(session: AsyncSession, order: Order) -> OrderResponse:
    try:
        customer_name = await session.scalar(select(Customer.display_name).where(Customer.business_id == order.business_id, Customer.id == order.customer_id))
        lines = list((await session.scalars(select(OrderLineItem).where(OrderLineItem.business_id == order.business_id, OrderLineItem.order_id == order.id).order_by(OrderLineItem.created_at, OrderLineItem.id))).all())
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    if customer_name is None:
        raise OperationsPersistenceError
    return OrderResponse(
        id=order.id, business_id=order.business_id, customer_id=order.customer_id,
        customer_display_name=customer_name, order_number=order.order_number,
        status=order.status, source=order.source, currency=order.currency,
        subtotal=order.subtotal, adjustment_amount=order.adjustment_amount,
        total=order.total, notes=order.notes,
        lines=[OrderLineResponse.model_validate(line) for line in lines],
        created_at=order.created_at, updated_at=order.updated_at,
    )


async def order_responses(session: AsyncSession, orders: list[Order]) -> list[OrderResponse]:
    if not orders:
        return []
    business_id = orders[0].business_id
    order_ids = [order.id for order in orders]
    customer_ids = {order.customer_id for order in orders}
    try:
        customer_rows = (await session.execute(select(Customer.id, Customer.display_name).where(Customer.business_id == business_id, Customer.id.in_(customer_ids)))).all()
        line_rows = list((await session.scalars(select(OrderLineItem).where(OrderLineItem.business_id == business_id, OrderLineItem.order_id.in_(order_ids)).order_by(OrderLineItem.created_at, OrderLineItem.id))).all())
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    names = {customer_id: name for customer_id, name in customer_rows}
    lines_by_order: dict[UUID, list[OrderLineItem]] = {order_id: [] for order_id in order_ids}
    for line in line_rows:
        lines_by_order[line.order_id].append(line)
    try:
        return [OrderResponse(
            id=order.id, business_id=order.business_id, customer_id=order.customer_id,
            customer_display_name=names[order.customer_id], order_number=order.order_number,
            status=order.status, source=order.source, currency=order.currency,
            subtotal=order.subtotal, adjustment_amount=order.adjustment_amount,
            total=order.total, notes=order.notes,
            lines=[OrderLineResponse.model_validate(line) for line in lines_by_order[order.id]],
            created_at=order.created_at, updated_at=order.updated_at,
        ) for order in orders]
    except (KeyError, ValueError):
        raise OperationsPersistenceError from None


async def create_order(session: AsyncSession, *, business_id: UUID, actor_user_id: UUID, data: OrderCreate) -> Order:
    try:
        customer_id = await session.scalar(select(Customer.id).where(Customer.business_id == business_id, Customer.id == data.customer_id, Customer.status != "archived").with_for_update())
        if customer_id is None:
            raise OperationsValidationError
        catalog_ids = {line.catalog_item_id for line in data.lines if line.catalog_item_id}
        if catalog_ids:
            found = set((await session.scalars(select(CatalogItem.id).where(CatalogItem.business_id == business_id, CatalogItem.id.in_(catalog_ids), CatalogItem.status == "active").with_for_update())).all())
            if found != catalog_ids:
                raise OperationsValidationError
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    subtotal = sum((line.unit_price * line.quantity for line in data.lines), Decimal("0.00")).quantize(Decimal("0.01"))
    total = (subtotal + data.adjustment_amount).quantize(Decimal("0.01"))
    if total > Decimal("999999999999.99"):
        raise OperationsValidationError
    order = Order(business_id=business_id, customer_id=data.customer_id, order_number=f"ORD-{uuid4().hex[:12].upper()}", status="draft", source=data.source, currency=data.currency, subtotal=subtotal, adjustment_amount=data.adjustment_amount, total=total, notes=data.notes)
    session.add(order)
    try:
        await session.flush()
        session.add_all([OrderLineItem(business_id=business_id, order_id=order.id, catalog_item_id=line.catalog_item_id, description=line.description, quantity=line.quantity, unit_price=line.unit_price) for line in data.lines])
        await session.flush()
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="order.created", entity_type="order", entity_id=order.id, summary=f"Created order {order.order_number}.", after_value=f"total={order.total} {order.currency}")
    record_automation_event(session, business_id=business_id, event_type="order_created", entity_type="order", entity_id=order.id, payload={"status": order.status, "source": order.source, "total": float(order.total)})
    return order


async def change_order_status(session: AsyncSession, *, business_id: UUID, order_id: UUID, actor_user_id: UUID, status: str) -> Order:
    order = await get_order(session, business_id=business_id, order_id=order_id)
    if status not in ORDER_STATUS_TRANSITIONS.get(order.status, frozenset()):
        raise OperationsStateError
    before = order.status
    order.status = status
    try:
        await session.flush()
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="order.status_changed", entity_type="order", entity_id=order.id, summary=f"Changed order {order.order_number} status.", before_value=before, after_value=status)
    record_automation_event(session, business_id=business_id, event_type="order_status_changed", entity_type="order", entity_id=order.id, payload={"status": status, "previous_status": before, "total": float(order.total)})
    return order


async def list_conversations(session: AsyncSession, *, business_id: UUID, page: int, page_size: int, search: str | None, status: str | None):
    statement = select(Conversation).outerjoin(Customer, and_(Customer.id == Conversation.customer_id, Customer.business_id == Conversation.business_id)).where(Conversation.business_id == business_id)
    term = _search_term(search)
    if term:
        statement = statement.where(or_(Customer.display_name.icontains(term, autoescape=True), Conversation.external_reference.icontains(term, autoescape=True)))
    if status:
        statement = statement.where(Conversation.status == status)
    return await _paged(session, statement.order_by(Conversation.last_activity_at.desc(), Conversation.id.desc()), page, page_size)


async def get_conversation(session: AsyncSession, *, business_id: UUID, conversation_id: UUID) -> Conversation:
    try:
        value = await session.scalar(select(Conversation).where(Conversation.business_id == business_id, Conversation.id == conversation_id))
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    if value is None:
        raise OperationsNotFoundError
    return value


async def conversation_response(session: AsyncSession, conversation: Conversation, *, include_messages: bool = True) -> ConversationResponse:
    try:
        customer_name = await session.scalar(select(Customer.display_name).where(Customer.business_id == conversation.business_id, Customer.id == conversation.customer_id)) if conversation.customer_id else None
        messages = list((await session.scalars(select(ConversationMessage).where(ConversationMessage.business_id == conversation.business_id, ConversationMessage.conversation_id == conversation.id).order_by(ConversationMessage.sent_at, ConversationMessage.id))).all()) if include_messages else []
        latest = await session.scalar(select(ConversationMessage.content).where(ConversationMessage.business_id == conversation.business_id, ConversationMessage.conversation_id == conversation.id).order_by(ConversationMessage.sent_at.desc(), ConversationMessage.id.desc()).limit(1))
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    return ConversationResponse(id=conversation.id, business_id=conversation.business_id, customer_id=conversation.customer_id, customer_display_name=customer_name, channel=conversation.channel, external_reference=conversation.external_reference, status=conversation.status, assigned_user_id=conversation.assigned_user_id, last_activity_at=conversation.last_activity_at, latest_message=latest, unread=False, messages=[MessageResponse.model_validate(message) for message in messages], created_at=conversation.created_at, updated_at=conversation.updated_at)


async def conversation_responses(session: AsyncSession, conversations: list[Conversation]) -> list[ConversationResponse]:
    if not conversations:
        return []
    business_id = conversations[0].business_id
    conversation_ids = [item.id for item in conversations]
    customer_ids = {item.customer_id for item in conversations if item.customer_id}
    try:
        customer_rows = (await session.execute(select(Customer.id, Customer.display_name).where(Customer.business_id == business_id, Customer.id.in_(customer_ids)))).all() if customer_ids else []
        messages = list((await session.scalars(select(ConversationMessage).where(ConversationMessage.business_id == business_id, ConversationMessage.conversation_id.in_(conversation_ids)).order_by(ConversationMessage.sent_at.desc(), ConversationMessage.id.desc()))).all())
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    names = {customer_id: name for customer_id, name in customer_rows}
    latest: dict[UUID, str] = {}
    for message in messages:
        latest.setdefault(message.conversation_id, message.content)
    return [ConversationResponse(
        id=item.id, business_id=item.business_id, customer_id=item.customer_id,
        customer_display_name=names.get(item.customer_id), channel=item.channel,
        external_reference=item.external_reference, status=item.status,
        assigned_user_id=item.assigned_user_id, last_activity_at=item.last_activity_at,
        latest_message=latest.get(item.id), unread=False, messages=[],
        created_at=item.created_at, updated_at=item.updated_at,
    ) for item in conversations]


async def create_conversation(session: AsyncSession, *, business_id: UUID, actor_user_id: UUID, data: ConversationCreate) -> Conversation:
    try:
        if not await _customer_exists(session, business_id, data.customer_id) or not await _member_exists(session, business_id, data.assigned_user_id):
            raise OperationsValidationError
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    value = Conversation(business_id=business_id, **data.model_dump())
    session.add(value)
    try:
        await session.flush()
    except SQLAlchemyError:
        raise OperationsConflictError from None
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="conversation.created", entity_type="conversation", entity_id=value.id, summary=f"Created {value.channel} conversation record.")
    record_automation_event(session, business_id=business_id, event_type="conversation_created", entity_type="conversation", entity_id=value.id, payload={"status": value.status, "channel": value.channel})
    return value


async def update_conversation(session: AsyncSession, *, business_id: UUID, conversation_id: UUID, actor_user_id: UUID, data: ConversationUpdate) -> Conversation:
    value = await get_conversation(session, business_id=business_id, conversation_id=conversation_id)
    values = data.model_dump(exclude_unset=True)
    try:
        if "assigned_user_id" in values and not await _member_exists(session, business_id, values["assigned_user_id"]):
            raise OperationsValidationError
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    before = value.status
    for key, new_value in values.items():
        setattr(value, key, new_value)
    try:
        await session.flush()
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="conversation.updated", entity_type="conversation", entity_id=value.id, summary="Updated conversation record.", before_value=before, after_value=value.status)
    return value


async def add_message(session: AsyncSession, *, business_id: UUID, conversation_id: UUID, actor_user_id: UUID, data: MessageCreate) -> ConversationMessage:
    conversation = await get_conversation(session, business_id=business_id, conversation_id=conversation_id)
    now = datetime.now(timezone.utc)
    message = ConversationMessage(business_id=business_id, conversation_id=conversation.id, direction=data.direction, sender_type="user", sender_user_id=actor_user_id, content=data.content, sent_at=now, delivery_status="recorded")
    conversation.last_activity_at = now
    session.add(message)
    try:
        await session.flush()
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="conversation.message_recorded", entity_type="conversation", entity_id=conversation.id, summary="Recorded an internal conversation message; no external message was sent.")
    if data.direction == "inbound":
        record_automation_event(session, business_id=business_id, event_type="inbound_message_recorded", entity_type="conversation", entity_id=conversation.id, payload={"status": conversation.status, "channel": conversation.channel})
    return message


async def list_notifications(session: AsyncSession, *, business_id: UUID, user_id: UUID, page: int, page_size: int, unread_only: bool):
    statement = select(Notification).where(Notification.business_id == business_id, or_(Notification.recipient_user_id == user_id, Notification.recipient_user_id.is_(None)))
    if unread_only:
        statement = statement.where(Notification.read.is_(False))
    return await _paged(session, statement.order_by(Notification.created_at.desc(), Notification.id.desc()), page, page_size)


async def create_notification(session: AsyncSession, *, business_id: UUID, actor_user_id: UUID, data: NotificationCreate) -> Notification:
    try:
        if not await _member_exists(session, business_id, data.recipient_user_id):
            raise OperationsValidationError
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    value = Notification(business_id=business_id, **data.model_dump())
    session.add(value)
    try:
        await session.flush()
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="notification.created", entity_type="notification", entity_id=value.id, summary="Created an internal notification.")
    return value


async def mark_notification_read(session: AsyncSession, *, business_id: UUID, notification_id: UUID, user_id: UUID) -> Notification:
    try:
        value = await session.scalar(select(Notification).where(Notification.business_id == business_id, Notification.id == notification_id, or_(Notification.recipient_user_id == user_id, Notification.recipient_user_id.is_(None))))
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    if value is None:
        raise OperationsNotFoundError
    value.read = True
    try:
        await session.flush()
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    return value


async def mark_all_notifications_read(session: AsyncSession, *, business_id: UUID, user_id: UUID) -> int:
    try:
        result = await session.execute(update(Notification).where(Notification.business_id == business_id, Notification.read.is_(False), or_(Notification.recipient_user_id == user_id, Notification.recipient_user_id.is_(None))).values(read=True))
        await session.flush()
        return int(result.rowcount or 0)
    except SQLAlchemyError:
        raise OperationsPersistenceError from None


async def list_opportunities(session: AsyncSession, *, business_id: UUID, page: int, page_size: int, search: str | None, status: str | None):
    statement = select(Opportunity).where(Opportunity.business_id == business_id)
    term = _search_term(search)
    if term:
        statement = statement.where(or_(Opportunity.title.icontains(term, autoescape=True), Opportunity.description.icontains(term, autoescape=True)))
    if status:
        statement = statement.where(Opportunity.status == status)
    return await _paged(session, statement.order_by(Opportunity.updated_at.desc(), Opportunity.id.desc()), page, page_size)


async def get_opportunity(session: AsyncSession, *, business_id: UUID, opportunity_id: UUID) -> Opportunity:
    try:
        value = await session.scalar(select(Opportunity).where(Opportunity.business_id == business_id, Opportunity.id == opportunity_id))
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    if value is None:
        raise OperationsNotFoundError
    return value


async def _validate_opportunity_refs(session: AsyncSession, business_id: UUID, customer_id: UUID | None, lead_id: UUID | None) -> None:
    try:
        if not await _customer_exists(session, business_id, customer_id):
            raise OperationsValidationError
        if lead_id and not await session.scalar(select(CRMLead.id).where(CRMLead.business_id == business_id, CRMLead.id == lead_id)):
            raise OperationsValidationError
    except SQLAlchemyError:
        raise OperationsPersistenceError from None


async def create_opportunity(session: AsyncSession, *, business_id: UUID, actor_user_id: UUID, data: OpportunityCreate) -> Opportunity:
    if data.estimated_value is not None and data.currency is None:
        raise OperationsValidationError
    await _validate_opportunity_refs(session, business_id, data.customer_id, data.lead_id)
    value = Opportunity(business_id=business_id, **data.model_dump())
    session.add(value)
    try:
        await session.flush()
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="opportunity.created", entity_type="opportunity", entity_id=value.id, summary=f"Created opportunity {value.title}.")
    record_automation_event(session, business_id=business_id, event_type="opportunity_created", entity_type="opportunity", entity_id=value.id, payload={"status": value.status, "category": value.category, "priority": value.priority, "estimated_value": float(value.estimated_value) if value.estimated_value is not None else None})
    return value


async def update_opportunity(session: AsyncSession, *, business_id: UUID, opportunity_id: UUID, actor_user_id: UUID, data: OpportunityUpdate) -> Opportunity:
    value = await get_opportunity(session, business_id=business_id, opportunity_id=opportunity_id)
    values = data.model_dump(exclude_unset=True)
    resulting_value = values.get("estimated_value", value.estimated_value)
    resulting_currency = values.get("currency", value.currency)
    if resulting_value is not None and resulting_currency is None:
        raise OperationsValidationError
    await _validate_opportunity_refs(session, business_id, values.get("customer_id", value.customer_id), values.get("lead_id", value.lead_id))
    for key, new_value in values.items():
        setattr(value, key, new_value)
    try:
        await session.flush()
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="opportunity.updated", entity_type="opportunity", entity_id=value.id, summary=f"Updated opportunity {value.title}.")
    return value


async def change_opportunity_status(session: AsyncSession, *, business_id: UUID, opportunity_id: UUID, actor_user_id: UUID, status: str) -> Opportunity:
    value = await get_opportunity(session, business_id=business_id, opportunity_id=opportunity_id)
    if status not in OPPORTUNITY_STATUS_TRANSITIONS.get(value.status, frozenset()):
        raise OperationsStateError
    before = value.status
    value.status = status
    try:
        await session.flush()
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="opportunity.status_changed", entity_type="opportunity", entity_id=value.id, summary=f"Changed opportunity {value.title} status.", before_value=before, after_value=status)
    record_automation_event(session, business_id=business_id, event_type="opportunity_status_changed", entity_type="opportunity", entity_id=value.id, payload={"status": status, "previous_status": before, "category": value.category, "priority": value.priority})
    return value


async def list_audit_logs(session: AsyncSession, *, business_id: UUID, page: int, page_size: int, search: str | None, event_type: str | None):
    statement = select(AuditLog).where(AuditLog.business_id == business_id)
    term = _search_term(search)
    if term:
        statement = statement.where(or_(AuditLog.summary.icontains(term, autoescape=True), AuditLog.event_type.icontains(term, autoescape=True)))
    if event_type:
        statement = statement.where(AuditLog.event_type == event_type)
    return await _paged(session, statement.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()), page, page_size)


async def core_analytics(session: AsyncSession, *, business_id: UUID, period_start: date, period_end: date) -> CoreAnalyticsResponse:
    if period_end < period_start or (period_end - period_start).days > 366:
        raise OperationsValidationError
    try:
        timezone_name = await session.scalar(select(Business.timezone).where(Business.id == business_id))
        business_timezone = ZoneInfo(timezone_name or "UTC")
    except (SQLAlchemyError, ZoneInfoNotFoundError, ValueError, TypeError):
        raise OperationsPersistenceError from None
    start_at = datetime.combine(period_start, time.min, tzinfo=business_timezone).astimezone(timezone.utc)
    end_at = datetime.combine(period_end + timedelta(days=1), time.min, tzinfo=business_timezone).astimezone(timezone.utc)
    async def count_model(model, *conditions) -> int:
        return int(await session.scalar(select(func.count(model.id)).where(model.business_id == business_id, *conditions)) or 0)
    async def grouped(model, column, *conditions) -> dict[str, int]:
        rows = (await session.execute(select(column, func.count(model.id)).where(model.business_id == business_id, *conditions).group_by(column))).all()
        return {str(key): int(value) for key, value in rows}
    try:
        order_conditions = (Order.created_at >= start_at, Order.created_at < end_at, Order.status != "canceled")
        orders = await count_model(Order, *order_conditions)
        revenue = Decimal(await session.scalar(select(func.coalesce(func.sum(Order.total), 0)).where(Order.business_id == business_id, *order_conditions)) or 0).quantize(Decimal("0.01"))
        local_order_day = func.date(func.timezone(timezone_name, Order.created_at))
        series_rows = (await session.execute(select(local_order_day, func.coalesce(func.sum(Order.total), 0), func.count(Order.id)).where(Order.business_id == business_id, *order_conditions).group_by(local_order_day).order_by(local_order_day))).all()
        return CoreAnalyticsResponse(
            period_start=period_start, period_end=period_end,
            customers=await count_model(Customer, Customer.created_at >= start_at, Customer.created_at < end_at),
            leads=await count_model(CRMLead, CRMLead.created_at >= start_at, CRMLead.created_at < end_at),
            crm_stage_counts=await grouped(CRMLead, CRMLead.stage, CRMLead.created_at >= start_at, CRMLead.created_at < end_at),
            orders=orders, order_revenue=revenue,
            average_order_value=(revenue / orders).quantize(Decimal("0.01")) if orders else Decimal("0.00"),
            appointments=await count_model(Appointment, Appointment.starts_at >= start_at, Appointment.starts_at < end_at),
            appointment_status_counts=await grouped(Appointment, Appointment.status, Appointment.starts_at >= start_at, Appointment.starts_at < end_at),
            providers=await count_model(ServiceProvider, ServiceProvider.active.is_(True)),
            opportunities=await count_model(Opportunity, Opportunity.created_at >= start_at, Opportunity.created_at < end_at),
            opportunity_status_counts=await grouped(Opportunity, Opportunity.status, Opportunity.created_at >= start_at, Opportunity.created_at < end_at),
            ai_executions=await count_model(AIAgentExecution, AIAgentExecution.created_at >= start_at, AIAgentExecution.created_at < end_at),
            ai_actions=await count_model(AIAction, AIAction.created_at >= start_at, AIAction.created_at < end_at),
            revenue_series=[AnalyticsPoint(label=str(day), revenue=Decimal(total).quantize(Decimal("0.01")), orders=int(count)) for day, total, count in series_rows],
            lead_source_counts=await grouped(CRMLead, CRMLead.source, CRMLead.created_at >= start_at, CRMLead.created_at < end_at),
        )
    except SQLAlchemyError:
        raise OperationsPersistenceError from None


async def list_reports(session: AsyncSession, *, business_id: UUID, page: int, page_size: int, report_type: str | None):
    statement = select(BusinessReport).where(BusinessReport.business_id == business_id)
    if report_type:
        statement = statement.where(BusinessReport.report_type == report_type)
    return await _paged(session, statement.order_by(BusinessReport.generated_at.desc(), BusinessReport.id.desc()), page, page_size)


async def generate_report(session: AsyncSession, *, business_id: UUID, actor_user_id: UUID, data: ReportGenerateRequest) -> BusinessReport:
    if data.report_type == "marketing":
        from app.exceptions.marketing import MarketingPersistenceError, MarketingValidationError
        from app.services.marketing import marketing_analytics

        try:
            analytics = await marketing_analytics(session, business_id=business_id, period_start=data.period_start, period_end=data.period_end)
        except MarketingValidationError:
            raise OperationsValidationError from None
        except MarketingPersistenceError:
            raise OperationsPersistenceError from None
        metrics = analytics.model_dump(mode="json")
        summary = f"Marketing report: {analytics.conversions} conversions, {analytics.leads} leads, and {analytics.revenue} {analytics.currency} recorded revenue."
    else:
        analytics = await core_analytics(session, business_id=business_id, period_start=data.period_start, period_end=data.period_end)
        metrics = analytics.model_dump(mode="json")
        summary = f"{data.report_type.replace('_', ' ').title()} report: {analytics.orders} orders, {analytics.customers} new customers, and {analytics.appointments} appointments."
    report = BusinessReport(business_id=business_id, report_type=data.report_type, period_start=data.period_start, period_end=data.period_end, status="ready", generated_at=datetime.now(timezone.utc), summary=summary, metrics=metrics)
    session.add(report)
    try:
        await session.flush()
    except SQLAlchemyError:
        raise OperationsPersistenceError from None
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="report.generated", entity_type="business_report", entity_id=report.id, summary=f"Generated {report.report_type} report.")
    return report
