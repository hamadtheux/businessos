from __future__ import annotations

from typing import Final

from app.exceptions.ai_agent import AIAgentContextError
from app.schemas.ai_context import AIContextBundle


_CONTEXT_ERROR_MESSAGE: Final = "Unable to render trusted AI context"


def render_ai_context(
    context: AIContextBundle,
) -> str:
    """
    Render one trusted AIContextBundle into deterministic provider-safe text.

    The renderer intentionally includes only fields already approved by the
    AI Context contract.

    It does not expose:
    - raw database records
    - source_reference
    - storage keys
    - credentials
    - internal provider metadata
    - archived/superseded memory state
    - hidden reasoning

    Source boundaries are explicit so model providers can distinguish
    authoritative Business Brain content from learned Persistent Memory.
    """
    try:
        sections: list[str] = [
            "# Trusted Business Context",
            "",
            f"Business ID: {context.business_id}",
            f"Context revision: {context.revision}",
            f"Total trusted sources: {context.source_count}",
            "",
        ]

        if not context.sources:
            sections.extend(
                [
                    "No trusted business sources matched this request.",
                    "",
                ]
            )

            return "\n".join(sections).rstrip()

        for index, source in enumerate(
            context.sources,
            start=1,
        ):
            sections.append(
                f"## Source {index}"
            )

            if source.origin == "business_brain":
                sections.extend(
                    [
                        "Origin: Business Brain",
                        f"Source type: {source.source_type}",
                        f"Title: {source.title}",
                        f"Source ID: {source.source_id}",
                        f"Updated at: {source.updated_at.isoformat()}",
                        "",
                        source.content,
                        "",
                    ]
                )

                continue

            if source.origin == "business_memory":
                sections.extend(
                    [
                        "Origin: Persistent Business Memory",
                        f"Memory type: {source.memory_type}",
                        f"Memory ID: {source.memory_id}",
                        f"Importance: {source.importance}/5",
                        f"Confidence: {source.confidence}",
                    ]
                )

                if source.occurred_at is not None:
                    sections.append(
                        f"Occurred at: {source.occurred_at.isoformat()}"
                    )

                sections.extend(
                    [
                        f"Updated at: {source.updated_at.isoformat()}",
                        "",
                        source.content,
                        "",
                    ]
                )

                continue

            raise AIAgentContextError(
                _CONTEXT_ERROR_MESSAGE
            )

        return "\n".join(sections).rstrip()

    except AIAgentContextError:
        raise

    except Exception:
        raise AIAgentContextError(
            _CONTEXT_ERROR_MESSAGE
        ) from None


def build_provider_task_message(
    *,
    task: str,
    rendered_context: str,
) -> str:
    """
    Build the provider-neutral user/task message.

    Instructions remain separate from trusted business context so untrusted
    task text cannot silently become authoritative Business Brain knowledge.
    """
    normalized_task = task.strip()

    if not normalized_task:
        raise AIAgentContextError(
            "Agent task cannot be blank"
        )

    normalized_context = rendered_context.strip()

    if not normalized_context:
        raise AIAgentContextError(
            "Rendered AI context cannot be blank"
        )

    return (
        "# Task\n"
        f"{normalized_task}\n\n"
        "# Trusted Context\n"
        f"{normalized_context}\n\n"
        "# Response Requirement\n"
        "Use only the trusted context above when making factual business claims. "
        "Clearly identify missing or uncertain information. "
        "Return conclusions, recommendations, and proposed actions only. "
        "Do not claim that any proposed action has already been executed."
    )