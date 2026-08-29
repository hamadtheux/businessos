from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, settings
from app.domain.business_industries import is_commerce_business_type
from app.integrations.action_adapters import (
    ConnectorActionAdapterRegistry,
    connector_action_adapters,
)
from app.integrations.registry import require_connector
from app.models.ai_workforce import AIAgentConfig
from app.models.automation import AutomationWorkflow
from app.models.background_job import WorkerInstance
from app.models.business import Business
from app.models.business_branding import BusinessBranding
from app.models.business_knowledge_entry import BusinessKnowledgeEntry
from app.models.catalog_item import CatalogItem
from app.models.chatbot import ChatbotConfig
from app.models.commerce import CommerceConnection
from app.models.integration import IntegrationConnection
from app.models.provider_write_acceptance import ProviderWriteAcceptance
from app.schemas.activation_readiness import (
    ActivationReadinessCheck,
    ActivationReadinessResponse,
)
from app.services.action_registry import ACTION_REGISTRY


_COMMUNICATION_ACTIONS = {
    "gmail": "send_email",
    "microsoft_outlook": "send_email",
    "whatsapp_business": "send_whatsapp_message",
}
_GOVERNED_EXTERNAL_ACTIONS = frozenset(
    {
        "send_email",
        "send_whatsapp_message",
        "send_customer_message",
        "publish_social_post",
        "create_meta_campaign",
        "launch_meta_campaign",
        "create_google_ads_campaign",
        "launch_google_ads_campaign",
        "change_ad_budget",
        "pause_ad_campaign",
    }
)


@dataclass(frozen=True, slots=True)
class ActivationReadinessFacts:
    environment: str
    activation_gate_enabled: bool
    database_available: bool
    business_active: bool
    profile_ready: bool
    branding_ready: bool
    active_knowledge_entries: int
    active_catalog_items: int
    enabled_ai_agents: int
    active_workflows: int
    openai_configured: bool
    credential_store_configured: bool
    communication_connections: int
    communication_authenticated: int
    communication_healthy: int
    communication_write_ready: int
    communication_write_ready_providers: tuple[str, ...]
    commerce_applicable: bool
    commerce_healthy_connections: int
    worker_last_heartbeat_at: datetime | None
    scheduler_last_heartbeat_at: datetime | None
    worker_heartbeat_fresh: bool
    scheduler_heartbeat_fresh: bool
    approvals_fail_closed: bool
    chatbot_enabled: bool
    chatbot_allowed_domains: int


async def activation_readiness(
    session: AsyncSession,
    *,
    business: Business,
    configuration: Settings = settings,
    action_adapters: ConnectorActionAdapterRegistry = connector_action_adapters,
    now: datetime | None = None,
) -> ActivationReadinessResponse:
    instant = (now or datetime.now(UTC)).astimezone(UTC)
    counts = (
        await session.execute(
            select(
                select(func.count())
                .select_from(BusinessKnowledgeEntry)
                .where(
                    BusinessKnowledgeEntry.business_id == business.id,
                    BusinessKnowledgeEntry.status == "active",
                )
                .scalar_subquery()
                .label("active_knowledge_entries"),
                select(func.count())
                .select_from(CatalogItem)
                .where(
                    CatalogItem.business_id == business.id,
                    CatalogItem.status == "active",
                )
                .scalar_subquery()
                .label("active_catalog_items"),
                select(func.count())
                .select_from(AIAgentConfig)
                .where(
                    AIAgentConfig.business_id == business.id,
                    AIAgentConfig.enabled.is_(True),
                )
                .scalar_subquery()
                .label("enabled_ai_agents"),
                select(func.count())
                .select_from(AutomationWorkflow)
                .where(
                    AutomationWorkflow.business_id == business.id,
                    AutomationWorkflow.status == "active",
                    AutomationWorkflow.enabled.is_(True),
                )
                .scalar_subquery()
                .label("active_workflows"),
                select(func.count())
                .select_from(BusinessBranding)
                .where(
                    BusinessBranding.business_id == business.id,
                    (
                        BusinessBranding.logo_storage_key.is_not(None)
                        | BusinessBranding.primary_color.is_not(None)
                        | BusinessBranding.secondary_color.is_not(None)
                        | BusinessBranding.accent_color.is_not(None)
                    ),
                )
                .scalar_subquery()
                .label("branding_records"),
            )
        )
    ).one()._mapping

    integration_connections = list(
        (
            await session.scalars(
                select(IntegrationConnection).where(
                    IntegrationConnection.business_id == business.id,
                    IntegrationConnection.connector_type.in_(
                        tuple(_COMMUNICATION_ACTIONS)
                    ),
                )
            )
        ).all()
    )
    commerce_connections = (
        list(
            (
                await session.scalars(
                    select(CommerceConnection).where(
                        CommerceConnection.business_id == business.id
                    )
                )
            ).all()
        )
        if is_commerce_business_type(business.business_type)
        else []
    )
    chatbot = await session.scalar(
        select(ChatbotConfig).where(ChatbotConfig.business_id == business.id)
    )
    heartbeat_rows = (
        await session.execute(
            select(
                WorkerInstance.role,
                func.max(WorkerInstance.last_heartbeat_at),
            )
            .where(WorkerInstance.status == "running")
            .group_by(WorkerInstance.role)
        )
    ).all()
    heartbeats = {str(role): value for role, value in heartbeat_rows}

    authenticated = [
        connection
        for connection in integration_connections
        if _authenticated_connection(connection)
    ]
    healthy = [
        connection for connection in authenticated if _healthy_connection(connection)
    ]
    write_configured = [
        connection
        for connection in healthy
        if _write_ready_connection(
            connection,
            configuration=configuration,
            action_adapters=action_adapters,
        )
    ]

    # A connector being configured for writes is not WRITE_ACCEPTED.
    # First-client activation requires durable evidence that the exact,
    # tenant-owned connection previously completed a governed provider write.
    accepted_connection_ids: set[object] = set()
    if write_configured:
        accepted_connection_ids = set(
            (
                await session.scalars(
                    select(
                        ProviderWriteAcceptance.integration_connection_id
                    ).where(
                        ProviderWriteAcceptance.business_id == business.id,
                        ProviderWriteAcceptance.integration_connection_id.in_(
                            [connection.id for connection in write_configured]
                        ),
                    )
                )
            ).all()
        )

    write_ready = [
        connection
        for connection in write_configured
        if connection.id in accepted_connection_ids
    ]

    worker_last = heartbeats.get("worker")
    scheduler_last = heartbeats.get("scheduler")
    worker_window = timedelta(seconds=max(configuration.worker_heartbeat_seconds * 3, 30))
    scheduler_window = timedelta(
        seconds=max(int(configuration.scheduler_poll_interval_seconds * 3), 30)
    )

    facts = ActivationReadinessFacts(
        environment=configuration.environment,
        activation_gate_enabled=configuration.first_client_activation_enabled,
        database_available=True,
        business_active=business.status == "active",
        profile_ready=bool(
            business.name.strip()
            and (business.description or "").strip()
            and (business.brand_voice or "").strip()
        ),
        branding_ready=bool(counts["branding_records"]),
        active_knowledge_entries=int(counts["active_knowledge_entries"]),
        active_catalog_items=int(counts["active_catalog_items"]),
        enabled_ai_agents=int(counts["enabled_ai_agents"]),
        active_workflows=int(counts["active_workflows"]),
        openai_configured=bool(configuration.openai_api_key_value),
        credential_store_configured=(
            configuration.integration_credential_backend != "disabled"
        ),
        communication_connections=len(integration_connections),
        communication_authenticated=len(authenticated),
        communication_healthy=len(healthy),
        communication_write_ready=len(write_ready),
        communication_write_ready_providers=tuple(
            sorted({connection.connector_type for connection in write_ready})
        ),
        commerce_applicable=is_commerce_business_type(business.business_type),
        commerce_healthy_connections=sum(
            connection.status == "connected"
            and connection.health == "healthy"
            and bool(connection.credential_reference)
            and connection.last_success_at is not None
            for connection in commerce_connections
        ),
        worker_last_heartbeat_at=worker_last,
        scheduler_last_heartbeat_at=scheduler_last,
        worker_heartbeat_fresh=_fresh(worker_last, instant, worker_window),
        scheduler_heartbeat_fresh=_fresh(
            scheduler_last, instant, scheduler_window
        ),
        approvals_fail_closed=_external_actions_require_approval(),
        chatbot_enabled=bool(chatbot and chatbot.enabled),
        chatbot_allowed_domains=len(chatbot.allowed_domains) if chatbot else 0,
    )
    return build_activation_readiness(facts, generated_at=instant)


def build_activation_readiness(
    facts: ActivationReadinessFacts,
    *,
    generated_at: datetime | None = None,
) -> ActivationReadinessResponse:
    checks: list[ActivationReadinessCheck] = []

    production_ready = (
        facts.environment == "production" and facts.activation_gate_enabled
    )
    production_detail = (
        "Production configuration is active and the operator activation gate is enabled."
        if production_ready
        else "Production deployment must be externally smoke-tested before the operator activation gate is enabled."
    )
    checks.append(
        _check(
            "production_environment",
            "Production environment",
            production_ready,
            production_detail,
            "/settings",
            environment=facts.environment,
            activation_gate_enabled=facts.activation_gate_enabled,
        )
    )
    checks.extend(
        (
            _check(
                "database",
                "Database",
                facts.database_available,
                "The tenant readiness query completed against the current database.",
                "/settings",
            ),
            _check(
                "business_profile",
                "Business profile",
                facts.business_active and facts.profile_ready,
                "The business is active with a description and brand voice."
                if facts.business_active and facts.profile_ready
                else "Activate the business and complete its description and brand voice.",
                "/settings",
                business_active=facts.business_active,
            ),
            _check(
                "business_brain",
                "Business Brain",
                facts.active_knowledge_entries > 0,
                f"{facts.active_knowledge_entries} active trusted knowledge source(s) are available."
                if facts.active_knowledge_entries
                else "Add at least one active trusted knowledge source.",
                "/business-brain",
                active_sources=facts.active_knowledge_entries,
            ),
            _check(
                "ai_runtime",
                "AI runtime",
                facts.openai_configured,
                "The server-side AI provider credential is configured."
                if facts.openai_configured
                else "Install the AI provider credential through production secret management.",
                "/settings",
                configured=facts.openai_configured,
            ),
            _check(
                "ai_workforce",
                "AI workforce",
                facts.enabled_ai_agents > 0,
                f"{facts.enabled_ai_agents} AI employee(s) are enabled."
                if facts.enabled_ai_agents
                else "Enable and review at least one AI employee.",
                "/agents",
                enabled_agents=facts.enabled_ai_agents,
            ),
            _check(
                "credential_store",
                "Secure credential store",
                facts.credential_store_configured,
                "An external credential-reference backend is configured."
                if facts.credential_store_configured
                else "Configure the server-side integration credential store.",
                "/integrations",
                configured=facts.credential_store_configured,
            ),
            _check(
                "communication_connection",
                "Customer communication provider",
                facts.communication_connections > 0,
                f"{facts.communication_connections} tenant-owned communication connection(s) exist."
                if facts.communication_connections
                else "Connect Gmail or WhatsApp Business; Outlook write execution is not yet supported.",
                "/integrations",
                connections=facts.communication_connections,
            ),
            _check(
                "provider_authentication",
                "Provider authentication",
                facts.communication_authenticated > 0,
                f"{facts.communication_authenticated} communication provider(s) have current server-side authorization."
                if facts.communication_authenticated
                else "Complete provider authentication and secure credential storage.",
                "/integrations",
                authenticated=facts.communication_authenticated,
            ),
            _check(
                "provider_health",
                "Provider health",
                facts.communication_healthy > 0,
                f"{facts.communication_healthy} authenticated communication provider(s) passed the current health check."
                if facts.communication_healthy
                else "Run a successful provider health/read check.",
                "/integrations",
                healthy=facts.communication_healthy,
            ),
            _check(
                "provider_write",
                "Governed provider write",
                facts.communication_write_ready > 0,
                "A currently authenticated and healthy communication connection has durable governed provider-write acceptance evidence."
                if facts.communication_write_ready
                else "Complete current provider write configuration and a bounded governed write acceptance; OAuth, scopes, and adapter availability alone are insufficient.",
                "/integrations",
                write_ready=facts.communication_write_ready,
                providers=",".join(facts.communication_write_ready_providers) or None,
            ),
            _check(
                "worker",
                "Background worker",
                facts.worker_heartbeat_fresh,
                "The durable worker heartbeat is current."
                if facts.worker_heartbeat_fresh
                else "Restore the worker and confirm a current database heartbeat.",
                "/automations",
                last_heartbeat_at=_timestamp(facts.worker_last_heartbeat_at),
            ),
            _check(
                "scheduler",
                "Scheduler",
                facts.scheduler_heartbeat_fresh,
                "The scheduler heartbeat is current."
                if facts.scheduler_heartbeat_fresh
                else "Restore the scheduler and confirm a current database heartbeat.",
                "/automations",
                last_heartbeat_at=_timestamp(facts.scheduler_last_heartbeat_at),
            ),
            _check(
                "automation",
                "Business Autopilot",
                facts.active_workflows > 0,
                f"{facts.active_workflows} governed workflow(s) are active."
                if facts.active_workflows
                else "Prepare, test, approve, and activate at least one supported workflow.",
                "/automations",
                active_workflows=facts.active_workflows,
            ),
            _check(
                "approval_governance",
                "External-action approvals",
                facts.approvals_fail_closed,
                "All external communication, publication, campaign, spend, and pause actions remain approval-gated."
                if facts.approvals_fail_closed
                else "Restore mandatory external-action approval policy before activation.",
                "/approvals",
            ),
            _check(
                "brand_identity",
                "Brand identity",
                facts.branding_ready,
                "A saved logo or brand color is available."
                if facts.branding_ready
                else "Add a saved logo or brand color.",
                "/settings",
            ),
        )
    )

    if facts.commerce_applicable:
        checks.extend(
            (
                _check(
                    "catalog",
                    "Products and services",
                    facts.active_catalog_items > 0,
                    f"{facts.active_catalog_items} active catalog item(s) are available."
                    if facts.active_catalog_items
                    else "Add or import at least one active catalog item.",
                    "/catalog",
                    active_items=facts.active_catalog_items,
                ),
                _check(
                    "commerce",
                    "Commerce data source",
                    facts.commerce_healthy_connections > 0,
                    f"{facts.commerce_healthy_connections} commerce connection(s) have a successful healthy sync."
                    if facts.commerce_healthy_connections
                    else "Connect a commerce source and complete a successful bounded sync.",
                    "/commerce",
                    healthy_connections=facts.commerce_healthy_connections,
                ),
            )
        )
    else:
        checks.append(
            _not_applicable(
                "commerce",
                "Commerce data source",
                "Commerce activation is not required for this business type.",
                "/commerce",
            )
        )

    if facts.chatbot_enabled:
        checks.append(
            _check(
                "website_widget",
                "Website chatbot",
                facts.chatbot_allowed_domains > 0
                and facts.active_knowledge_entries > 0,
                "The enabled widget has an explicit origin allowlist and Business Brain context."
                if facts.chatbot_allowed_domains > 0
                and facts.active_knowledge_entries > 0
                else "Add explicit allowed website domains and trusted Business Brain context before enabling the widget.",
                "/chatbot",
                allowed_domains=facts.chatbot_allowed_domains,
            )
        )
    else:
        checks.append(
            _not_applicable(
                "website_widget",
                "Website chatbot",
                "The website chatbot is not enabled for this activation.",
                "/chatbot",
            )
        )

    required = [item for item in checks if item.required]
    ready_count = sum(item.state == "ready" for item in required)
    activation_ready = bool(required) and ready_count == len(required)
    return ActivationReadinessResponse(
        activation_ready=activation_ready,
        overall_status="ready" if activation_ready else "action_needed",
        ready_required_checks=ready_count,
        required_checks=len(required),
        checks=checks,
        generated_at=(generated_at or datetime.now(UTC)).astimezone(UTC),
    )


def _authenticated_connection(connection: IntegrationConnection) -> bool:
    return bool(
        connection.status in {"connected", "degraded"}
        and connection.authentication_state == "authorized"
        and connection.credential_reference
    )


def _healthy_connection(connection: IntegrationConnection) -> bool:
    return bool(
        connection.status == "connected"
        and connection.health == "healthy"
        and connection.last_health_check_at is not None
    )


def _write_ready_connection(
    connection: IntegrationConnection,
    *,
    configuration: Settings,
    action_adapters: ConnectorActionAdapterRegistry,
) -> bool:
    action_type = _COMMUNICATION_ACTIONS.get(connection.connector_type)
    if (
        action_type is None
        or configuration.external_connector_write_mode != "enabled"
        or not configuration.external_connector_writes_enabled
        or not action_adapters.supports(connection.connector_type, action_type)
    ):
        return False
    definition = require_connector(connection.connector_type)
    granted = set(connection.scopes_granted or [])
    if not set(definition.oauth_write_scopes).issubset(granted):
        return False
    if definition.resource_selection_required:
        selected_types = {
            str(item.get("resource_type"))
            for item in (connection.selected_resources or [])
        }
        if not set(definition.resource_types).issubset(selected_types):
            return False
    return True


def _external_actions_require_approval() -> bool:
    definitions = {
        definition.action_type: definition for definition in ACTION_REGISTRY.definitions
    }
    return all(
        action_type in definitions
        and definitions[action_type].always_requires_approval
        for action_type in _GOVERNED_EXTERNAL_ACTIONS
    )


def _fresh(
    value: datetime | None,
    now: datetime,
    window: timedelta,
) -> bool:
    return bool(value and value.astimezone(UTC) >= now - window)


def _timestamp(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None


def _check(
    identifier: str,
    label: str,
    ready: bool,
    detail: str,
    href: str,
    **evidence: str | int | bool | None,
) -> ActivationReadinessCheck:
    return ActivationReadinessCheck(
        id=identifier,
        label=label,
        state="ready" if ready else "action_needed",
        required=True,
        detail=detail,
        href=href,
        evidence=evidence,
    )


def _not_applicable(
    identifier: str,
    label: str,
    detail: str,
    href: str,
) -> ActivationReadinessCheck:
    return ActivationReadinessCheck(
        id=identifier,
        label=label,
        state="not_applicable",
        required=False,
        detail=detail,
        href=href,
    )
