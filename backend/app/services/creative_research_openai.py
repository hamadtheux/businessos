from __future__ import annotations

import logging
from typing import TypeVar

from openai import AsyncOpenAI, OpenAIError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.services.creative_research import (
    CreativeInspirationInsight,
    CreativeResearchProviderError,
    CreativeResearchRequest,
    CreativeResearchResult,
    normalize_references,
    sanitize_reference_url,
)


logger = logging.getLogger("aibos.creative_research_openai")


class _OpenAIResearchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    references: list[CreativeInspirationInsight] = Field(
        min_length=1,
        max_length=15,
    )


T = TypeVar("T", bound=BaseModel)


class OpenAICreativeResearchProvider:
    """OpenAI Responses web-search adapter for public creative inspiration."""

    provider_name = "openai_web_search"

    def __init__(self, *, client: AsyncOpenAI, model: str) -> None:
        normalized_model = model.strip()
        if not normalized_model:
            raise ValueError("Creative research model cannot be blank")
        self._client = client
        self._model = normalized_model

    async def search(
        self,
        request: CreativeResearchRequest,
    ) -> CreativeResearchResult:
        # The input is deliberately constructed only from the request's closed,
        # public-safe vocabulary. No tenant identifier or private context is
        # available to this adapter.
        task = (
            "Research current public creative-design examples for these generalized "
            "themes. Use diversified reputable domains and public search metadata.\n"
            + "\n".join(f"- {query}" for query in request.queries)
            + "\nReturn 8-15 candidate references when available, ranked for relevance, "
            "recency, platform fit, sophistication, and uniqueness. For each source, "
            "report only a public HTTPS URL, a short title, and abstract design "
            "principles. Never reproduce source copy, coordinates, logos, brand "
            "systems, or identifiable artwork. Do not include image bytes or page text."
        )
        try:
            response = await self._client.responses.parse(
                model=self._model,
                input=[
                    {
                        "role": "developer",
                        "content": (
                            "You are a compliant visual-research analyst. Use web search "
                            "only for public inspiration. Synthesize reviewable design "
                            "conclusions, not hidden reasoning and not clone instructions."
                        ),
                    },
                    {"role": "user", "content": task},
                ],
                tools=[
                    {
                        "type": "web_search",
                        "external_web_access": True,
                        "search_context_size": "medium",
                    }
                ],
                tool_choice="required",
                include=["web_search_call.action.sources"],
                text_format=_OpenAIResearchOutput,
                max_tool_calls=3,
                max_output_tokens=3600,
                store=False,
            )
        except (OpenAIError, ValidationError, TypeError, ValueError):
            logger.warning(
                "creative_research_provider_failed provider=openai_web_search model=%s",
                _safe_model(self._model),
                extra={"provider": self.provider_name, "model": _safe_model(self._model)},
            )
            raise CreativeResearchProviderError(
                "Creative research provider could not complete the request"
            ) from None
        except Exception:
            raise CreativeResearchProviderError(
                "Creative research provider could not complete the request"
            ) from None

        if getattr(response, "status", None) != "completed":
            raise CreativeResearchProviderError(
                "Creative research provider returned an incomplete response"
            )

        parsed = _extract_single_parsed_output(response, _OpenAIResearchOutput)
        source_urls = _extract_verified_source_urls(response)
        if not source_urls:
            raise CreativeResearchProviderError(
                "Creative research provider returned no verifiable sources"
            )
        references = normalize_references(
            parsed.references,
            max_results=request.max_results,
            permitted_source_urls=source_urls,
        )
        if not references:
            raise CreativeResearchProviderError(
                "Creative research provider returned no usable references"
            )
        return CreativeResearchResult(
            provider=self.provider_name,
            references=references,
        )


def _extract_single_parsed_output(response: object, output_type: type[T]) -> T:
    parsed_values: list[T] = []
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
                parsed_values.append(output_type.model_validate(parsed))
            except ValidationError:
                raise CreativeResearchProviderError(
                    "Creative research provider returned invalid structured output"
                ) from None
    if len(parsed_values) != 1:
        raise CreativeResearchProviderError(
            "Creative research provider returned ambiguous structured output"
        )
    return parsed_values[0]


def _extract_verified_source_urls(response: object) -> set[str]:
    values: set[str] = set()
    for output in getattr(response, "output", ()):
        if getattr(output, "type", None) == "web_search_call":
            action = getattr(output, "action", None)
            for source in getattr(action, "sources", ()) or ():
                _add_safe_url(values, getattr(source, "url", None))
        if getattr(output, "type", None) != "message":
            continue
        for content in getattr(output, "content", ()):
            for annotation in getattr(content, "annotations", ()) or ():
                if getattr(annotation, "type", None) == "url_citation":
                    _add_safe_url(values, getattr(annotation, "url", None))
    return values


def _add_safe_url(values: set[str], value: object) -> None:
    if not isinstance(value, str):
        return
    try:
        values.add(sanitize_reference_url(value))
    except ValueError:
        return


def _safe_model(value: str) -> str:
    return (
        value
        if len(value) <= 128
        and value.replace("-", "").replace(".", "").isalnum()
        else "unknown"
    )
