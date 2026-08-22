from __future__ import annotations

from dataclasses import dataclass
from typing import Final
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.context_renderer import (
    build_provider_task_message,
    render_ai_context,
)
from app.agents.definitions import (
    build_agent_system_instructions,
    get_agent_definition,
)
from app.agents.provider import (
    AIAgentProvider,
    AIAgentProviderMetadata,
    AIAgentProviderRequest,
    AIAgentProviderResult,
    validate_agent_provider,
)
from app.exceptions.ai_agent import (
    AIAgentContextError,
    AIAgentProviderError,
    AIAgentResponseError,
)
from app.exceptions.ai_context import AIContextAssemblyError
from app.schemas.ai_agent import (
    AIAgentExecutionRequest,
    AIAgentExecutionResult,
    AIAgentStructuredOutput,
)
from app.schemas.ai_context import AIContextRequest
from app.services.ai_context import assemble_ai_context


_CONTEXT_ERROR_MESSAGE: Final = (
    "Unable to prepare trusted business context for the AI agent"
)

_PROVIDER_ERROR_MESSAGE: Final = (
    "AI provider could not complete the request"
)

_RESPONSE_ERROR_MESSAGE: Final = (
    "AI provider returned an invalid structured response"
)


@dataclass(
    frozen=True,
    slots=True,
)
class AIAgentRuntimeResult:
    """
    Internal runtime result for one controlled AI agent execution.

    `execution_result` is the public, provider-neutral business result.

    `provider_metadata` contains only audit-safe operational metadata such as
    request ID and token usage. It must never contain credentials, provider
    response bodies, rendered business context, or hidden reasoning.
    """

    execution_result: AIAgentExecutionResult
    provider_metadata: AIAgentProviderMetadata


async def execute_ai_agent(
    session: AsyncSession,
    business_id: UUID,
    request: AIAgentExecutionRequest,
    provider: AIAgentProvider,
) -> AIAgentExecutionResult:
    """
    Backward-compatible AI Agent Runtime entry point.

    Existing callers receive exactly the same AIAgentExecutionResult contract
    as before.

    Provider metadata is available through execute_ai_agent_with_metadata()
    for audit-ledger and observability callers.
    """
    runtime_result = await execute_ai_agent_with_metadata(
        session,
        business_id,
        request,
        provider,
    )

    return runtime_result.execution_result


async def execute_ai_agent_with_metadata(
    session: AsyncSession,
    business_id: UUID,
    request: AIAgentExecutionRequest,
    provider: AIAgentProvider,
) -> AIAgentRuntimeResult:
    """
    Execute one controlled AI employee task and retain safe provider metadata.

    Runtime flow:

        Agent execution request
            ↓
        Server-controlled agent definition
            ↓
        Tenant-scoped AI Context assembly
            ↓
        Deterministic trusted context rendering
            ↓
        Provider-neutral AI request
            ↓
        Validated provider result + safe metadata
            ↓
        Validated AIAgentExecutionResult
            ↓
        Internal AIAgentRuntimeResult

    This runtime is deliberately read-only with respect to business actions.

    Proposed actions returned by the provider are descriptions only. No CRM
    mutation, customer message, campaign launch, order modification, inventory
    change, integration call, ad spend, or other external side effect occurs
    here.
    """
    validate_agent_provider(
        provider
    )

    definition = get_agent_definition(
        request.role,
    )

    context_request = AIContextRequest(
        purpose=definition.context_purpose,
        task=request.task,
        include_business_brain=request.include_business_brain,
        include_memory=request.include_memory,
        brain_source_types=request.brain_source_types,
        memory_types=request.memory_types,
        brain_source_limit=request.brain_source_limit,
        memory_limit=request.memory_limit,
        min_memory_importance=request.min_memory_importance,
        min_memory_confidence=request.min_memory_confidence,
    )

    try:
        context = await assemble_ai_context(
            session,
            business_id,
            context_request,
        )

    except AIContextAssemblyError:
        raise AIAgentContextError(
            _CONTEXT_ERROR_MESSAGE
        ) from None

    # Defense in depth: the context assembler is already tenant-scoped, but
    # the runtime independently verifies the returned identity and purpose.
    if context.business_id != business_id:
        raise AIAgentContextError(
            _CONTEXT_ERROR_MESSAGE
        )

    if context.purpose != definition.context_purpose:
        raise AIAgentContextError(
            _CONTEXT_ERROR_MESSAGE
        )

    if context.task != request.task:
        raise AIAgentContextError(
            _CONTEXT_ERROR_MESSAGE
        )

    rendered_context = render_ai_context(
        context,
    )

    provider_task_message = build_provider_task_message(
        task=request.task,
        rendered_context=rendered_context,
    )

    system_instructions = build_agent_system_instructions(
        definition,
    )

    provider_request = AIAgentProviderRequest(
        business_id=business_id,
        role=request.role,
        system_instructions=system_instructions,
        task=provider_task_message,
        context=context,
    )

    provider_result = await _generate_provider_result(
        provider,
        provider_request,
    )

    output = _validate_provider_output(
        provider_result.output,
    )

    provider_metadata = _validate_provider_metadata(
        provider_result.metadata,
    )

    execution_result = AIAgentExecutionResult(
        business_id=business_id,
        role=request.role,
        context_revision=context.revision,
        context_source_count=context.source_count,
        business_brain_source_count=context.business_brain_source_count,
        memory_source_count=context.memory_source_count,
        output=output,
    )

    return AIAgentRuntimeResult(
        execution_result=execution_result,
        provider_metadata=provider_metadata,
    )


async def _generate_provider_result(
    provider: AIAgentProvider,
    request: AIAgentProviderRequest,
) -> AIAgentProviderResult:
    """
    Execute the provider while preserving backward compatibility.

    Metadata-aware providers should expose generate_with_metadata().

    Older/test providers implementing only generate() remain supported and
    receive an empty metadata envelope. This lets provider integrations be
    upgraded incrementally without weakening the runtime trust boundary.
    """
    generate_with_metadata = getattr(
        provider,
        "generate_with_metadata",
        None,
    )

    try:
        if callable(
            generate_with_metadata
        ):
            value = await generate_with_metadata(
                request
            )

            return _validate_provider_result(
                value
            )

        legacy_output = await provider.generate(
            request,
        )

        return AIAgentProviderResult(
            output=_validate_provider_output(
                legacy_output
            ),
            metadata=AIAgentProviderMetadata(),
        )

    except (
        AIAgentProviderError,
        AIAgentResponseError,
    ):
        raise

    except Exception:
        # Provider SDK exceptions, HTTP details, response bodies, credentials,
        # request headers, and infrastructure details must never cross this
        # boundary.
        raise AIAgentProviderError(
            _PROVIDER_ERROR_MESSAGE
        ) from None


def _validate_provider_result(
    value: object,
) -> AIAgentProviderResult:
    """
    Validate the metadata-aware provider envelope at the runtime boundary.

    Provider adapters are trusted components, but their return values are
    still treated as untrusted input before entering the core runtime.
    """
    if not isinstance(
        value,
        AIAgentProviderResult,
    ):
        raise AIAgentResponseError(
            _RESPONSE_ERROR_MESSAGE
        )

    output = _validate_provider_output(
        value.output,
    )

    metadata = _validate_provider_metadata(
        value.metadata,
    )

    return AIAgentProviderResult(
        output=output,
        metadata=metadata,
    )


def _validate_provider_metadata(
    value: object,
) -> AIAgentProviderMetadata:
    """
    Reconstruct provider metadata at the runtime trust boundary.

    Only the explicitly approved audit-safe fields survive this boundary.
    """
    if not isinstance(
        value,
        AIAgentProviderMetadata,
    ):
        raise AIAgentResponseError(
            _RESPONSE_ERROR_MESSAGE
        )

    try:
        return AIAgentProviderMetadata(
            provider_request_id=value.provider_request_id,
            input_tokens=value.input_tokens,
            output_tokens=value.output_tokens,
        )

    except (
        TypeError,
        ValueError,
    ):
        raise AIAgentResponseError(
            _RESPONSE_ERROR_MESSAGE
        ) from None

    except Exception:
        raise AIAgentResponseError(
            _RESPONSE_ERROR_MESSAGE
        ) from None


def _validate_provider_output(
    value: object,
) -> AIAgentStructuredOutput:
    """
    Revalidate provider output at the runtime trust boundary.

    Provider adapters are expected to return AIAgentStructuredOutput already,
    but the runtime does not blindly trust adapter implementations.
    """
    try:
        return AIAgentStructuredOutput.model_validate(
            value,
        )

    except ValidationError:
        raise AIAgentResponseError(
            _RESPONSE_ERROR_MESSAGE
        ) from None

    except Exception:
        raise AIAgentResponseError(
            _RESPONSE_ERROR_MESSAGE
        ) from None