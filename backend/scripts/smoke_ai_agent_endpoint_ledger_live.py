from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from sqlalchemy import delete, func, select

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings
from app.core.security import create_access_token
from app.db.session import AsyncSessionFactory
from app.main import app
from app.models.ai_action import AIAction
from app.models.ai_agent_execution import AIAgentExecution
from app.models.approval_request import ApprovalRequest
from app.models.business import Business
from app.models.business_membership import BusinessMembership
from app.models.user import User


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
                "No active user/business membership is available."
            )

        user_id, business_id = row

        return user_id, business_id


async def main() -> None:
    user_id, business_id = await find_active_identity()

    smoke_id = uuid4().hex

    execution_id: UUID | None = None

    task = (
        "Live AI governed-action smoke test "
        f"{smoke_id}. "
        "Using only trusted business context, provide one short factual "
        "summary of the business and propose exactly one non-executing "
        "customer follow-up action. "
        "The proposed action must use action_type "
        "'send_customer_message', risk_level 'medium', and "
        "requires_approval true. "
        "Do not send any message and do not perform any external action."
    )

    access_token = create_access_token(
        user_id
    )

    url = (
        f"/api/v1/businesses/"
        f"{business_id}/agents/execute"
    )

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=app,
            ),
            base_url="http://testserver",
        ) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": (
                        f"Bearer {access_token}"
                    ),
                },
                json={
                    "role": "business_manager",
                    "task": task,
                    "include_business_brain": True,
                    "include_memory": True,
                },
            )

        print(
            "HTTP STATUS:",
            response.status_code,
        )

        print(
            "CACHE CONTROL:",
            response.headers.get(
                "Cache-Control"
            ),
        )

        if response.status_code != 200:
            print(
                "SAFE RESPONSE:",
                response.text,
            )

            async with AsyncSessionFactory() as failure_session:
                failed_execution = await failure_session.scalar(
                    select(
                        AIAgentExecution
                    )
                    .where(
                        AIAgentExecution.business_id
                        == business_id,
                        AIAgentExecution.task
                        == task,
                    )
                    .order_by(
                        AIAgentExecution.created_at.desc(),
                    )
                    .limit(1)
                )

                if failed_execution is None:
                    print(
                        "FAILURE LEDGER FOUND:",
                        False,
                    )
                else:
                    execution_id = failed_execution.id

                    print(
                        "FAILURE LEDGER FOUND:",
                        True,
                    )

                    print(
                        "FAILURE LEDGER STATUS:",
                        failed_execution.status,
                    )

                    print(
                        "FAILURE CODE:",
                        failed_execution.failure_code,
                    )

                    failed_action_count = await failure_session.scalar(
                        select(
                            func.count(
                                AIAction.id
                            )
                        ).where(
                            AIAction.execution_id
                            == failed_execution.id
                        )
                    )

                    failed_approval_count = await failure_session.scalar(
                        select(
                            func.count(
                                ApprovalRequest.id
                            )
                        )
                        .select_from(
                            ApprovalRequest
                        )
                        .join(
                            AIAction,
                            AIAction.id
                            == ApprovalRequest.action_id,
                        )
                        .where(
                            AIAction.execution_id
                            == failed_execution.id
                        )
                    )

                    print(
                        "FAILURE ACTION COUNT:",
                        failed_action_count,
                    )

                    print(
                        "FAILURE APPROVAL COUNT:",
                        failed_approval_count,
                    )

            raise RuntimeError(
                "Live AI endpoint request failed."
            )

        payload = response.json()

        print(
            "API ROLE:",
            payload["role"],
        )

        print(
            "API STATUS:",
            payload["output"]["status"],
        )

        print(
            "API CONTEXT SOURCES:",
            payload["context_source_count"],
        )

        print(
            "API SUMMARY:",
            payload["output"]["summary"],
        )

        response_text = response.text.lower()

        forbidden_response_fields = (
            "provider_request_id",
            "input_tokens",
            "output_tokens",
            "provider_metadata",
            "api_key",
            "authorization",
            "system_instructions",
        )

        metadata_hidden = all(
            field not in response_text
            for field in forbidden_response_fields
        )

        print(
            "PROVIDER METADATA HIDDEN FROM API:",
            metadata_hidden,
        )

        async with AsyncSessionFactory() as session:
            statement = (
                select(
                    AIAgentExecution
                )
                .where(
                    AIAgentExecution.business_id
                    == business_id,
                    AIAgentExecution.task
                    == task,
                )
                .order_by(
                    AIAgentExecution.created_at.desc(),
                )
                .limit(1)
            )

            execution = (
                await session.execute(statement)
            ).scalar_one_or_none()

            if execution is None:
                raise RuntimeError(
                    "Execution ledger row was not persisted."
                )

            execution_id = execution.id

            print(
                "LEDGER FOUND:",
                True,
            )

            print(
                "LEDGER BUSINESS MATCH:",
                execution.business_id
                == business_id,
            )

            print(
                "LEDGER REQUESTER MATCH:",
                execution.requested_by_user_id
                == user_id,
            )

            print(
                "LEDGER ROLE:",
                execution.role,
            )

            print(
                "LEDGER TRIGGER:",
                execution.trigger_type,
            )

            print(
                "LEDGER STATUS:",
                execution.status,
            )

            print(
                "LEDGER PROVIDER:",
                execution.provider_name,
            )

            print(
                "LEDGER MODEL:",
                execution.model_name,
            )

            print(
                "MODEL MATCH:",
                execution.model_name
                == settings.openai_model,
            )

            print(
                "CONTEXT REVISION STORED:",
                execution.context_revision
                == payload["context_revision"],
            )

            print(
                "CONTEXT COUNT MATCH:",
                execution.context_source_count
                == payload["context_source_count"],
            )

            print(
                "BRAIN COUNT MATCH:",
                execution.business_brain_source_count
                == payload[
                    "business_brain_source_count"
                ],
            )

            print(
                "MEMORY COUNT MATCH:",
                execution.memory_source_count
                == payload["memory_source_count"],
            )

            print(
                "SUMMARY MATCH:",
                execution.output_summary
                == payload["output"]["summary"],
            )

            print(
                "TERMINAL COMPLETED AT:",
                execution.completed_at is not None,
            )

            print(
                "SAFE FAILURE FIELD:",
                execution.failure_code is None,
            )

            print(
                "DURATION RECORDED:",
                (
                    execution.duration_ms is not None
                    and execution.duration_ms >= 0
                ),
            )

            print(
                "PROVIDER REQUEST ID RECORDED:",
                (
                    execution.provider_request_id
                    is not None
                    and bool(
                        execution.provider_request_id.strip()
                    )
                ),
            )

            print(
                "INPUT TOKENS RECORDED:",
                (
                    execution.input_tokens is not None
                    and execution.input_tokens > 0
                ),
            )

            print(
                "OUTPUT TOKENS RECORDED:",
                (
                    execution.output_tokens is not None
                    and execution.output_tokens > 0
                ),
            )

            print(
                "INPUT TOKENS:",
                execution.input_tokens,
            )

            print(
                "OUTPUT TOKENS:",
                execution.output_tokens,
            )

            print(
                "ESTIMATED COST NOT YET CALCULATED:",
                execution.estimated_cost_usd is None,
            )

            expected_status = (
                payload["output"]["status"]
            )

            if execution.status != expected_status:
                raise RuntimeError(
                    "Ledger status does not match API result."
                )

            if execution.provider_name != "openai":
                raise RuntimeError(
                    "Ledger provider metadata is incorrect."
                )

            if execution.model_name != settings.openai_model:
                raise RuntimeError(
                    "Ledger model metadata is incorrect."
                )

            if execution.completed_at is None:
                raise RuntimeError(
                    "Ledger execution did not reach terminal state."
                )

            if not metadata_hidden:
                raise RuntimeError(
                    "Internal provider metadata leaked into API response."
                )

            if (
                execution.provider_request_id is None
                or not execution.provider_request_id.strip()
            ):
                raise RuntimeError(
                    "OpenAI request ID was not persisted."
                )

            if (
                execution.input_tokens is None
                or execution.input_tokens <= 0
            ):
                raise RuntimeError(
                    "OpenAI input token usage was not persisted."
                )

            if (
                execution.output_tokens is None
                or execution.output_tokens <= 0
            ):
                raise RuntimeError(
                    "OpenAI output token usage was not persisted."
                )

            if execution.estimated_cost_usd is not None:
                raise RuntimeError(
                    "Estimated cost must remain unset until "
                    "the pricing engine is implemented."
                )

            print(
                "LIVE OPENAI METADATA → LEDGER OK"
            )

            api_actions = payload[
                "output"
            ]["proposed_actions"]

            actions = list(
                (
                    await session.scalars(
                        select(
                            AIAction
                        )
                        .where(
                            AIAction.business_id
                            == business_id,
                            AIAction.execution_id
                            == execution.id,
                        )
                        .order_by(
                            AIAction.proposal_index.asc(),
                            AIAction.id.asc(),
                        )
                    )
                ).all()
            )

            print(
                "API PROPOSED ACTION COUNT:",
                len(api_actions),
            )

            print(
                "DATABASE ACTION COUNT:",
                len(actions),
            )

            if len(api_actions) != 1:
                raise RuntimeError(
                    "Expected exactly one proposed API action."
                )

            if len(actions) != 1:
                raise RuntimeError(
                    "Expected exactly one governed AIAction row."
                )

            api_action = api_actions[0]
            action = actions[0]

            print(
                "API REQUIRES APPROVAL:",
                api_action[
                    "requires_approval"
                ],
            )

            print(
                "API STATUS NEEDS APPROVAL:",
                payload["output"]["status"]
                == "needs_approval",
            )

            print(
                "ACTION BUSINESS MATCH:",
                action.business_id
                == business_id,
            )

            print(
                "ACTION EXECUTION MATCH:",
                action.execution_id
                == execution.id,
            )

            print(
                "ACTION PROPOSAL INDEX:",
                action.proposal_index,
            )

            print(
                "ACTION TYPE:",
                action.action_type,
            )

            print(
                "ACTION TYPE MATCHES API:",
                action.action_type
                == api_action["action_type"],
            )

            print(
                "ACTION DESCRIPTION MATCHES API:",
                action.description
                == api_action["description"],
            )

            print(
                "ACTION RISK MATCHES API:",
                action.risk_level
                == api_action["risk_level"],
            )

            print(
                "ACTION APPROVAL FLAG MATCHES API:",
                action.proposed_requires_approval
                == api_action[
                    "requires_approval"
                ],
            )

            approval = await session.scalar(
                select(
                    ApprovalRequest
                ).where(
                    ApprovalRequest.business_id
                    == business_id,
                    ApprovalRequest.action_id
                    == action.id,
                    ApprovalRequest.status
                    == "pending",
                )
            )

            print(
                "ACTION STATUS:",
                action.status,
            )

            print(
                "ACTION PAYLOAD MATCHES API:",
                action.action_payload
                == api_action["action_payload"],
            )

            print(
                "POLICY DECISION:",
                action.policy_decision,
            )

            print(
                "POLICY REASON:",
                action.policy_reason_code,
            )

            print(
                "POLICY EVALUATED:",
                action.policy_evaluated_at
                is not None,
            )

            print(
                "APPROVAL FOUND:",
                approval is not None,
            )

            if approval is not None:
                print(
                    "APPROVAL STATUS:",
                    approval.status,
                )

                print(
                    "APPROVAL BUSINESS MATCH:",
                    approval.business_id
                    == business_id,
                )

                print(
                    "APPROVAL ACTION MATCH:",
                    approval.action_id
                    == action.id,
                )

                print(
                    "APPROVAL REQUESTER MATCH:",
                    approval.requested_by_user_id
                    == user_id,
                )

                print(
                    "APPROVAL REASON MATCH:",
                    approval.reason_code
                    == action.policy_reason_code,
                )

            print(
                "NO EXECUTION STARTED:",
                action.execution_started_at is None,
            )

            print(
                "NO EXECUTION COMPLETED:",
                action.execution_completed_at is None,
            )

            print(
                "NO EXTERNAL REFERENCE:",
                action.external_reference_id is None,
            )

            print(
                "NO ACTION FAILURE:",
                action.failure_code is None,
            )

            if (
                payload["output"]["status"]
                != "needs_approval"
            ):
                raise RuntimeError(
                    "Approval-required proposal did not produce "
                    "needs_approval status."
                )

            if not api_action[
                "requires_approval"
            ]:
                raise RuntimeError(
                    "Live proposal unexpectedly bypassed approval."
                )

            if (
                action.business_id
                != business_id
            ):
                raise RuntimeError(
                    "Governed action business ownership is incorrect."
                )

            if (
                action.execution_id
                != execution.id
            ):
                raise RuntimeError(
                    "Governed action execution linkage is incorrect."
                )

            if action.proposal_index != 0:
                raise RuntimeError(
                    "Governed action proposal index is incorrect."
                )

            if (
                action.action_type
                != api_action["action_type"]
            ):
                raise RuntimeError(
                    "Governed action type does not match API proposal."
                )

            if (
                action.description
                != api_action["description"]
            ):
                raise RuntimeError(
                    "Governed action description does not match API."
                )

            if (
                action.risk_level
                != api_action["risk_level"]
            ):
                raise RuntimeError(
                    "Governed action risk does not match API."
                )

            if (
                action.proposed_requires_approval
                != api_action[
                    "requires_approval"
                ]
            ):
                raise RuntimeError(
                    "Governed action approval flag does not match API."
                )

            if (
                action.action_payload
                != api_action["action_payload"]
            ):
                raise RuntimeError(
                    "Governed action payload does not match "
                    "the validated API proposal."
                )

            if (
                action.status
                != "pending_approval"
            ):
                raise RuntimeError(
                    "Governed action did not enter pending approval."
                )

            if (
                action.policy_decision
                != "require_approval"
            ):
                raise RuntimeError(
                    "Server policy did not require approval."
                )

            if (
                action.policy_reason_code
                != "external_communication"
            ):
                raise RuntimeError(
                    "Unexpected server policy reason."
                )

            if (
                action.policy_evaluated_at
                is None
            ):
                raise RuntimeError(
                    "Policy evaluation timestamp was not persisted."
                )

            if approval is None:
                raise RuntimeError(
                    "Pending ApprovalRequest was not persisted."
                )

            if approval.status != "pending":
                raise RuntimeError(
                    "ApprovalRequest is not pending."
                )

            if (
                approval.business_id
                != business_id
            ):
                raise RuntimeError(
                    "ApprovalRequest business ownership is incorrect."
                )

            if (
                approval.action_id
                != action.id
            ):
                raise RuntimeError(
                    "ApprovalRequest action linkage is incorrect."
                )

            if (
                approval.requested_by_user_id
                != user_id
            ):
                raise RuntimeError(
                    "Approval requester does not match "
                    "authenticated user."
                )

            if (
                approval.reason_code
                != action.policy_reason_code
            ):
                raise RuntimeError(
                    "Approval reason does not match server policy."
                )

            if action.execution_started_at is not None:
                raise RuntimeError(
                    "Governed action unexpectedly started execution."
                )

            if action.execution_completed_at is not None:
                raise RuntimeError(
                    "Governed action unexpectedly completed execution."
                )

            if action.external_reference_id is not None:
                raise RuntimeError(
                    "Governed action unexpectedly has an "
                    "external reference."
                )

            if action.failure_code is not None:
                raise RuntimeError(
                    "Pending governed action unexpectedly has "
                    "a failure code."
                )

            print(
                "LIVE OPENAI → POLICY → APPROVAL OK"
            )

    finally:
        async with AsyncSessionFactory() as session:
            result = await session.execute(
                delete(
                    AIAgentExecution
                ).where(
                    AIAgentExecution.task
                    == task,
                    AIAgentExecution.business_id
                    == business_id,
                )
            )

            await session.commit()

            print(
                "SMOKE EXECUTION CLEANUP:",
                result.rowcount == 1,
            )

            if execution_id is not None:
                remaining_actions = (
                    await session.scalar(
                        select(
                            func.count(
                                AIAction.id
                            )
                        ).where(
                            AIAction.execution_id
                            == execution_id
                        )
                    )
                )

                remaining_approvals = (
                    await session.scalar(
                        select(
                            func.count(
                                ApprovalRequest.id
                            )
                        )
                        .select_from(
                            ApprovalRequest
                        )
                        .join(
                            AIAction,
                            AIAction.id
                            == ApprovalRequest.action_id,
                        )
                        .where(
                            AIAction.execution_id
                            == execution_id
                        )
                    )
                )

                print(
                    "SMOKE ACTION CASCADE CLEANUP:",
                    remaining_actions == 0,
                )

                print(
                    "SMOKE APPROVAL CASCADE CLEANUP:",
                    remaining_approvals == 0,
                )

                if remaining_actions != 0:
                    raise RuntimeError(
                        "Smoke AIAction rows were not cleaned up."
                    )

                if remaining_approvals != 0:
                    raise RuntimeError(
                        "Smoke ApprovalRequest rows were not cleaned up."
                    )


asyncio.run(main())
