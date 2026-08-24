from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class AutomationWorkflow(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "automation_workflows"
    __table_args__ = (
        UniqueConstraint("id", "business_id", name="uq_automation_workflows_id_business"),
        CheckConstraint("char_length(btrim(name)) BETWEEN 1 AND 180", name="valid_name"),
        CheckConstraint("description IS NULL OR char_length(description) <= 2000", name="valid_description"),
        CheckConstraint("status IN ('draft','active','paused','archived')", name="valid_status"),
        CheckConstraint("current_version >= 1", name="valid_current_version"),
        CheckConstraint("char_length(btrim(trigger_type)) BETWEEN 1 AND 64", name="valid_trigger_type"),
        CheckConstraint("char_length(btrim(timezone)) BETWEEN 1 AND 64", name="valid_timezone"),
        CheckConstraint("jsonb_typeof(schedule_definition) = 'object'", name="valid_schedule_definition"),
        CheckConstraint("(status = 'active') = enabled", name="consistent_enabled_status"),
        Index("ix_automation_workflows_business_status_updated", "business_id", "status", "updated_at", "id"),
        Index("ix_automation_workflows_business_next_run", "business_id", "next_run_at", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft", server_default="draft")
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    trigger_type: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC", server_default="UTC")
    schedule_definition: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class AutomationWorkflowVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "automation_workflow_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workflow_id", "business_id"],
            ["automation_workflows.id", "automation_workflows.business_id"],
            name="fk_automation_versions_workflow_business",
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "business_id", "workflow_id", name="uq_automation_versions_id_business_workflow"),
        UniqueConstraint("workflow_id", "version", name="uq_automation_versions_workflow_version"),
        CheckConstraint("version >= 1", name="valid_version"),
        Index("ix_automation_versions_business_workflow_version", "business_id", "workflow_id", "version"),
    )

    business_id: Mapped[UUID] = mapped_column(nullable=False)
    workflow_id: Mapped[UUID] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class AutomationNode(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "automation_nodes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workflow_version_id", "business_id", "workflow_id"],
            ["automation_workflow_versions.id", "automation_workflow_versions.business_id", "automation_workflow_versions.workflow_id"],
            name="fk_automation_nodes_version_business_workflow",
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "business_id", "workflow_version_id", name="uq_automation_nodes_id_business_version"),
        UniqueConstraint("workflow_version_id", "node_key", "business_id", name="uq_automation_nodes_version_key_business"),
        CheckConstraint("node_type IN ('trigger','condition','branch','action','delay','approval','ai','internal_operation','end')", name="valid_node_type"),
        CheckConstraint("char_length(btrim(name)) BETWEEN 1 AND 180", name="valid_name"),
        CheckConstraint("jsonb_typeof(configuration) = 'object'", name="valid_configuration"),
        CheckConstraint("position_x BETWEEN -100000 AND 100000 AND position_y BETWEEN -100000 AND 100000", name="valid_position"),
        CheckConstraint("order_index BETWEEN 0 AND 10000", name="valid_order"),
        Index("ix_automation_nodes_business_version_order", "business_id", "workflow_version_id", "order_index", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(nullable=False)
    workflow_id: Mapped[UUID] = mapped_column(nullable=False)
    workflow_version_id: Mapped[UUID] = mapped_column(nullable=False)
    node_key: Mapped[UUID] = mapped_column(default=uuid4, nullable=False)
    node_type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    configuration: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    position_x: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    position_y: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class AutomationEdge(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "automation_edges"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workflow_version_id", "business_id", "workflow_id"],
            ["automation_workflow_versions.id", "automation_workflow_versions.business_id", "automation_workflow_versions.workflow_id"],
            name="fk_automation_edges_version_business_workflow",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workflow_version_id", "source_node_key", "business_id"],
            ["automation_nodes.workflow_version_id", "automation_nodes.node_key", "automation_nodes.business_id"],
            name="fk_automation_edges_source_node",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workflow_version_id", "target_node_key", "business_id"],
            ["automation_nodes.workflow_version_id", "automation_nodes.node_key", "automation_nodes.business_id"],
            name="fk_automation_edges_target_node",
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "business_id", "workflow_version_id", name="uq_automation_edges_id_business_version"),
        UniqueConstraint("workflow_version_id", "source_node_key", "branch_label", name="uq_automation_edges_source_branch"),
        CheckConstraint("source_node_key <> target_node_key", name="no_self_edge"),
        CheckConstraint("branch_label IS NULL OR char_length(btrim(branch_label)) BETWEEN 1 AND 64", name="valid_branch_label"),
        CheckConstraint("order_index BETWEEN 0 AND 10000", name="valid_order"),
        Index("ix_automation_edges_business_version_source", "business_id", "workflow_version_id", "source_node_key", "order_index"),
    )

    business_id: Mapped[UUID] = mapped_column(nullable=False)
    workflow_id: Mapped[UUID] = mapped_column(nullable=False)
    workflow_version_id: Mapped[UUID] = mapped_column(nullable=False)
    edge_key: Mapped[UUID] = mapped_column(default=uuid4, nullable=False)
    source_node_key: Mapped[UUID] = mapped_column(nullable=False)
    target_node_key: Mapped[UUID] = mapped_column(nullable=False)
    branch_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class AutomationEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "automation_events"
    __table_args__ = (
        UniqueConstraint("id", "business_id", name="uq_automation_events_id_business"),
        CheckConstraint("char_length(btrim(event_type)) BETWEEN 1 AND 64", name="valid_event_type"),
        CheckConstraint("char_length(btrim(entity_type)) BETWEEN 1 AND 64", name="valid_entity_type"),
        CheckConstraint("status IN ('pending','processing','processed','failed')", name="valid_status"),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="valid_payload"),
        CheckConstraint("failure_code IS NULL OR char_length(btrim(failure_code)) BETWEEN 1 AND 64", name="valid_failure_code"),
        Index("ix_automation_events_business_status_occurred", "business_id", "status", "occurred_at", "id"),
        Index("ix_automation_events_business_type_occurred", "business_id", "event_type", "occurred_at", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[UUID | None] = mapped_column(nullable=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", server_default="pending")
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AutomationWorkflowRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "automation_workflow_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workflow_id", "business_id"], ["automation_workflows.id", "automation_workflows.business_id"],
            name="fk_automation_runs_workflow_business", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workflow_version_id", "business_id", "workflow_id"],
            ["automation_workflow_versions.id", "automation_workflow_versions.business_id", "automation_workflow_versions.workflow_id"],
            name="fk_automation_runs_version_business_workflow",
        ),
        ForeignKeyConstraint(
            ["trigger_event_id", "business_id"], ["automation_events.id", "automation_events.business_id"],
            name="fk_automation_runs_event_business",
        ),
        UniqueConstraint("id", "business_id", "workflow_version_id", name="uq_automation_runs_id_business_version"),
        UniqueConstraint("workflow_version_id", "trigger_event_id", name="uq_automation_runs_version_event"),
        UniqueConstraint("business_id", "idempotency_key", name="uq_automation_runs_business_idempotency"),
        CheckConstraint("status IN ('queued','running','waiting','succeeded','failed','canceled')", name="valid_status"),
        CheckConstraint("trigger_type IN ('event','schedule','manual')", name="valid_trigger_type"),
        CheckConstraint("jsonb_typeof(context_payload) = 'object'", name="valid_context_payload"),
        CheckConstraint("failure_code IS NULL OR char_length(btrim(failure_code)) BETWEEN 1 AND 64", name="valid_failure_code"),
        CheckConstraint("idempotency_key IS NULL OR char_length(btrim(idempotency_key)) BETWEEN 1 AND 128", name="valid_idempotency_key"),
        CheckConstraint("completed_at IS NULL OR started_at IS NOT NULL", name="completion_requires_start"),
        CheckConstraint("completed_at IS NULL OR completed_at >= started_at", name="valid_completion"),
        Index("ix_automation_runs_business_workflow_created", "business_id", "workflow_id", "created_at", "id"),
        Index("ix_automation_runs_business_status_created", "business_id", "status", "created_at", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(nullable=False)
    workflow_id: Mapped[UUID] = mapped_column(nullable=False)
    workflow_version_id: Mapped[UUID] = mapped_column(nullable=False)
    trigger_event_id: Mapped[UUID | None] = mapped_column(nullable=True)
    trigger_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued", server_default="queued")
    context_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    current_node_key: Mapped[UUID | None] = mapped_column(nullable=True)
    waiting_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class AutomationNodeRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "automation_node_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workflow_run_id", "business_id", "workflow_version_id"],
            ["automation_workflow_runs.id", "automation_workflow_runs.business_id", "automation_workflow_runs.workflow_version_id"],
            name="fk_automation_node_runs_run_business_version",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workflow_version_id", "node_key", "business_id"],
            ["automation_nodes.workflow_version_id", "automation_nodes.node_key", "automation_nodes.business_id"],
            name="fk_automation_node_runs_node",
        ),
        ForeignKeyConstraint(
            ["action_id", "business_id"], ["ai_actions.id", "ai_actions.business_id"],
            name="fk_automation_node_runs_action_business",
        ),
        UniqueConstraint("id", "business_id", name="uq_automation_node_runs_id_business"),
        UniqueConstraint("workflow_run_id", "node_key", "attempt", name="uq_automation_node_runs_node_attempt"),
        CheckConstraint("status IN ('running','succeeded','waiting','failed','skipped','canceled')", name="valid_status"),
        CheckConstraint("attempt BETWEEN 1 AND 3", name="valid_attempt"),
        CheckConstraint("branch_outcome IS NULL OR char_length(btrim(branch_outcome)) BETWEEN 1 AND 64", name="valid_branch_outcome"),
        CheckConstraint("result_summary IS NULL OR char_length(result_summary) <= 2000", name="valid_result_summary"),
        CheckConstraint("failure_code IS NULL OR char_length(btrim(failure_code)) BETWEEN 1 AND 64", name="valid_failure_code"),
        CheckConstraint("completed_at IS NULL OR completed_at >= started_at", name="valid_completion"),
        Index("ix_automation_node_runs_business_run_created", "business_id", "workflow_run_id", "created_at", "id"),
        Index("ix_automation_node_runs_business_status_resume", "business_id", "status", "resume_at", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(nullable=False)
    workflow_version_id: Mapped[UUID] = mapped_column(nullable=False)
    workflow_run_id: Mapped[UUID] = mapped_column(nullable=False)
    node_key: Mapped[UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    branch_outcome: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resume_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    action_id: Mapped[UUID | None] = mapped_column(nullable=True)
