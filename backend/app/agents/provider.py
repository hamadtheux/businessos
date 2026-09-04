from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar, runtime_checkable
from uuid import UUID

from pydantic import BaseModel

from app.schemas.ai_agent import (
    AIAgentRole,
    AIAgentStructuredOutput,
)
from app.schemas.ai_context import AIContextBundle


AIAgentTypedOutput = TypeVar(
    "AIAgentTypedOutput",
    bound=BaseModel,
)


@dataclass(frozen=True, slots=True)
class AIAgentProviderRequest:
    """
    Provider-neutral input for one model execution.

    The provider receives:
    - server-controlled agent identity/instructions
    - the explicit user/business task
    - the already assembled trusted tenant context

    Provider-specific settings such as API keys, HTTP headers,
    temperature, retry rules, and token limits belong inside provider
    adapters, never inside this request.
    """

    business_id: UUID
    role: AIAgentRole

    system_instructions: str
    task: str

    context: AIContextBundle
    max_output_tokens: int | None = None

    def __post_init__(self) -> None:
        if self.context.business_id != self.business_id:
            raise ValueError(
                "Provider request context belongs to a different business"
            )

        if not self.system_instructions.strip():
            raise ValueError(
                "system_instructions cannot be blank"
            )

        if not self.task.strip():
            raise ValueError(
                "task cannot be blank"
            )

        if (
            self.max_output_tokens is not None
            and not 1 <= self.max_output_tokens <= 32_768
        ):
            raise ValueError(
                "max_output_tokens must be between 1 and 32768"
            )


@dataclass(
    frozen=True,
    slots=True,
)
class AIAgentProviderMetadata:
    """
    Safe provider metadata produced by exactly one model request.

    This object intentionally contains only audit-safe operational metadata.

    It must never contain:
    - API keys
    - authorization headers
    - raw provider responses
    - rendered Business Brain/context payloads
    - hidden reasoning
    - provider error bodies
    """

    provider_request_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None

    def __post_init__(self) -> None:
        if self.provider_request_id is not None:
            normalized_request_id = (
                self.provider_request_id.strip()
            )

            if not normalized_request_id:
                raise ValueError(
                    "provider_request_id cannot be blank"
                )

            if len(normalized_request_id) > 255:
                raise ValueError(
                    "provider_request_id exceeds maximum length"
                )

            object.__setattr__(
                self,
                "provider_request_id",
                normalized_request_id,
            )

        _validate_optional_token_count(
            self.input_tokens,
            field_name="input_tokens",
        )

        _validate_optional_token_count(
            self.output_tokens,
            field_name="output_tokens",
        )


@dataclass(
    frozen=True,
    slots=True,
)
class AIAgentProviderResult:
    """
    Provider-neutral result envelope for one AI model execution.

    `output` is the validated public agent result.

    `metadata` contains only safe operational information required for
    observability, billing, and the persistent execution ledger.
    """

    output: AIAgentStructuredOutput
    metadata: AIAgentProviderMetadata


@dataclass(
    frozen=True,
    slots=True,
)
class AIAgentTypedProviderResult(
    Generic[AIAgentTypedOutput]
):
    """Provider-neutral typed output plus existing audit-safe metadata."""

    output: AIAgentTypedOutput
    metadata: AIAgentProviderMetadata


@runtime_checkable
class AIAgentProvider(Protocol):
    """
    Contract implemented by every supported AI model provider.

    The wider agent runtime depends only on this interface.

    Provider implementations are responsible for:
    - authenticating with their own service
    - choosing/configuring their model
    - sending the provider-specific request
    - enforcing provider timeouts/retries
    - validating/parsing structured provider responses
    - converting provider failures into safe agent-domain exceptions

    Providers must never execute business actions themselves.
    """

    @property
    def provider_name(self) -> str:
        """Stable internal identifier for the provider."""
        ...

    async def generate(
        self,
        request: AIAgentProviderRequest,
    ) -> AIAgentStructuredOutput:
        """
        Generate one validated structured AI employee response.

        This return type remains unchanged for the moment so introducing the
        provider metadata envelope does not destabilize the existing runtime.
        The runtime contract will be upgraded in the next controlled step.
        """
        ...


def validate_agent_provider(
    provider: AIAgentProvider,
) -> None:
    """
    Fail fast when the runtime receives an object that does not implement the
    provider contract.
    """
    if not isinstance(
        provider,
        AIAgentProvider,
    ):
        raise TypeError(
            "AI agent provider does not implement AIAgentProvider"
        )

    provider_name = provider.provider_name

    if (
        not isinstance(provider_name, str)
        or not provider_name.strip()
    ):
        raise ValueError(
            "AI agent provider_name cannot be blank"
        )


def get_agent_provider_model_name(
    provider: AIAgentProvider,
) -> str:
    """
    Return the provider's configured model identifier for audit logging.

    Model configuration remains owned by the provider adapter. The execution
    ledger uses this helper rather than reading global application settings,
    which keeps audit records correct when multiple providers or models are
    introduced later.

    Provider implementations used for real execution must expose a non-empty
    string `model` property.
    """
    model_name = getattr(
        provider,
        "model",
        None,
    )

    if (
        not isinstance(model_name, str)
        or not model_name.strip()
    ):
        raise ValueError(
            "AI agent provider model cannot be determined"
        )

    return model_name.strip()


def _validate_optional_token_count(
    value: int | None,
    *,
    field_name: str,
) -> None:
    if value is None:
        return

    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
    ):
        raise ValueError(
            f"{field_name} must be a non-negative integer"
        )
