from __future__ import annotations

from typing import Final, TypeVar

from openai import AsyncOpenAI, OpenAIError
from pydantic import BaseModel, ValidationError

from app.agents.provider import (
    AIAgentProviderMetadata,
    AIAgentProviderRequest,
    AIAgentProviderResult,
    AIAgentTypedProviderResult,
)
from app.core.config import Settings
from app.exceptions.ai_agent import (
    AIAgentProviderError,
    AIAgentResponseError,
)
from app.schemas.ai_agent import (
    AIAgentStructuredOutput,
)


_PROVIDER_NOT_CONFIGURED: Final = (
    "OpenAI provider is not configured"
)

_PROVIDER_FAILURE: Final = (
    "OpenAI provider could not complete the request"
)

_INVALID_RESPONSE: Final = (
    "OpenAI provider returned an invalid structured response"
)


StructuredOutput = TypeVar(
    "StructuredOutput",
    bound=BaseModel,
)


class OpenAIAgentProvider:
    """
    Production OpenAI adapter for the provider-independent AI Agent Runtime.

    Responsibilities are deliberately narrow:

    - communicate with OpenAI
    - request strict structured output
    - disable response storage
    - sanitize provider failures
    - validate returned output
    - expose audit-safe request and token metadata

    This adapter does NOT:
    - query the database
    - choose tenant context
    - execute business actions
    - manage approvals
    - write persistent AI memory
    """

    def __init__(
        self,
        *,
        client: AsyncOpenAI,
        model: str,
    ) -> None:
        normalized_model = model.strip()

        if not normalized_model:
            raise ValueError(
                "OpenAI model cannot be blank"
            )

        self._client = client
        self._model = normalized_model

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def model(self) -> str:
        return self._model

    async def generate(
        self,
        request: AIAgentProviderRequest,
    ) -> AIAgentStructuredOutput:
        """
        Backward-compatible provider entry point.

        The existing runtime still expects only AIAgentStructuredOutput.
        The richer metadata contract is introduced through
        generate_with_metadata() and will be adopted by the runtime in the
        next controlled step.
        """
        result = await self.generate_with_metadata(
            request
        )

        return result.output

    async def generate_with_metadata(
        self,
        request: AIAgentProviderRequest,
    ) -> AIAgentProviderResult:
        """
        Generate one validated structured result together with audit-safe
        provider metadata.

        Business context has already been assembled and tenant-validated by
        the runtime before this method is called.

        `store=False` prevents this application from intentionally requesting
        OpenAI response storage for private business-agent executions.

        Only safe operational metadata is returned:
        - OpenAI request ID
        - input token count
        - output token count

        Raw responses, headers, credentials, context, and hidden reasoning are
        never returned.
        """
        typed_result = await self.generate_typed_with_metadata(
            request,
            AIAgentStructuredOutput,
        )

        return AIAgentProviderResult(
            output=typed_result.output,
            metadata=typed_result.metadata,
        )

    async def generate_typed_with_metadata(
        self,
        request: AIAgentProviderRequest,
        output_type: type[StructuredOutput],
    ) -> AIAgentTypedProviderResult[StructuredOutput]:
        """
        Generate one caller-selected, strictly parsed Pydantic result.

        The request and metadata boundaries are identical to ordinary agent
        generation; only the public structured result schema varies.
        """
        try:
            response = await self._client.responses.parse(
                model=self._model,
                input=[
                    {
                        "role": "developer",
                        "content": request.system_instructions,
                    },
                    {
                        "role": "user",
                        "content": request.task,
                    },
                ],
                text_format=output_type,
                store=False,
                max_output_tokens=request.max_output_tokens,
            )

        except ValidationError:
            raise AIAgentResponseError(
                _INVALID_RESPONSE
            ) from None

        except OpenAIError:
            # Never expose provider response bodies, request IDs,
            # authentication details, headers, or API keys through
            # exception text.
            raise AIAgentProviderError(
                _PROVIDER_FAILURE
            ) from None

        except Exception:
            # Defense in depth against unexpected SDK/runtime failures.
            raise AIAgentProviderError(
                _PROVIDER_FAILURE
            ) from None

        if response.status != "completed":
            raise AIAgentResponseError(
                _INVALID_RESPONSE
            )

        parsed_outputs: list[StructuredOutput] = []

        for output in response.output:
            if output.type != "message":
                continue

            for content in output.content:
                if content.type != "output_text":
                    continue

                parsed = content.parsed

                if parsed is None:
                    continue

                try:
                    validated = (
                        output_type.model_validate(
                            parsed
                        )
                    )

                except ValidationError:
                    raise AIAgentResponseError(
                        _INVALID_RESPONSE
                    ) from None

                except Exception:
                    raise AIAgentResponseError(
                        _INVALID_RESPONSE
                    ) from None

                parsed_outputs.append(
                    validated
                )

        # One agent execution must produce exactly one authoritative
        # structured result. Multiple independent parsed results would make
        # approval semantics and auditability ambiguous.
        if len(parsed_outputs) != 1:
            raise AIAgentResponseError(
                _INVALID_RESPONSE
            )

        metadata = AIAgentProviderMetadata(
            provider_request_id=(
                _safe_request_id(
                    getattr(
                        response,
                        "_request_id",
                        None,
                    )
                )
            ),
            input_tokens=(
                _safe_token_count(
                    getattr(
                        getattr(
                            response,
                            "usage",
                            None,
                        ),
                        "input_tokens",
                        None,
                    )
                )
            ),
            output_tokens=(
                _safe_token_count(
                    getattr(
                        getattr(
                            response,
                            "usage",
                            None,
                        ),
                        "output_tokens",
                        None,
                    )
                )
            ),
        )

        return AIAgentTypedProviderResult(
            output=parsed_outputs[0],
            metadata=metadata,
        )


def create_openai_provider(
    config: Settings,
) -> OpenAIAgentProvider:
    """
    Build the production OpenAI provider from server-side settings.

    The API key remains inside the backend process and is never added to
    requests, responses, schemas, logs, browser code, or tenant data.
    """
    api_key = config.openai_api_key_value

    if api_key is None or not api_key.strip():
        raise AIAgentProviderError(
            _PROVIDER_NOT_CONFIGURED
        )

    client = AsyncOpenAI(
        api_key=api_key,
        timeout=config.openai_timeout_seconds,
        max_retries=config.openai_max_retries,
    )

    return OpenAIAgentProvider(
        client=client,
        model=config.openai_model,
    )


def _safe_request_id(
    value: object,
) -> str | None:
    """
    Return only a bounded, non-blank request identifier.

    Optional provider metadata must never cause an otherwise valid AI
    execution to fail.
    """
    if not isinstance(
        value,
        str,
    ):
        return None

    normalized = value.strip()

    if (
        not normalized
        or len(normalized) > 255
    ):
        return None

    return normalized


def _safe_token_count(
    value: object,
) -> int | None:
    """
    Return a safe non-negative token count.

    Provider metadata is observational. Malformed optional metadata must not
    invalidate an otherwise valid structured AI result.
    """
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
    ):
        return None

    return value
