from __future__ import annotations

import base64
import logging

from openai import AsyncOpenAI, OpenAIError
from pydantic import ValidationError

from app.agents.provider import AIAgentProviderMetadata
from app.services.creative_visual_review import (
    CreativeVisualReview,
    CreativeVisualReviewProviderError,
    CreativeVisualReviewRequest,
    CreativeVisualReviewResult,
    build_visual_review_task,
)


logger = logging.getLogger("aibos.creative_visual_review_openai")


class OpenAICreativeVisualReviewProvider:
    """OpenAI Responses adapter for one transient, typed final-image review."""

    provider_name = "openai_vision"

    def __init__(
        self,
        *,
        client: AsyncOpenAI,
        model: str,
        max_output_tokens: int,
    ) -> None:
        normalized_model = model.strip()
        if not normalized_model:
            raise ValueError("Creative visual-review model cannot be blank")
        if not 500 <= max_output_tokens <= 2_500:
            raise ValueError("Creative visual-review output limit is invalid")
        self._client = client
        self._model = normalized_model
        self._max_output_tokens = max_output_tokens

    async def review(
        self,
        request: CreativeVisualReviewRequest,
    ) -> CreativeVisualReviewResult:
        image_url = (
            "data:image/png;base64,"
            + base64.b64encode(request.final_png).decode("ascii")
        )
        try:
            response = await self._client.responses.parse(
                model=self._model,
                input=[
                    {
                        "role": "developer",
                        "content": (
                            "You are a visual QA gate. Return only the requested "
                            "structured assessment. Never reproduce hidden reasoning."
                        ),
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": build_visual_review_task(request)},
                            {
                                "type": "input_image",
                                "image_url": image_url,
                                "detail": "high",
                            },
                        ],
                    },
                ],
                text_format=CreativeVisualReview,
                max_output_tokens=self._max_output_tokens,
                store=False,
            )
        except (OpenAIError, ValidationError, TypeError, ValueError):
            logger.warning(
                "creative_visual_review_provider_failed provider=openai_vision model=%s",
                _safe_model(self._model),
                extra={"provider": self.provider_name, "model": _safe_model(self._model)},
            )
            raise CreativeVisualReviewProviderError(
                "Creative visual review provider could not complete the request"
            ) from None
        except Exception:
            raise CreativeVisualReviewProviderError(
                "Creative visual review provider could not complete the request"
            ) from None

        if getattr(response, "status", None) != "completed":
            raise CreativeVisualReviewProviderError(
                "Creative visual review provider returned an incomplete response"
            )
        parsed_values: list[CreativeVisualReview] = []
        for output in getattr(response, "output", ()):
            if getattr(output, "type", None) != "message":
                continue
            for content in getattr(output, "content", ()):
                if getattr(content, "type", None) != "output_text":
                    continue
                parsed = getattr(content, "parsed", None)
                if parsed is None:
                    continue
                try:
                    parsed_values.append(CreativeVisualReview.model_validate(parsed))
                except ValidationError:
                    raise CreativeVisualReviewProviderError(
                        "Creative visual review provider returned invalid structured output"
                    ) from None
        if len(parsed_values) != 1:
            raise CreativeVisualReviewProviderError(
                "Creative visual review provider returned ambiguous structured output"
            )
        usage = getattr(response, "usage", None)
        return CreativeVisualReviewResult(
            review=parsed_values[0],
            metadata=AIAgentProviderMetadata(
                provider_request_id=_safe_request_id(
                    getattr(response, "_request_id", None)
                ),
                input_tokens=_safe_token_count(getattr(usage, "input_tokens", None)),
                output_tokens=_safe_token_count(getattr(usage, "output_tokens", None)),
            ),
        )


def _safe_model(value: str) -> str:
    return (
        value
        if len(value) <= 128 and value.replace("-", "").replace(".", "").isalnum()
        else "unknown"
    )


def _safe_request_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized if normalized and len(normalized) <= 255 else None


def _safe_token_count(value: object) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return None
    return value
