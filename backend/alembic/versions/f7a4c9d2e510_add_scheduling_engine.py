"""add multi-tenant scheduling engine

Revision ID: f7a4c9d2e510
Revises: e6f9b2c8d041
Create Date: 2026-08-23 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "f7a4c9d2e510"
down_revision: str | Sequence[str] | None = "e6f9b2c8d041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> tuple[sa.Column, sa.Column, sa.Column]:
    return (
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.create_table(
        "service_providers",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("provider_type", sa.String(32), nullable=False),
        sa.Column("title", sa.String(120), nullable=True),
        sa.Column("specialty", sa.String(160), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("location_reference", sa.String(100), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "char_length(btrim(display_name)) BETWEEN 1 AND 160",
            name=op.f("ck_service_providers_valid_display_name"),
        ),
        sa.CheckConstraint(
            "provider_type ~ '^[a-z][a-z0-9_]{0,31}$'",
            name=op.f("ck_service_providers_valid_provider_type"),
        ),
        sa.CheckConstraint(
            "title IS NULL OR char_length(btrim(title)) BETWEEN 1 AND 120",
            name=op.f("ck_service_providers_valid_title"),
        ),
        sa.CheckConstraint(
            "specialty IS NULL OR char_length(btrim(specialty)) BETWEEN 1 AND 160",
            name=op.f("ck_service_providers_valid_specialty"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(timezone)) BETWEEN 1 AND 64",
            name=op.f("ck_service_providers_valid_timezone"),
        ),
        sa.CheckConstraint(
            "location_reference IS NULL OR "
            "char_length(btrim(location_reference)) BETWEEN 1 AND 100",
            name=op.f("ck_service_providers_valid_location_reference"),
        ),
        sa.ForeignKeyConstraint(
            ["business_id"], ["businesses.id"], ondelete="CASCADE",
            name=op.f("fk_service_providers_business_id_businesses"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_service_providers")),
        sa.UniqueConstraint("id", "business_id", name="uq_service_providers_id_business"),
    )
    op.create_index(
        "ix_service_providers_business_active",
        "service_providers",
        ["business_id", "active", "id"],
    )
    op.create_index(
        "ix_service_providers_business_type",
        "service_providers",
        ["business_id", "provider_type", "id"],
    )

    op.create_table(
        "appointment_types",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("buffer_before_minutes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("buffer_after_minutes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("slot_interval_minutes", sa.Integer(), server_default="15", nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("minimum_notice_minutes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("maximum_future_days", sa.Integer(), server_default="365", nullable=False),
        sa.Column("allow_same_day", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("cancellation_cutoff_minutes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reschedule_cutoff_minutes", sa.Integer(), server_default="0", nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 160",
            name=op.f("ck_appointment_types_valid_name"),
        ),
        sa.CheckConstraint(
            "description IS NULL OR char_length(description) <= 2000",
            name=op.f("ck_appointment_types_valid_description"),
        ),
        sa.CheckConstraint(
            "duration_minutes BETWEEN 5 AND 1440",
            name=op.f("ck_appointment_types_valid_duration"),
        ),
        sa.CheckConstraint(
            "buffer_before_minutes BETWEEN 0 AND 720",
            name=op.f("ck_appointment_types_valid_buffer_before"),
        ),
        sa.CheckConstraint(
            "buffer_after_minutes BETWEEN 0 AND 720",
            name=op.f("ck_appointment_types_valid_buffer_after"),
        ),
        sa.CheckConstraint(
            "slot_interval_minutes BETWEEN 5 AND 1440",
            name=op.f("ck_appointment_types_valid_slot_interval"),
        ),
        sa.CheckConstraint(
            "minimum_notice_minutes BETWEEN 0 AND 525600",
            name=op.f("ck_appointment_types_valid_minimum_notice"),
        ),
        sa.CheckConstraint(
            "maximum_future_days BETWEEN 1 AND 730",
            name=op.f("ck_appointment_types_valid_future_horizon"),
        ),
        sa.CheckConstraint(
            "cancellation_cutoff_minutes BETWEEN 0 AND 525600",
            name=op.f("ck_appointment_types_valid_cancellation_cutoff"),
        ),
        sa.CheckConstraint(
            "reschedule_cutoff_minutes BETWEEN 0 AND 525600",
            name=op.f("ck_appointment_types_valid_reschedule_cutoff"),
        ),
        sa.ForeignKeyConstraint(
            ["business_id"], ["businesses.id"], ondelete="CASCADE",
            name=op.f("fk_appointment_types_business_id_businesses"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_appointment_types")),
        sa.UniqueConstraint("id", "business_id", name="uq_appointment_types_id_business"),
    )
    op.create_index(
        "ix_appointment_types_business_active",
        "appointment_types",
        ["business_id", "active", "id"],
    )

    op.create_table(
        "customers",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "char_length(btrim(display_name)) BETWEEN 1 AND 160",
            name=op.f("ck_customers_valid_display_name"),
        ),
        sa.ForeignKeyConstraint(
            ["business_id"], ["businesses.id"], ondelete="CASCADE",
            name=op.f("fk_customers_business_id_businesses"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customers")),
        sa.UniqueConstraint("id", "business_id", name="uq_customers_id_business"),
    )
    op.create_index(
        "ix_customers_business_active", "customers", ["business_id", "active", "id"]
    )

    op.create_table(
        "provider_appointment_types",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("appointment_type_id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["provider_id", "business_id"],
            ["service_providers.id", "service_providers.business_id"],
            name="fk_provider_appointment_types_provider_business",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["appointment_type_id", "business_id"],
            ["appointment_types.id", "appointment_types.business_id"],
            name="fk_provider_appointment_types_type_business",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_appointment_types")),
        sa.UniqueConstraint(
            "business_id", "provider_id", "appointment_type_id",
            name="uq_provider_appointment_types_assignment",
        ),
    )
    op.create_index(
        "ix_provider_appointment_types_business_type",
        "provider_appointment_types",
        ["business_id", "appointment_type_id", "provider_id"],
    )

    op.create_table(
        "provider_availability_rules",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("start_local_time", sa.Time(timezone=False), nullable=False),
        sa.Column("end_local_time", sa.Time(timezone=False), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name=op.f("ck_provider_availability_rules_valid_weekday")),
        sa.CheckConstraint("start_local_time < end_local_time", name=op.f("ck_provider_availability_rules_valid_time_window")),
        sa.CheckConstraint(
            "valid_until IS NULL OR valid_from IS NULL OR valid_until >= valid_from",
            name=op.f("ck_provider_availability_rules_valid_date_range"),
        ),
        sa.ForeignKeyConstraint(
            ["provider_id", "business_id"],
            ["service_providers.id", "service_providers.business_id"],
            name="fk_provider_availability_rules_provider_business",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_availability_rules")),
    )
    op.create_index(
        "ix_provider_availability_rules_business_provider_weekday",
        "provider_availability_rules",
        ["business_id", "provider_id", "weekday", "active"],
    )

    op.create_table(
        "provider_availability_exceptions",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("exception_date", sa.Date(), nullable=False),
        sa.Column("exception_kind", sa.String(32), nullable=False),
        sa.Column("whole_day", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("start_local_time", sa.Time(timezone=False), nullable=True),
        sa.Column("end_local_time", sa.Time(timezone=False), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "exception_kind IN ('unavailable', 'available_override')",
            name=op.f("ck_provider_availability_exceptions_valid_exception_kind"),
        ),
        sa.CheckConstraint(
            "(whole_day AND start_local_time IS NULL AND end_local_time IS NULL) OR "
            "(NOT whole_day AND start_local_time IS NOT NULL AND end_local_time IS NOT NULL "
            "AND start_local_time < end_local_time)",
            name=op.f("ck_provider_availability_exceptions_valid_exception_window"),
        ),
        sa.ForeignKeyConstraint(
            ["provider_id", "business_id"],
            ["service_providers.id", "service_providers.business_id"],
            name="fk_provider_availability_exceptions_provider_business",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_availability_exceptions")),
    )
    op.create_index(
        "ix_provider_availability_exceptions_business_provider_date",
        "provider_availability_exceptions",
        ["business_id", "provider_id", "exception_date", "active"],
    )

    op.create_table(
        "appointments",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("appointment_type_id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(24), server_default="confirmed", nullable=False),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("cancellation_reason_code", sa.String(64), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("ends_at > starts_at", name=op.f("ck_appointments_valid_time_range")),
        sa.CheckConstraint(
            "status IN ('confirmed', 'canceled', 'completed', 'no_show')",
            name=op.f("ck_appointments_valid_status"),
        ),
        sa.CheckConstraint(
            "source IN ('manual', 'api', 'ai', 'website', 'whatsapp', 'import')",
            name=op.f("ck_appointments_valid_source"),
        ),
        sa.CheckConstraint(
            "(status = 'canceled' AND cancellation_reason_code IS NOT NULL) OR "
            "(status <> 'canceled' AND cancellation_reason_code IS NULL)",
            name=op.f("ck_appointments_consistent_cancellation"),
        ),
        sa.CheckConstraint(
            "cancellation_reason_code IS NULL OR "
            "cancellation_reason_code ~ '^[a-z][a-z0-9_]{0,63}$'",
            name=op.f("ck_appointments_valid_cancellation_reason_code"),
        ),
        sa.ForeignKeyConstraint(
            ["business_id"], ["businesses.id"], ondelete="CASCADE",
            name=op.f("fk_appointments_business_id_businesses"),
        ),
        sa.ForeignKeyConstraint(
            ["provider_id", "business_id"],
            ["service_providers.id", "service_providers.business_id"],
            name="fk_appointments_provider_business",
        ),
        sa.ForeignKeyConstraint(
            ["appointment_type_id", "business_id"],
            ["appointment_types.id", "appointment_types.business_id"],
            name="fk_appointments_type_business",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id", "business_id"],
            ["customers.id", "customers.business_id"],
            name="fk_appointments_customer_business",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL",
            name=op.f("fk_appointments_created_by_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_appointments")),
        sa.UniqueConstraint("id", "business_id", name="uq_appointments_id_business"),
    )
    op.execute(
        "ALTER TABLE appointments ADD CONSTRAINT "
        "ex_appointments_provider_time_overlap EXCLUDE USING gist "
        "(provider_id WITH =, tstzrange(starts_at, ends_at, '[)') WITH &&) "
        "WHERE (status = 'confirmed')"
    )
    op.create_index(
        "ix_appointments_business_provider_start",
        "appointments", ["business_id", "provider_id", "starts_at", "id"],
    )
    op.create_index(
        "ix_appointments_business_customer_start",
        "appointments", ["business_id", "customer_id", "starts_at", "id"],
    )
    op.create_index(
        "ix_appointments_business_status_start",
        "appointments", ["business_id", "status", "starts_at", "id"],
    )


def downgrade() -> None:
    op.drop_table("appointments")
    op.drop_table("provider_availability_exceptions")
    op.drop_table("provider_availability_rules")
    op.drop_table("provider_appointment_types")
    op.drop_table("customers")
    op.drop_table("appointment_types")
    op.drop_table("service_providers")
    # btree_gist may be shared by other application objects; do not drop it.
