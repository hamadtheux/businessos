from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from pydantic import BaseModel, ValidationError

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault(
    "AIBOS_AUTH_SECRET_KEY",
    "x" * 32,
)

from app.agents.context_renderer import (  # noqa: E402
    build_provider_task_message,
    render_ai_context,
)
from app.agents.definitions import (  # noqa: E402
    AI_AGENT_DEFINITIONS,
    build_agent_system_instructions,
    get_agent_definition,
)
from app.agents.provider import (  # noqa: E402
    AIAgentProviderMetadata,
    AIAgentProviderRequest,
    AIAgentProviderResult,
    AIAgentTypedProviderResult,
    validate_agent_provider,
)
from app.agents.runtime import (  # noqa: E402
    execute_ai_agent,
    execute_ai_agent_typed_with_metadata,
    execute_ai_agent_with_metadata,
)
from app.exceptions.ai_agent import (  # noqa: E402
    AIAgentContextError,
    AIAgentProviderError,
    AIAgentResponseError,
    AIAgentValidationError,
)
from app.exceptions.ai_context import AIContextAssemblyError  # noqa: E402
from app.schemas.ai_agent import (  # noqa: E402
    AIAgentExecutionRequest,
    AIAgentExecutionResult,
    AIAgentProposedAction,
    AIAgentStructuredOutput,
)
from app.schemas.ai_context import (  # noqa: E402
    AIContextBundle,
    BusinessBrainContextSource,
    BusinessMemoryContextSource,
)


BUSINESS_A_ID = UUID(
    "71000000-0000-0000-0000-000000000001"
)

BUSINESS_B_ID = UUID(
    "72000000-0000-0000-0000-000000000002"
)

BASE_TIME = datetime(
    2026,
    8,
    21,
    12,
    0,
    tzinfo=UTC,
)


class AgentDefinitionTests(unittest.TestCase):
    def test_all_supported_roles_have_immutable_definitions(
        self,
    ) -> None:
        self.assertEqual(
            set(AI_AGENT_DEFINITIONS),
            {
                "business_manager",
                "cmo",
                "sales",
                "support",
                "operations",
                "analytics",
            },
        )

        self.assertEqual(
            AI_AGENT_DEFINITIONS["business_manager"].context_purpose,
            "business_manager",
        )

        self.assertEqual(
            AI_AGENT_DEFINITIONS["cmo"].context_purpose,
            "marketing",
        )

        self.assertEqual(
            AI_AGENT_DEFINITIONS["sales"].context_purpose,
            "sales",
        )

        self.assertEqual(
            AI_AGENT_DEFINITIONS["support"].context_purpose,
            "support",
        )

        self.assertEqual(
            AI_AGENT_DEFINITIONS["operations"].context_purpose,
            "operations",
        )

        self.assertEqual(
            AI_AGENT_DEFINITIONS["analytics"].context_purpose,
            "analytics",
        )

    def test_unknown_agent_role_is_rejected_safely(self) -> None:
        with self.assertRaises(AIAgentValidationError):
            get_agent_definition(
                "super_admin_agent",  # type: ignore[arg-type]
            )

    def test_system_instructions_include_control_boundaries(
        self,
    ) -> None:
        definition = get_agent_definition(
            "sales",
        )

        instructions = build_agent_system_instructions(
            definition,
        )

        self.assertIn(
            "AI Sales",
            instructions,
        )

        self.assertIn(
            "Use only the trusted business context",
            instructions,
        )

        self.assertIn(
            "Do not reveal hidden reasoning or chain-of-thought",
            instructions,
        )

        self.assertIn(
            "Never claim that a proposed action has already been executed",
            instructions,
        )


class AgentSchemaSecurityTests(unittest.TestCase):
    def test_client_cannot_disable_all_trusted_context(
        self,
    ) -> None:
        with self.assertRaises(ValidationError):
            AIAgentExecutionRequest(
                role="sales",
                task="Do something.",
                include_business_brain=False,
                include_memory=False,
            )

    def test_duplicate_memory_filters_are_rejected(
        self,
    ) -> None:
        with self.assertRaises(ValidationError):
            AIAgentExecutionRequest(
                role="sales",
                task="Prepare context.",
                memory_types=[
                    "customer",
                    "customer",
                ],
            )

    def test_critical_action_cannot_bypass_approval(
        self,
    ) -> None:
        with self.assertRaises(ValidationError):
            AIAgentProposedAction(
                action_type="delete_customer_data",
                description="Delete customer data.",
                risk_level="critical",
                requires_approval=False,
            )

    def test_completed_output_cannot_contain_approval_action(
        self,
    ) -> None:
        with self.assertRaises(ValidationError):
            AIAgentStructuredOutput(
                status="completed",
                summary="Ready.",
                proposed_actions=[
                    AIAgentProposedAction(
                        action_type="send_customer_message",
                        description="Send message.",
                        requires_approval=True,
                    ),
                ],
            )

    def test_needs_approval_requires_real_approval_action(
        self,
    ) -> None:
        with self.assertRaises(ValidationError):
            AIAgentStructuredOutput(
                status="needs_approval",
                summary="Approval needed.",
                proposed_actions=[],
            )


class ProviderContractTests(unittest.TestCase):
    def test_valid_provider_is_accepted(
        self,
    ) -> None:
        provider = _FakeProvider()

        validate_agent_provider(
            provider,
        )

        self.assertEqual(
            provider.provider_name,
            "fake",
        )

    def test_invalid_provider_is_rejected(
        self,
    ) -> None:
        with self.assertRaises(TypeError):
            validate_agent_provider(
                object(),  # type: ignore[arg-type]
            )

    def test_blank_provider_name_is_rejected(
        self,
    ) -> None:
        provider = _BlankNameProvider()

        with self.assertRaises(ValueError):
            validate_agent_provider(
                provider,
            )

    def test_provider_request_rejects_cross_tenant_context(
        self,
    ) -> None:
        context = _context_bundle(
            business_id=BUSINESS_B_ID,
            purpose="sales",
            task="Prepare context.",
        )

        with self.assertRaises(ValueError):
            AIAgentProviderRequest(
                business_id=BUSINESS_A_ID,
                role="sales",
                system_instructions="Controlled sales instructions.",
                task="Perform task.",
                context=context,
            )


class ContextRendererTests(unittest.TestCase):
    def test_renderer_preserves_source_boundaries(
        self,
    ) -> None:
        context = _context_bundle(
            business_id=BUSINESS_A_ID,
            purpose="sales",
            task="Prepare context.",
            include_sources=True,
        )

        rendered = render_ai_context(
            context,
        )

        self.assertIn(
            "Origin: Business Brain",
            rendered,
        )

        self.assertIn(
            "Origin: Persistent Business Memory",
            rendered,
        )

        self.assertIn(
            "Premium Milk",
            rendered,
        )

        self.assertIn(
            "Customer prefers WhatsApp.",
            rendered,
        )

    def test_renderer_does_not_expose_forbidden_internal_fields(
        self,
    ) -> None:
        context = _context_bundle(
            business_id=BUSINESS_A_ID,
            purpose="sales",
            task="Prepare context.",
            include_sources=True,
        )

        rendered = render_ai_context(
            context,
        )

        for forbidden in (
            "source_reference",
            "password",
            "access_token",
            "logo_storage_key",
            "private/storage/key",
        ):
            self.assertNotIn(
                forbidden,
                rendered,
            )

    def test_provider_task_keeps_task_and_context_separate(
        self,
    ) -> None:
        message = build_provider_task_message(
            task="Recommend the next sales step.",
            rendered_context=(
                "# Trusted Business Context\n"
                "Name: Tenant A"
            ),
        )

        self.assertIn(
            "# Task",
            message,
        )

        self.assertIn(
            "# Trusted Context",
            message,
        )

        self.assertIn(
            "# Response Requirement",
            message,
        )

        self.assertIn(
            "Use only the trusted context above",
            message,
        )


class AIAgentRuntimeTests(
    unittest.IsolatedAsyncioTestCase,
):
    async def test_typed_runtime_preserves_trusted_context_and_provider_metadata(
        self,
    ) -> None:
        class TypedRuntimeOutput(BaseModel):
            headline: str
            channel: str

        class TypedRuntimeProvider(_FakeProvider):
            def __init__(self) -> None:
                self.request: AIAgentProviderRequest | None = None
                self.output_type = None

            @property
            def provider_name(self) -> str:
                return "typed-runtime"

            async def generate_typed_with_metadata(
                self,
                request: AIAgentProviderRequest,
                output_type,
            ):
                self.request = request
                self.output_type = output_type

                return AIAgentTypedProviderResult(
                    output=output_type.model_validate(
                        {
                            "headline": "Grounded campaign direction.",
                            "channel": "instagram",
                        }
                    ),
                    metadata=AIAgentProviderMetadata(
                        provider_request_id="req_runtime_typed_123",
                        input_tokens=880,
                        output_tokens=210,
                    ),
                )

        task = "Prepare a grounded campaign direction."

        context = _context_bundle(
            business_id=BUSINESS_A_ID,
            purpose="sales",
            task=task,
            include_sources=True,
        )

        provider = TypedRuntimeProvider()
        session = AsyncMock()

        with patch(
            "app.agents.runtime.assemble_ai_context",
            new=AsyncMock(
                return_value=context,
            ),
        ) as assemble:
            result = await execute_ai_agent_typed_with_metadata(
                session,
                BUSINESS_A_ID,
                AIAgentExecutionRequest(
                    role="sales",
                    task=task,
                ),
                provider,
                TypedRuntimeOutput,
            )

        self.assertEqual(
            result.business_id,
            BUSINESS_A_ID,
        )
        self.assertEqual(
            result.role,
            "sales",
        )

        self.assertEqual(
            result.context_revision,
            context.revision,
        )
        self.assertEqual(
            result.context_source_count,
            context.source_count,
        )
        self.assertEqual(
            result.business_brain_source_count,
            context.business_brain_source_count,
        )
        self.assertEqual(
            result.memory_source_count,
            context.memory_source_count,
        )

        self.assertIsInstance(
            result.output,
            TypedRuntimeOutput,
        )
        self.assertEqual(
            result.output.headline,
            "Grounded campaign direction.",
        )
        self.assertEqual(
            result.output.channel,
            "instagram",
        )

        self.assertEqual(
            result.provider_metadata.provider_request_id,
            "req_runtime_typed_123",
        )
        self.assertEqual(
            result.provider_metadata.input_tokens,
            880,
        )
        self.assertEqual(
            result.provider_metadata.output_tokens,
            210,
        )

        self.assertIs(
            provider.output_type,
            TypedRuntimeOutput,
        )
        self.assertIsNotNone(
            provider.request,
        )
        self.assertEqual(
            provider.request.business_id,
            BUSINESS_A_ID,
        )
        self.assertIs(
            provider.request.context,
            context,
        )

        # Proves the same trusted Business Brain / permitted-memory context
        # was rendered into the typed provider request.
        self.assertIn(
            "Premium Milk",
            provider.request.task,
        )
        self.assertIn(
            "Customer prefers WhatsApp.",
            provider.request.task,
        )

        assemble.assert_awaited_once()

    async def test_metadata_aware_provider_returns_safe_metadata(
        self,
    ) -> None:
        context = _context_bundle(
            business_id=BUSINESS_A_ID,
            purpose="sales",
            task="Prepare context.",
        )

        with patch(
            "app.agents.runtime.assemble_ai_context",
            new=AsyncMock(
                return_value=context,
            ),
        ):
            result = await execute_ai_agent_with_metadata(
                AsyncMock(),
                BUSINESS_A_ID,
                AIAgentExecutionRequest(
                    role="sales",
                    task="Prepare context.",
                ),
                _MetadataAwareProvider(),
            )

        self.assertEqual(
            result.execution_result.business_id,
            BUSINESS_A_ID,
        )

        self.assertEqual(
            result.execution_result.role,
            "sales",
        )

        self.assertEqual(
            result.execution_result.output.status,
            "completed",
        )

        self.assertEqual(
            result.provider_metadata.provider_request_id,
            "req_runtime_123",
        )

        self.assertEqual(
            result.provider_metadata.input_tokens,
            1500,
        )

        self.assertEqual(
            result.provider_metadata.output_tokens,
            300,
        )

    async def test_metadata_runtime_supports_legacy_provider(
        self,
    ) -> None:
        context = _context_bundle(
            business_id=BUSINESS_A_ID,
            purpose="sales",
            task="Prepare context.",
        )

        with patch(
            "app.agents.runtime.assemble_ai_context",
            new=AsyncMock(
                return_value=context,
            ),
        ):
            result = await execute_ai_agent_with_metadata(
                AsyncMock(),
                BUSINESS_A_ID,
                AIAgentExecutionRequest(
                    role="sales",
                    task="Prepare context.",
                ),
                _FakeProvider(),
            )

        self.assertEqual(
            result.execution_result.output.status,
            "completed",
        )

        self.assertIsNone(
            result.provider_metadata.provider_request_id,
        )

        self.assertIsNone(
            result.provider_metadata.input_tokens,
        )

        self.assertIsNone(
            result.provider_metadata.output_tokens,
        )

    async def test_legacy_runtime_result_remains_backward_compatible(
        self,
    ) -> None:
        context = _context_bundle(
            business_id=BUSINESS_A_ID,
            purpose="sales",
            task="Prepare context.",
        )

        with patch(
            "app.agents.runtime.assemble_ai_context",
            new=AsyncMock(
                return_value=context,
            ),
        ):
            result = await execute_ai_agent(
                AsyncMock(),
                BUSINESS_A_ID,
                AIAgentExecutionRequest(
                    role="sales",
                    task="Prepare context.",
                ),
                _MetadataAwareProvider(),
            )

        self.assertIs(
            type(result),
            AIAgentExecutionResult,
        )

        self.assertEqual(
            result.output.summary,
            "Safe metadata-aware structured result.",
        )

        self.assertFalse(
            hasattr(
                result,
                "provider_metadata",
            )
        )

    async def test_invalid_metadata_provider_envelope_is_rejected(
        self,
    ) -> None:
        context = _context_bundle(
            business_id=BUSINESS_A_ID,
            purpose="sales",
            task="Prepare context.",
        )

        with patch(
            "app.agents.runtime.assemble_ai_context",
            new=AsyncMock(
                return_value=context,
            ),
        ):
            with self.assertRaises(
                AIAgentResponseError,
            ) as raised:
                await execute_ai_agent_with_metadata(
                    AsyncMock(),
                    BUSINESS_A_ID,
                    AIAgentExecutionRequest(
                        role="sales",
                        task="Prepare context.",
                    ),
                    _InvalidMetadataResultProvider(),
                )

        self.assertNotIn(
            "secret raw provider detail",
            str(raised.exception),
        )

    async def test_unexpected_metadata_provider_exception_is_sanitized(
        self,
    ) -> None:
        context = _context_bundle(
            business_id=BUSINESS_A_ID,
            purpose="sales",
            task="Prepare context.",
        )

        provider = _ExplodingMetadataProvider(
            RuntimeError(
                "secret provider HTTP body API key internal detail"
            )
        )

        with patch(
            "app.agents.runtime.assemble_ai_context",
            new=AsyncMock(
                return_value=context,
            ),
        ):
            with self.assertRaises(
                AIAgentProviderError,
            ) as raised:
                await execute_ai_agent_with_metadata(
                    AsyncMock(),
                    BUSINESS_A_ID,
                    AIAgentExecutionRequest(
                        role="sales",
                        task="Prepare context.",
                    ),
                    provider,
                )

        error_text = str(
            raised.exception
        )

        self.assertNotIn(
            "secret provider HTTP body",
            error_text,
        )

        self.assertNotIn(
            "API key",
            error_text,
        )

    async def test_metadata_provider_domain_error_is_preserved(
        self,
    ) -> None:
        context = _context_bundle(
            business_id=BUSINESS_A_ID,
            purpose="sales",
            task="Prepare context.",
        )

        provider = _ExplodingMetadataProvider(
            AIAgentProviderError(
                "safe provider failure"
            )
        )

        with patch(
            "app.agents.runtime.assemble_ai_context",
            new=AsyncMock(
                return_value=context,
            ),
        ):
            with self.assertRaises(
                AIAgentProviderError,
            ) as raised:
                await execute_ai_agent_with_metadata(
                    AsyncMock(),
                    BUSINESS_A_ID,
                    AIAgentExecutionRequest(
                        role="sales",
                        task="Prepare context.",
                    ),
                    provider,
                )

        self.assertEqual(
            str(raised.exception),
            "safe provider failure",
        )

    async def test_metadata_provider_request_remains_tenant_controlled(
        self,
    ) -> None:
        task = "Recommend next step."

        context = _context_bundle(
            business_id=BUSINESS_A_ID,
            purpose="sales",
            task=task,
            include_sources=True,
        )

        provider = _MetadataAwareProvider()

        with patch(
            "app.agents.runtime.assemble_ai_context",
            new=AsyncMock(
                return_value=context,
            ),
        ):
            await execute_ai_agent_with_metadata(
                AsyncMock(),
                BUSINESS_A_ID,
                AIAgentExecutionRequest(
                    role="sales",
                    task=task,
                ),
                provider,
            )

        self.assertIsNotNone(
            provider.request,
        )

        assert provider.request is not None

        self.assertEqual(
            provider.request.business_id,
            BUSINESS_A_ID,
        )

        self.assertEqual(
            provider.request.role,
            "sales",
        )

        self.assertEqual(
            provider.request.context.business_id,
            BUSINESS_A_ID,
        )

        self.assertEqual(
            provider.request.system_instructions,
            build_agent_system_instructions(
                get_agent_definition(
                    "sales",
                )
            ),
        )

        self.assertEqual(
            provider.request.task,
            build_provider_task_message(
                task=task,
                rendered_context=render_ai_context(
                    context,
                ),
            ),
        )

    async def test_sales_execution_uses_sales_context_purpose(
        self,
    ) -> None:
        context = _context_bundle(
            business_id=BUSINESS_A_ID,
            purpose="sales",
            task="Recommend next step.",
        )

        context_mock = AsyncMock(
            return_value=context,
        )

        provider = _FakeProvider()

        with patch(
            "app.agents.runtime.assemble_ai_context",
            new=context_mock,
        ):
            result = await execute_ai_agent(
                AsyncMock(),
                BUSINESS_A_ID,
                AIAgentExecutionRequest(
                    role="sales",
                    task="Recommend next step.",
                    memory_types=[
                        "customer",
                        "decision",
                    ],
                    min_memory_importance=3,
                    min_memory_confidence=Decimal("0.800"),
                ),
                provider,
            )

        self.assertEqual(
            result.role,
            "sales",
        )

        self.assertEqual(
            result.output.status,
            "completed",
        )

        context_request = (
            context_mock.await_args.args[2]
        )

        self.assertEqual(
            context_request.purpose,
            "sales",
        )

        self.assertEqual(
            context_request.memory_types,
            [
                "customer",
                "decision",
            ],
        )

        self.assertEqual(
            context_request.min_memory_importance,
            3,
        )

        self.assertEqual(
            context_request.min_memory_confidence,
            Decimal("0.800"),
        )

    async def test_cmo_maps_to_marketing_context(
        self,
    ) -> None:
        context = _context_bundle(
            business_id=BUSINESS_A_ID,
            purpose="marketing",
            task="Prepare campaign recommendations.",
        )

        context_mock = AsyncMock(
            return_value=context,
        )

        with patch(
            "app.agents.runtime.assemble_ai_context",
            new=context_mock,
        ):
            result = await execute_ai_agent(
                AsyncMock(),
                BUSINESS_A_ID,
                AIAgentExecutionRequest(
                    role="cmo",
                    task="Prepare campaign recommendations.",
                ),
                _FakeProvider(),
            )

        context_request = (
            context_mock.await_args.args[2]
        )

        self.assertEqual(
            context_request.purpose,
            "marketing",
        )

        self.assertEqual(
            result.role,
            "cmo",
        )

    async def test_provider_receives_server_controlled_instructions(
        self,
    ) -> None:
        context = _context_bundle(
            business_id=BUSINESS_A_ID,
            purpose="sales",
            task="Recommend next step.",
            include_sources=True,
        )

        provider = _CapturingProvider()

        with patch(
            "app.agents.runtime.assemble_ai_context",
            new=AsyncMock(
                return_value=context,
            ),
        ):
            await execute_ai_agent(
                AsyncMock(),
                BUSINESS_A_ID,
                AIAgentExecutionRequest(
                    role="sales",
                    task="Recommend next step.",
                ),
                provider,
            )

        self.assertIsNotNone(
            provider.request,
        )

        assert provider.request is not None

        self.assertEqual(
            provider.request.business_id,
            BUSINESS_A_ID,
        )

        self.assertEqual(
            provider.request.role,
            "sales",
        )

        self.assertIn(
            "Role: AI Sales",
            provider.request.system_instructions,
        )

        self.assertIn(
            "Use only the trusted business context",
            provider.request.system_instructions,
        )

        self.assertIn(
            "# Task",
            provider.request.task,
        )

        self.assertIn(
            "# Trusted Context",
            provider.request.task,
        )

        self.assertIn(
            "Premium Milk",
            provider.request.task,
        )

    async def test_runtime_rejects_cross_tenant_context(
        self,
    ) -> None:
        context = _context_bundle(
            business_id=BUSINESS_B_ID,
            purpose="sales",
            task="Recommend next step.",
        )

        with patch(
            "app.agents.runtime.assemble_ai_context",
            new=AsyncMock(
                return_value=context,
            ),
        ):
            with self.assertRaises(
                AIAgentContextError,
            ):
                await execute_ai_agent(
                    AsyncMock(),
                    BUSINESS_A_ID,
                    AIAgentExecutionRequest(
                        role="sales",
                        task="Recommend next step.",
                    ),
                    _FakeProvider(),
                )

    async def test_runtime_rejects_wrong_context_purpose(
        self,
    ) -> None:
        context = _context_bundle(
            business_id=BUSINESS_A_ID,
            purpose="marketing",
            task="Recommend next step.",
        )

        with patch(
            "app.agents.runtime.assemble_ai_context",
            new=AsyncMock(
                return_value=context,
            ),
        ):
            with self.assertRaises(
                AIAgentContextError,
            ):
                await execute_ai_agent(
                    AsyncMock(),
                    BUSINESS_A_ID,
                    AIAgentExecutionRequest(
                        role="sales",
                        task="Recommend next step.",
                    ),
                    _FakeProvider(),
                )

    async def test_runtime_rejects_context_task_mismatch(
        self,
    ) -> None:
        context = _context_bundle(
            business_id=BUSINESS_A_ID,
            purpose="sales",
            task="Different task.",
        )

        with patch(
            "app.agents.runtime.assemble_ai_context",
            new=AsyncMock(
                return_value=context,
            ),
        ):
            with self.assertRaises(
                AIAgentContextError,
            ):
                await execute_ai_agent(
                    AsyncMock(),
                    BUSINESS_A_ID,
                    AIAgentExecutionRequest(
                        role="sales",
                        task="Original task.",
                    ),
                    _FakeProvider(),
                )

    async def test_context_assembly_failure_is_sanitized(
        self,
    ) -> None:
        with patch(
            "app.agents.runtime.assemble_ai_context",
            new=AsyncMock(
                side_effect=AIContextAssemblyError(
                    "private PostgreSQL connection detail"
                )
            ),
        ):
            with self.assertRaises(
                AIAgentContextError,
            ) as raised:
                await execute_ai_agent(
                    AsyncMock(),
                    BUSINESS_A_ID,
                    AIAgentExecutionRequest(
                        role="sales",
                        task="Prepare context.",
                    ),
                    _FakeProvider(),
                )

        self.assertNotIn(
            "private PostgreSQL connection detail",
            str(raised.exception),
        )

    async def test_unknown_provider_exception_is_sanitized(
        self,
    ) -> None:
        context = _context_bundle(
            business_id=BUSINESS_A_ID,
            purpose="sales",
            task="Prepare context.",
        )

        provider = _ExplodingProvider(
            RuntimeError(
                "secret provider HTTP body and API key detail"
            )
        )

        with patch(
            "app.agents.runtime.assemble_ai_context",
            new=AsyncMock(
                return_value=context,
            ),
        ):
            with self.assertRaises(
                AIAgentProviderError,
            ) as raised:
                await execute_ai_agent(
                    AsyncMock(),
                    BUSINESS_A_ID,
                    AIAgentExecutionRequest(
                        role="sales",
                        task="Prepare context.",
                    ),
                    provider,
                )

        self.assertNotIn(
            "secret provider HTTP body",
            str(raised.exception),
        )

    async def test_provider_domain_error_remains_domain_error(
        self,
    ) -> None:
        context = _context_bundle(
            business_id=BUSINESS_A_ID,
            purpose="sales",
            task="Prepare context.",
        )

        provider = _ExplodingProvider(
            AIAgentProviderError(
                "safe provider failure"
            )
        )

        with patch(
            "app.agents.runtime.assemble_ai_context",
            new=AsyncMock(
                return_value=context,
            ),
        ):
            with self.assertRaises(
                AIAgentProviderError,
            ) as raised:
                await execute_ai_agent(
                    AsyncMock(),
                    BUSINESS_A_ID,
                    AIAgentExecutionRequest(
                        role="sales",
                        task="Prepare context.",
                    ),
                    provider,
                )

        self.assertEqual(
            str(raised.exception),
            "safe provider failure",
        )

    async def test_invalid_provider_output_is_rejected(
        self,
    ) -> None:
        context = _context_bundle(
            business_id=BUSINESS_A_ID,
            purpose="sales",
            task="Prepare context.",
        )

        provider = _InvalidOutputProvider()

        with patch(
            "app.agents.runtime.assemble_ai_context",
            new=AsyncMock(
                return_value=context,
            ),
        ):
            with self.assertRaises(
                AIAgentResponseError,
            ):
                await execute_ai_agent(
                    AsyncMock(),
                    BUSINESS_A_ID,
                    AIAgentExecutionRequest(
                        role="sales",
                        task="Prepare context.",
                    ),
                    provider,
                )

    async def test_approval_required_output_is_preserved(
        self,
    ) -> None:
        context = _context_bundle(
            business_id=BUSINESS_A_ID,
            purpose="sales",
            task="Prepare follow-up.",
        )

        provider = _ApprovalProvider()

        with patch(
            "app.agents.runtime.assemble_ai_context",
            new=AsyncMock(
                return_value=context,
            ),
        ):
            result = await execute_ai_agent(
                AsyncMock(),
                BUSINESS_A_ID,
                AIAgentExecutionRequest(
                    role="sales",
                    task="Prepare follow-up.",
                ),
                provider,
            )

        self.assertEqual(
            result.output.status,
            "needs_approval",
        )

        self.assertEqual(
            len(result.output.proposed_actions),
            1,
        )

        action = result.output.proposed_actions[0]

        self.assertEqual(
            action.action_type,
            "send_customer_message",
        )

        self.assertTrue(
            action.requires_approval,
        )

    async def test_result_contains_traceable_context_revision_and_counts(
        self,
    ) -> None:
        context = _context_bundle(
            business_id=BUSINESS_A_ID,
            purpose="sales",
            task="Prepare context.",
            include_sources=True,
        )

        with patch(
            "app.agents.runtime.assemble_ai_context",
            new=AsyncMock(
                return_value=context,
            ),
        ):
            result = await execute_ai_agent(
                AsyncMock(),
                BUSINESS_A_ID,
                AIAgentExecutionRequest(
                    role="sales",
                    task="Prepare context.",
                ),
                _FakeProvider(),
            )

        self.assertEqual(
            result.context_revision,
            context.revision,
        )

        self.assertEqual(
            result.context_source_count,
            2,
        )

        self.assertEqual(
            result.business_brain_source_count,
            1,
        )

        self.assertEqual(
            result.memory_source_count,
            1,
        )


class _FakeProvider:
    @property
    def provider_name(self) -> str:
        return "fake"

    async def generate(
        self,
        request: AIAgentProviderRequest,
    ) -> AIAgentStructuredOutput:
        _ = request

        return AIAgentStructuredOutput(
            status="completed",
            summary="Safe structured result.",
            recommendations=[
                "Continue with the recommended next step.",
            ],
        )


class _MetadataAwareProvider(_FakeProvider):
    def __init__(self) -> None:
        self.request: AIAgentProviderRequest | None = None

    @property
    def provider_name(self) -> str:
        return "metadata-aware"

    async def generate_with_metadata(
        self,
        request: AIAgentProviderRequest,
    ) -> AIAgentProviderResult:
        self.request = request

        return AIAgentProviderResult(
            output=AIAgentStructuredOutput(
                status="completed",
                summary=(
                    "Safe metadata-aware structured result."
                ),
            ),
            metadata=AIAgentProviderMetadata(
                provider_request_id="req_runtime_123",
                input_tokens=1500,
                output_tokens=300,
            ),
        )


class _InvalidMetadataResultProvider(_FakeProvider):
    async def generate_with_metadata(
        self,
        request: AIAgentProviderRequest,
    ) -> object:
        _ = request

        return {
            "raw_provider_response": (
                "secret raw provider detail"
            ),
        }


class _ExplodingMetadataProvider(_FakeProvider):
    def __init__(
        self,
        error: Exception,
    ) -> None:
        self.error = error

    async def generate_with_metadata(
        self,
        request: AIAgentProviderRequest,
    ) -> AIAgentProviderResult:
        _ = request
        raise self.error


class _CapturingProvider:
    def __init__(self) -> None:
        self.request: AIAgentProviderRequest | None = None

    @property
    def provider_name(self) -> str:
        return "capturing"

    async def generate(
        self,
        request: AIAgentProviderRequest,
    ) -> AIAgentStructuredOutput:
        self.request = request

        return AIAgentStructuredOutput(
            status="completed",
            summary="Captured.",
        )


class _BlankNameProvider:
    @property
    def provider_name(self) -> str:
        return "   "

    async def generate(
        self,
        request: AIAgentProviderRequest,
    ) -> AIAgentStructuredOutput:
        _ = request

        return AIAgentStructuredOutput(
            status="completed",
            summary="Never used.",
        )


class _ExplodingProvider:
    def __init__(
        self,
        error: Exception,
    ) -> None:
        self.error = error

    @property
    def provider_name(self) -> str:
        return "exploding"

    async def generate(
        self,
        request: AIAgentProviderRequest,
    ) -> AIAgentStructuredOutput:
        _ = request
        raise self.error


class _InvalidOutputProvider:
    @property
    def provider_name(self) -> str:
        return "invalid-output"

    async def generate(
        self,
        request: AIAgentProviderRequest,
    ):
        _ = request

        return {
            "status": "completed",
            "summary": "",
            "unknown_field": "must not survive",
        }


class _ApprovalProvider:
    @property
    def provider_name(self) -> str:
        return "approval"

    async def generate(
        self,
        request: AIAgentProviderRequest,
    ) -> AIAgentStructuredOutput:
        _ = request

        return AIAgentStructuredOutput(
            status="needs_approval",
            summary=(
                "A customer follow-up is recommended."
            ),
            recommendations=[
                "Offer the recurring delivery plan.",
            ],
            proposed_actions=[
                AIAgentProposedAction(
                    action_type="send_customer_message",
                    description=(
                        "Send the proposed recurring delivery "
                        "offer to the customer."
                    ),
                    risk_level="medium",
                    requires_approval=True,
                ),
            ],
        )


def _context_bundle(
    *,
    business_id: UUID,
    purpose: str,
    task: str,
    include_sources: bool = False,
) -> AIContextBundle:
    sources = []

    if include_sources:
        brain_content = (
            "Type: Product\n"
            "Name: Premium Milk\n"
            "Price: 500.00 PKR"
        )

        sources.append(
            BusinessBrainContextSource(
                business_id=business_id,
                source_type="catalog_item",
                source_id="catalog:milk",
                title="Premium Milk",
                content=brain_content,
                updated_at=BASE_TIME,
                content_hash=sha256(
                    brain_content.encode("utf-8")
                ).hexdigest(),
            )
        )

        memory_content = (
            "Customer prefers WhatsApp."
        )

        sources.append(
            BusinessMemoryContextSource(
                business_id=business_id,
                memory_id=uuid4(),
                memory_type="customer",
                content=memory_content,
                importance=5,
                confidence=Decimal("0.950"),
                occurred_at=None,
                updated_at=BASE_TIME,
                content_hash=sha256(
                    memory_content.encode("utf-8")
                ).hexdigest(),
            )
        )

    brain_count = sum(
        source.origin == "business_brain"
        for source in sources
    )

    memory_count = sum(
        source.origin == "business_memory"
        for source in sources
    )

    revision_hasher = sha256()

    for source in sources:
        revision_hasher.update(
            source.origin.encode("utf-8")
        )

        revision_hasher.update(
            source.content_hash.encode("ascii")
        )

    return AIContextBundle(
        business_id=business_id,
        purpose=purpose,  # type: ignore[arg-type]
        task=task,
        sources=sources,
        source_count=len(sources),
        business_brain_source_count=brain_count,
        memory_source_count=memory_count,
        revision=revision_hasher.hexdigest(),
    )
