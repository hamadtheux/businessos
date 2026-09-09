from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.ai_context import AIContextRequest
from app.schemas.business_brain import BusinessBrainSourceType
from app.services.ai_context import assemble_ai_context
from app.services.ai_context_policy import cmo_context_policy


_TOKEN = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "a", "about", "an", "and", "are", "as", "at", "be", "by", "for",
        "from", "in", "into", "is", "it", "of", "on", "or", "our", "that",
        "the", "their", "this", "to", "use", "using", "with", "your",
    }
)
_MAX_EVIDENCE_TOKENS: Final[int] = 4_096
_MAX_SOURCE_EVIDENCE_TOKENS: Final[int] = 512
_MAX_SOURCE_EVIDENCE_CHARS: Final[int] = 16_000
_MAX_EVIDENCE_SEGMENTS_PER_SOURCE: Final[int] = 64
_MAX_EVIDENCE_SEGMENTS: Final[int] = 256
_NEGATED_EVIDENCE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:no|not|never|without|cannot|can't|cant|doesn't|doesnt|"
    r"don't|dont|isn't|isnt|won't|wont|shouldn't|shouldnt)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class AuthoritativeCreativeContext:
    """Bounded tenant-scoped Business Brain evidence for deterministic scoring."""

    business_id: UUID
    revision: str
    source_count: int
    source_types: tuple[BusinessBrainSourceType, ...]
    evidence_tokens: frozenset[str]
    evidence_segments: tuple[frozenset[str], ...] = ()

    def supports_evidence_tokens(self, required: Collection[str]) -> bool:
        """Return whether one positive Brain segment contains all required terms."""
        required_tokens = frozenset(required)
        return bool(required_tokens) and any(
            required_tokens.issubset(segment)
            for segment in self.evidence_segments
        )


def _normalized_evidence_tokens(value: str) -> set[str]:
    tokens: set[str] = set()
    for token in _TOKEN.findall(value.casefold())[:_MAX_SOURCE_EVIDENCE_TOKENS]:
        if len(token) <= 2 or token in _STOPWORDS:
            continue
        tokens.add(token)
        if len(token) > 3 and token.endswith("s"):
            tokens.add(token[:-1])
    return tokens


def build_authoritative_creative_context(
    bundle: object,
    *,
    business_id: UUID,
) -> AuthoritativeCreativeContext:
    """Reduce an assembled Business Brain bundle to non-persisted evidence."""
    if getattr(bundle, "business_id", None) != business_id:
        raise ValueError("Creative authority belongs to a different business")
    if getattr(bundle, "memory_source_count", None) != 0:
        raise ValueError("Persistent memory cannot authorize creative capabilities")

    sources = tuple(
        source
        for source in getattr(bundle, "sources", ())
        if getattr(source, "origin", None) == "business_brain"
    )
    if len(sources) != getattr(bundle, "source_count", -1):
        raise ValueError("Creative authority contains an unexpected source count")
    if len(sources) != getattr(bundle, "business_brain_source_count", -1):
        raise ValueError("Creative authority contains an invalid source set")
    if any(getattr(source, "business_id", None) != business_id for source in sources):
        raise ValueError("Creative authority contains a cross-tenant source")

    evidence: set[str] = set()
    evidence_segments: list[frozenset[str]] = []
    source_types: list[BusinessBrainSourceType] = []
    for source in sources:
        source_types.append(source.source_type)
        if len(evidence_segments) >= _MAX_EVIDENCE_SEGMENTS:
            continue
        bounded_content = source.content[:_MAX_SOURCE_EVIDENCE_CHARS]
        for raw_segment in re.split(
            r"(?<=[.!?])\s+|[\r\n;]+",
            bounded_content,
        )[:_MAX_EVIDENCE_SEGMENTS_PER_SOURCE]:
            if _NEGATED_EVIDENCE.search(raw_segment):
                # A negated capability is never converted into positive authority.
                continue
            segment = frozenset(_normalized_evidence_tokens(raw_segment))
            if not segment:
                continue
            evidence_segments.append(segment)
            if len(evidence_segments) >= _MAX_EVIDENCE_SEGMENTS:
                break

    for segment in evidence_segments:
        if len(evidence) >= _MAX_EVIDENCE_TOKENS:
            break
        remaining = _MAX_EVIDENCE_TOKENS - len(evidence)
        evidence.update(sorted(segment)[:remaining])

    return AuthoritativeCreativeContext(
        business_id=business_id,
        revision=str(getattr(bundle, "revision", "")),
        source_count=len(sources),
        source_types=tuple(source_types),
        evidence_tokens=frozenset(evidence),
        evidence_segments=tuple(evidence_segments),
    )


async def assemble_authoritative_creative_context(
    session: AsyncSession,
    *,
    business_id: UUID,
    business_type: str,
) -> AuthoritativeCreativeContext:
    """Assemble current CMO-policy Business Brain evidence for creative scoring."""
    policy = cmo_context_policy(business_type)
    request = AIContextRequest(
        purpose="marketing",
        task="Assemble bounded Business Brain evidence for deterministic creative validation.",
        include_business_brain=True,
        include_memory=False,
        brain_source_types=(
            list(policy.brain_source_types)
            if policy.brain_source_types is not None
            else None
        ),
    )
    bundle = await assemble_ai_context(session, business_id, request)
    return build_authoritative_creative_context(bundle, business_id=business_id)
