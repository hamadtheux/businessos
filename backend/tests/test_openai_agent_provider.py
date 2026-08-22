from __future__ import annotations

import os
import unittest
from hashlib import sha256
from unittest.mock import patch
from uuid import uuid4

from openai import OpenAIError
from pydantic import SecretStr, ValidationError


os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault(
    "AIBOS_AUTH_SECRET_KEY",
    "x" * 32,
)


from app.agents.openai_provider import (  # noqa: E402
    OpenAIAgentProvider,
    create_openai_provider,
)
from app.agents.provider import AIAgentProviderRequest  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.exceptions.ai_agent import (  # noqa: E402
    AIAgentProviderError,
    AIAgentResponseError,
)
from app.schemas.ai_agent import (  # noqa: E402
    AIAgentStructuredOutput,
)
from app.schemas.ai_context import AIContextBundle  # noqa: E402


class OpenAIProviderConstructionTests(unittest.TestCase):
    def test_provider_rejects_blank_model(self) -> None:
        with self.assertRaises(ValueError):
            OpenAIAgentProvider(
                client=_FakeClient(
                    _successful_response()
                ),  # type: ignore[arg-type]
                model="   ",
            )

    def test_factory_rejects_missing_api_key_safely(self) -> None:
        config = _settings(
            openai_api_key=None,
        )

        with self.assertRaises(
            AIAgentProviderError,
        ) as raised:
            create_openai_provider(
                config,
            )

        self.assertEqual(
            str(raised.exception),
            "OpenAI provider is not configured",
        )

    def test_factory_builds_client_from_server_settings(
        self,
    ) -> None:
        config = _settings(
            openai_api_key=SecretStr(
                "sk-test-not-a-real-secret"
            ),
            openai_model="gpt-5.6-terra",
            openai_timeout_seconds=37.0,
            openai_max_retries=3,
        )

        fake_client = _FakeClient(
            _successful_response()
        )

        with patch(
            "app.agents.openai_provider.AsyncOpenAI",
            return_value=fake_client,
        ) as client_factory:
            provider = create_openai_provider(
                config,
            )

        self.assertEqual(
            provider.provider_name,
            "openai",
        )

        self.assertEqual(
            provider.model,
            "gpt-5.6-terra",
        )

        client_factory.assert_called_once_with(
            api_key="sk-test-not-a-real-secret",
            timeout=37.0,
            max_retries=3,
        )

    def test_secret_is_masked_in_settings_representation(
        self,
    ) -> None:
        secret = "sk-test-super-secret-value"

        config = _settings(
            openai_api_key=SecretStr(
                secret
            ),
        )

        self.assertNotIn(
            secret,
            repr(config),
        )

        self.assertNotIn(
            secret,
            str(config),
        )


class OpenAIProviderExecutionTests(
    unittest.IsolatedAsyncioTestCase,
):
    async def test_successful_request_uses_structured_output(
        self,
    ) -> None:
        responses = _FakeResponses(
            _successful_response()
        )

        provider = OpenAIAgentProvider(
            client=_FakeClient(
                responses=responses,
            ),  # type: ignore[arg-type]
            model="gpt-5.6-terra",
        )

        result = await provider.generate(
            _provider_request()
        )

        self.assertEqual(
            result.status,
            "completed",
        )

        self.assertEqual(
            result.summary,
            "Safe structured result.",
        )

        self.assertIsNotNone(
            responses.kwargs,
        )

        assert responses.kwargs is not None

        self.assertEqual(
            responses.kwargs["model"],
            "gpt-5.6-terra",
        )

        self.assertIs(
            responses.kwargs["text_format"],
            AIAgentStructuredOutput,
        )

    async def test_response_storage_is_explicitly_disabled(
        self,
    ) -> None:
        responses = _FakeResponses(
            _successful_response()
        )

        provider = OpenAIAgentProvider(
            client=_FakeClient(
                responses=responses,
            ),  # type: ignore[arg-type]
            model="gpt-5.6-terra",
        )

        await provider.generate(
            _provider_request()
        )

        assert responses.kwargs is not None

        self.assertIs(
            responses.kwargs["store"],
            False,
        )

    async def test_developer_and_user_messages_are_separated(
        self,
    ) -> None:
        responses = _FakeResponses(
            _successful_response()
        )

        provider = OpenAIAgentProvider(
            client=_FakeClient(
                responses=responses,
            ),  # type: ignore[arg-type]
            model="gpt-5.6-terra",
        )

        request = _provider_request()

        await provider.generate(
            request,
        )

        assert responses.kwargs is not None

        input_messages = responses.kwargs[
            "input"
        ]

        self.assertEqual(
            input_messages[0]["role"],
            "developer",
        )

        self.assertEqual(
            input_messages[0]["content"],
            request.system_instructions,
        )

        self.assertEqual(
            input_messages[1]["role"],
            "user",
        )

        self.assertEqual(
            input_messages[1]["content"],
            request.task,
        )

    async def test_non_completed_response_is_rejected(
        self,
    ) -> None:
        provider = OpenAIAgentProvider(
            client=_FakeClient(
                _FakeResponse(
                    status="incomplete",
                    output=[],
                )
            ),  # type: ignore[arg-type]
            model="gpt-5.6-terra",
        )

        with self.assertRaises(
            AIAgentResponseError,
        ):
            await provider.generate(
                _provider_request()
            )

    async def test_missing_parsed_output_is_rejected(
        self,
    ) -> None:
        response = _FakeResponse(
            status="completed",
            output=[
                _FakeMessage(
                    [
                        _FakeOutputText(
                            parsed=None,
                        )
                    ]
                )
            ],
        )

        provider = OpenAIAgentProvider(
            client=_FakeClient(
                response
            ),  # type: ignore[arg-type]
            model="gpt-5.6-terra",
        )

        with self.assertRaises(
            AIAgentResponseError,
        ):
            await provider.generate(
                _provider_request()
            )

    async def test_invalid_parsed_output_is_rejected(
        self,
    ) -> None:
        response = _FakeResponse(
            status="completed",
            output=[
                _FakeMessage(
                    [
                        _FakeOutputText(
                            parsed={
                                "status": "completed",
                                "summary": "",
                            },
                        )
                    ]
                )
            ],
        )

        provider = OpenAIAgentProvider(
            client=_FakeClient(
                response
            ),  # type: ignore[arg-type]
            model="gpt-5.6-terra",
        )

        with self.assertRaises(
            AIAgentResponseError,
        ):
            await provider.generate(
                _provider_request()
            )

    async def test_multiple_structured_outputs_are_rejected(
        self,
    ) -> None:
        first = AIAgentStructuredOutput(
            status="completed",
            summary="First result.",
        )

        second = AIAgentStructuredOutput(
            status="completed",
            summary="Second result.",
        )

        response = _FakeResponse(
            status="completed",
            output=[
                _FakeMessage(
                    [
                        _FakeOutputText(
                            parsed=first,
                        ),
                        _FakeOutputText(
                            parsed=second,
                        ),
                    ]
                )
            ],
        )

        provider = OpenAIAgentProvider(
            client=_FakeClient(
                response
            ),  # type: ignore[arg-type]
            model="gpt-5.6-terra",
        )

        with self.assertRaises(
            AIAgentResponseError,
        ):
            await provider.generate(
                _provider_request()
            )

    async def test_non_message_output_is_ignored_safely(
        self,
    ) -> None:
        response = _FakeResponse(
            status="completed",
            output=[
                _FakeOtherOutput(),
                _FakeMessage(
                    [
                        _FakeOutputText(
                            parsed=AIAgentStructuredOutput(
                                status="completed",
                                summary="Valid result.",
                            )
                        )
                    ]
                ),
            ],
        )

        provider = OpenAIAgentProvider(
            client=_FakeClient(
                response
            ),  # type: ignore[arg-type]
            model="gpt-5.6-terra",
        )

        result = await provider.generate(
            _provider_request()
        )

        self.assertEqual(
            result.summary,
            "Valid result.",
        )

    async def test_sdk_error_is_sanitized(
        self,
    ) -> None:
        secret_detail = (
            "secret provider response body"
        )

        provider = OpenAIAgentProvider(
            client=_FakeClient(
                error=OpenAIError(
                    secret_detail
                )
            ),  # type: ignore[arg-type]
            model="gpt-5.6-terra",
        )

        with self.assertRaises(
            AIAgentProviderError,
        ) as raised:
            await provider.generate(
                _provider_request()
            )

        self.assertEqual(
            str(raised.exception),
            (
                "OpenAI provider could not "
                "complete the request"
            ),
        )

        self.assertNotIn(
            secret_detail,
            str(raised.exception),
        )

    async def test_unexpected_error_is_sanitized(
        self,
    ) -> None:
        secret_detail = (
            "private API key and HTTP detail"
        )

        provider = OpenAIAgentProvider(
            client=_FakeClient(
                error=RuntimeError(
                    secret_detail
                )
            ),  # type: ignore[arg-type]
            model="gpt-5.6-terra",
        )

        with self.assertRaises(
            AIAgentProviderError,
        ) as raised:
            await provider.generate(
                _provider_request()
            )

        self.assertEqual(
            str(raised.exception),
            (
                "OpenAI provider could not "
                "complete the request"
            ),
        )

        self.assertNotIn(
            secret_detail,
            str(raised.exception),
        )

    async def test_sdk_validation_error_becomes_response_error(
        self,
    ) -> None:
        try:
            AIAgentStructuredOutput(
                status="completed",
                summary="",
            )
        except ValidationError as exc:
            validation_error = exc
        else:
            self.fail(
                "Expected schema validation failure"
            )

        provider = OpenAIAgentProvider(
            client=_FakeClient(
                error=validation_error,
            ),  # type: ignore[arg-type]
            model="gpt-5.6-terra",
        )

        with self.assertRaises(
            AIAgentResponseError,
        ):
            await provider.generate(
                _provider_request()
            )


class OpenAIProviderMetadataTests(
    unittest.IsolatedAsyncioTestCase,
):
    async def test_generate_with_metadata_returns_safe_provider_metadata(
        self,
    ) -> None:
        response = _successful_response(
            request_id="req_openai_123",
            input_tokens=1450,
            output_tokens=320,
        )

        provider = OpenAIAgentProvider(
            client=_FakeClient(
                response
            ),  # type: ignore[arg-type]
            model="gpt-5.6-terra",
        )

        result = await provider.generate_with_metadata(
            _provider_request()
        )

        self.assertEqual(
            result.output.status,
            "completed",
        )

        self.assertEqual(
            result.output.summary,
            "Safe structured result.",
        )

        self.assertEqual(
            result.metadata.provider_request_id,
            "req_openai_123",
        )

        self.assertEqual(
            result.metadata.input_tokens,
            1450,
        )

        self.assertEqual(
            result.metadata.output_tokens,
            320,
        )

    async def test_request_id_is_trimmed(
        self,
    ) -> None:
        response = _successful_response(
            request_id="  req_trimmed_456  ",
            input_tokens=10,
            output_tokens=20,
        )

        provider = OpenAIAgentProvider(
            client=_FakeClient(
                response
            ),  # type: ignore[arg-type]
            model="gpt-5.6-terra",
        )

        result = await provider.generate_with_metadata(
            _provider_request()
        )

        self.assertEqual(
            result.metadata.provider_request_id,
            "req_trimmed_456",
        )

    async def test_missing_provider_metadata_is_allowed(
        self,
    ) -> None:
        response = _successful_response()

        provider = OpenAIAgentProvider(
            client=_FakeClient(
                response
            ),  # type: ignore[arg-type]
            model="gpt-5.6-terra",
        )

        result = await provider.generate_with_metadata(
            _provider_request()
        )

        self.assertIsNone(
            result.metadata.provider_request_id,
        )

        self.assertIsNone(
            result.metadata.input_tokens,
        )

        self.assertIsNone(
            result.metadata.output_tokens,
        )

        self.assertEqual(
            result.output.status,
            "completed",
        )

    async def test_invalid_request_id_is_ignored_safely(
        self,
    ) -> None:
        cases = (
            "",
            "   ",
            "x" * 256,
            12345,
        )

        for request_id in cases:
            with self.subTest(
                request_id=request_id,
            ):
                response = _successful_response(
                    request_id=request_id,
                    input_tokens=10,
                    output_tokens=20,
                )

                provider = OpenAIAgentProvider(
                    client=_FakeClient(
                        response
                    ),  # type: ignore[arg-type]
                    model="gpt-5.6-terra",
                )

                result = (
                    await provider.generate_with_metadata(
                        _provider_request()
                    )
                )

                self.assertIsNone(
                    result.metadata.provider_request_id,
                )

                self.assertEqual(
                    result.output.status,
                    "completed",
                )

    async def test_invalid_token_metadata_is_ignored_safely(
        self,
    ) -> None:
        cases = (
            (
                -1,
                20,
                None,
                20,
            ),
            (
                10,
                -1,
                10,
                None,
            ),
            (
                True,
                20,
                None,
                20,
            ),
            (
                10,
                False,
                10,
                None,
            ),
            (
                "100",
                20,
                None,
                20,
            ),
            (
                10,
                "50",
                10,
                None,
            ),
        )

        for (
            input_tokens,
            output_tokens,
            expected_input,
            expected_output,
        ) in cases:
            with self.subTest(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            ):
                response = _successful_response(
                    request_id="req_safe_metadata",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )

                provider = OpenAIAgentProvider(
                    client=_FakeClient(
                        response
                    ),  # type: ignore[arg-type]
                    model="gpt-5.6-terra",
                )

                result = (
                    await provider.generate_with_metadata(
                        _provider_request()
                    )
                )

                self.assertEqual(
                    result.metadata.input_tokens,
                    expected_input,
                )

                self.assertEqual(
                    result.metadata.output_tokens,
                    expected_output,
                )

                self.assertEqual(
                    result.output.status,
                    "completed",
                )

    async def test_generate_remains_backward_compatible(
        self,
    ) -> None:
        response = _successful_response(
            request_id="req_backward_compatible",
            input_tokens=100,
            output_tokens=25,
        )

        provider = OpenAIAgentProvider(
            client=_FakeClient(
                response
            ),  # type: ignore[arg-type]
            model="gpt-5.6-terra",
        )

        result = await provider.generate(
            _provider_request()
        )

        self.assertIsInstance(
            result,
            AIAgentStructuredOutput,
        )

        self.assertEqual(
            result.status,
            "completed",
        )

        self.assertEqual(
            result.summary,
            "Safe structured result.",
        )

        self.assertFalse(
            hasattr(
                result,
                "metadata",
            )
        )


class _FakeOutputText:
    type = "output_text"

    def __init__(
        self,
        *,
        parsed,
    ) -> None:
        self.parsed = parsed


class _FakeOtherContent:
    type = "refusal"


class _FakeMessage:
    type = "message"

    def __init__(
        self,
        content,
    ) -> None:
        self.content = content


class _FakeOtherOutput:
    type = "tool_call"


class _FakeUsage:
    def __init__(
        self,
        *,
        input_tokens=None,
        output_tokens=None,
    ) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeResponse:
    def __init__(
        self,
        *,
        status: str,
        output,
        request_id=None,
        usage=None,
    ) -> None:
        self.status = status
        self.output = output
        self._request_id = request_id
        self.usage = usage


class _FakeResponses:
    def __init__(
        self,
        response=None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.kwargs = None

    async def parse(
        self,
        **kwargs,
    ):
        self.kwargs = kwargs

        if self.error is not None:
            raise self.error

        return self.response


class _FakeClient:
    def __init__(
        self,
        response=None,
        *,
        responses=None,
        error: Exception | None = None,
    ) -> None:
        if responses is not None:
            self.responses = responses
        else:
            self.responses = _FakeResponses(
                response,
                error=error,
            )


def _successful_response(
    *,
    request_id=None,
    input_tokens=None,
    output_tokens=None,
) -> _FakeResponse:
    output = AIAgentStructuredOutput(
        status="completed",
        summary="Safe structured result.",
        recommendations=[
            "Continue with the controlled workflow.",
        ],
    )

    usage = None

    if (
        input_tokens is not None
        or output_tokens is not None
    ):
        usage = _FakeUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    return _FakeResponse(
        status="completed",
        output=[
            _FakeMessage(
                [
                    _FakeOutputText(
                        parsed=output,
                    )
                ]
            )
        ],
        request_id=request_id,
        usage=usage,
    )


def _provider_request() -> AIAgentProviderRequest:
    business_id = uuid4()

    context = AIContextBundle(
        business_id=business_id,
        purpose="sales",
        task="Recommend next step.",
        sources=[],
        source_count=0,
        business_brain_source_count=0,
        memory_source_count=0,
        revision=sha256().hexdigest(),
    )

    return AIAgentProviderRequest(
        business_id=business_id,
        role="sales",
        system_instructions=(
            "Role: AI Sales. Use trusted context only."
        ),
        task=(
            "# Task\n"
            "Recommend next step.\n\n"
            "# Trusted Context\n"
            "No trusted sources matched."
        ),
        context=context,
    )


def _settings(
    *,
    openai_api_key: SecretStr | None,
    openai_model: str = "gpt-5.6-terra",
    openai_timeout_seconds: float = 45.0,
    openai_max_retries: int = 2,
) -> Settings:
    return Settings(
        _env_file=None,
        database_url=SecretStr(
            "postgresql+asyncpg://database.invalid/test"
        ),
        auth_secret_key=SecretStr(
            "x" * 32
        ),
        openai_api_key=openai_api_key,
        openai_model=openai_model,
        openai_timeout_seconds=openai_timeout_seconds,
        openai_max_retries=openai_max_retries,
    )