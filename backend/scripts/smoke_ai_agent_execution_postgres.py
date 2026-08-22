from __future__ import annotations

import asyncio
import sys
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db.session import AsyncSessionFactory
from app.models.ai_agent_execution import AIAgentExecution
from app.models.business import Business
from app.models.business_membership import BusinessMembership
from app.models.user import User
from app.schemas.ai_agent import (
    AIAgentExecutionResult,
    AIAgentProposedAction,
    AIAgentStructuredOutput,
)
from app.services.ai_agent_execution import (
    create_running_ai_agent_execution,
    fail_ai_agent_execution,
    finalize_successful_ai_agent_execution,
    get_ai_agent_execution,
)


CONTEXT_REVISION = "b" * 64


async def find_active_identity() -> tuple[UUID, UUID]:
    async with AsyncSessionFactory() as session:
        statement = (
            select(
                User.id,
                Business.id,
            )
            .join(
                BusinessMembership,
                BusinessMembership.user_id == User.id,
            )
            .join(
                Business,
                Business.id == BusinessMembership.business_id,
            )
            .where(
                User.status == "active",
                Business.status == "active",
                BusinessMembership.status == "active",
            )
            .order_by(
                Business.created_at.asc(),
                Business.id.asc(),
            )
            .limit(1)
        )

        row = (
            await session.execute(statement)
        ).one_or_none()

        if row is None:
            raise RuntimeError(
                "No active user/business membership is available for smoke testing"
            )

        user_id, business_id = row

        return user_id, business_id


async def main() -> None:
    user_id, business_id = await find_active_identity()

    async with AsyncSessionFactory() as session:
        transaction = await session.begin()

        try:
            completed_execution = (
                await create_running_ai_agent_execution(
                    session,
                    business_id=business_id,
                    requested_by_user_id=user_id,
                    role="sales",
                    task="PostgreSQL execution ledger smoke test.",
                    provider_name="openai",
                    model_name="gpt-5.6-terra",
                )
            )

            print(
                "RUNNING CREATED:",
                completed_execution.status == "running",
            )

            print(
                "INITIAL CONTEXT REVISION NULL:",
                completed_execution.context_revision is None,
            )

            loaded = await get_ai_agent_execution(
                session,
                business_id=business_id,
                execution_id=completed_execution.id,
            )

            print(
                "TENANT READ:",
                loaded.id == completed_execution.id,
            )

            result = AIAgentExecutionResult(
                business_id=business_id,
                role="sales",
                context_revision=CONTEXT_REVISION,
                context_source_count=3,
                business_brain_source_count=2,
                memory_source_count=1,
                output=AIAgentStructuredOutput(
                    status="needs_approval",
                    summary="Customer follow-up is recommended.",
                    recommendations=[
                        "Prepare the recurring-plan offer.",
                    ],
                    proposed_actions=[
                        AIAgentProposedAction(
                            action_type="send_customer_message",
                            description=(
                                "Send the prepared recurring-plan offer."
                            ),
                            risk_level="medium",
                            requires_approval=True,
                        ),
                    ],
                ),
            )

            finalized = (
                await finalize_successful_ai_agent_execution(
                    session,
                    business_id=business_id,
                    execution_id=completed_execution.id,
                    result=result,
                    duration_ms=1250,
                    input_tokens=1800,
                    output_tokens=420,
                    estimated_cost_usd=Decimal(
                        "0.008640"
                    ),
                    provider_request_id="req_smoke_safe",
                )
            )

            print(
                "FINAL STATUS:",
                finalized.status,
            )

            print(
                "CONTEXT REVISION STORED:",
                finalized.context_revision == CONTEXT_REVISION,
            )

            print(
                "CONTEXT COUNTS STORED:",
                (
                    finalized.context_source_count == 3
                    and finalized.business_brain_source_count == 2
                    and finalized.memory_source_count == 1
                ),
            )

            print(
                "APPROVAL ACTION STORED:",
                (
                    len(finalized.proposed_actions) == 1
                    and finalized.proposed_actions[0]["requires_approval"]
                    is True
                ),
            )

            print(
                "USAGE STORED:",
                (
                    finalized.input_tokens == 1800
                    and finalized.output_tokens == 420
                    and finalized.duration_ms == 1250
                ),
            )

            failed_execution = (
                await create_running_ai_agent_execution(
                    session,
                    business_id=business_id,
                    requested_by_user_id=user_id,
                    role="analytics",
                    task="PostgreSQL failure ledger smoke test.",
                    provider_name="openai",
                    model_name="gpt-5.6-terra",
                )
            )

            failed = await fail_ai_agent_execution(
                session,
                business_id=business_id,
                execution_id=failed_execution.id,
                failure_code="provider_unavailable",
                duration_ms=500,
            )

            print(
                "FAILED STATUS:",
                failed.status == "failed",
            )

            print(
                "SAFE FAILURE CODE:",
                failed.failure_code == "provider_unavailable",
            )

            print(
                "FAILED COMPLETED AT:",
                failed.completed_at is not None,
            )

            stored_rows = (
                await session.execute(
                    select(AIAgentExecution)
                    .where(
                        AIAgentExecution.id.in_(
                            [
                                completed_execution.id,
                                failed_execution.id,
                            ]
                        )
                    )
                    .order_by(
                        AIAgentExecution.created_at.asc(),
                        AIAgentExecution.id.asc(),
                    )
                )
            ).scalars().all()

            print(
                "POSTGRES ROW COUNT:",
                len(stored_rows) == 2,
            )

            print(
                "NO SECRET FIELD:",
                not hasattr(
                    completed_execution,
                    "api_key",
                ),
            )

            print(
                "AI AGENT EXECUTION POSTGRESQL SMOKE OK"
            )

        finally:
            await transaction.rollback()

    async with AsyncSessionFactory() as session:
        remaining = (
            await session.execute(
                select(AIAgentExecution)
                .where(
                    AIAgentExecution.task.in_(
                        [
                            "PostgreSQL execution ledger smoke test.",
                            "PostgreSQL failure ledger smoke test.",
                        ]
                    )
                )
            )
        ).scalars().all()

        print(
            "ROLLBACK CLEANUP:",
            len(remaining) == 0,
        )


asyncio.run(main())