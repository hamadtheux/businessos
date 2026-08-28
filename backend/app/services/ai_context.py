from __future__ import annotations

from hashlib import sha256
from typing import Final
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions.ai_context import AIContextAssemblyError
from app.exceptions.business_brain import BusinessBrainAssemblyError
from app.models.business_memory import BusinessMemory
from app.schemas.ai_context import (
    AIContextBundle,
    AIContextRequest,
    AIContextSource,
    BusinessBrainContextSource,
    BusinessMemoryContextSource,
)
from app.services.business_brain_assembly import (
    DEFAULT_SOURCE_BATCH_SIZE,
    iterate_business_brain_sources,
)


_ASSEMBLY_MESSAGE: Final = "Unable to assemble AI business context"


async def assemble_ai_context(
    session: AsyncSession,
    business_id: UUID,
    request: AIContextRequest,
) -> AIContextBundle:
    """
    Assemble bounded trusted context for one business and one AI task.

    Context is composed from two authoritative tenant-scoped sources:

    1. Business Brain
       Current business profile, branding, active catalog, and active curated
       knowledge.

    2. Persistent Business Memory
       Active learned memories selected using explicit importance/confidence
       thresholds and optional memory-type filters.

    This function is deliberately read-only. It performs no commits, flushes,
    reinforcement, deduplication, embeddings, or LLM calls.
    """
    sources: list[AIContextSource] = []

    if request.include_business_brain:
        brain_sources = await _assemble_business_brain_sources(
            session,
            business_id,
            request,
        )
        sources.extend(brain_sources)

    if request.include_memory:
        memory_sources = await _assemble_memory_sources(
            session,
            business_id,
            request,
        )
        sources.extend(memory_sources)

    business_brain_source_count = sum(
        source.origin == "business_brain"
        for source in sources
    )

    memory_source_count = sum(
        source.origin == "business_memory"
        for source in sources
    )

    return AIContextBundle(
        business_id=business_id,
        purpose=request.purpose,
        task=request.task,
        sources=sources,
        source_count=len(sources),
        business_brain_source_count=business_brain_source_count,
        memory_source_count=memory_source_count,
        revision=_build_context_revision(sources),
    )


async def _assemble_business_brain_sources(
    session: AsyncSession,
    business_id: UUID,
    request: AIContextRequest,
) -> list[BusinessBrainContextSource]:
    allowed_source_types = (
        set(request.brain_source_types)
        if request.brain_source_types is not None
        else None
    )

    selected: list[BusinessBrainContextSource] = []

    try:
        async for source in iterate_business_brain_sources(
            session,
            business_id,
            batch_size=DEFAULT_SOURCE_BATCH_SIZE,
            allowed_source_types=allowed_source_types,
        ):
            if source.business_id != business_id:
                raise AIContextAssemblyError(_ASSEMBLY_MESSAGE)

            selected.append(
                BusinessBrainContextSource(
                    business_id=business_id,
                    source_type=source.source_type,
                    source_id=source.source_id,
                    title=source.title,
                    content=source.content,
                    updated_at=source.updated_at,
                    content_hash=source.content_hash,
                )
            )

            if len(selected) >= request.brain_source_limit:
                break

    except AIContextAssemblyError:
        raise
    except BusinessBrainAssemblyError:
        raise AIContextAssemblyError(_ASSEMBLY_MESSAGE) from None

    return selected


async def _assemble_memory_sources(
    session: AsyncSession,
    business_id: UUID,
    request: AIContextRequest,
) -> list[BusinessMemoryContextSource]:
    statement = select(BusinessMemory).where(
        BusinessMemory.business_id == business_id,
        BusinessMemory.status == "active",
        BusinessMemory.importance >= request.min_memory_importance,
        BusinessMemory.confidence >= request.min_memory_confidence,
    )

    if request.memory_types is not None:
        statement = statement.where(
            BusinessMemory.memory_type.in_(request.memory_types)
        )

    if request.purpose not in {
        "business_manager",
        "marketing",
        "sales",
        "analytics",
    }:
        # Growth learnings are aggregate commercial evidence. They are useful
        # to management, CMO, Sales, and Analytics reasoning, but irrelevant to
        # Support/Operations tasks and must not broaden those contexts.
        statement = statement.where(
            or_(
                BusinessMemory.source_reference.is_(None),
                ~BusinessMemory.source_reference.like("growth-learning:%"),
            )
        )

    statement = statement.order_by(
        BusinessMemory.importance.desc(),
        BusinessMemory.confidence.desc(),
        BusinessMemory.updated_at.desc(),
        BusinessMemory.id.desc(),
    ).limit(request.memory_limit)

    try:
        result = await session.scalars(statement)
        memories = list(result.all())
    except SQLAlchemyError:
        raise AIContextAssemblyError(_ASSEMBLY_MESSAGE) from None

    if not all(
        isinstance(memory, BusinessMemory)
        and memory.business_id == business_id
        and memory.status == "active"
        for memory in memories
    ):
        raise AIContextAssemblyError(_ASSEMBLY_MESSAGE)

    return [
        BusinessMemoryContextSource(
            business_id=business_id,
            memory_id=memory.id,
            memory_type=memory.memory_type,
            content=memory.content,
            importance=memory.importance,
            confidence=memory.confidence,
            occurred_at=memory.occurred_at,
            updated_at=memory.updated_at,
            content_hash=memory.content_hash,
        )
        for memory in memories
    ]


def _build_context_revision(
    sources: list[AIContextSource],
) -> str:
    """
    Build a deterministic revision for the exact selected source collection.

    Execution metadata such as task text and AI role/purpose is intentionally
    excluded. The revision identifies trusted business context, not the request
    that caused it to be assembled.
    """
    revision_hasher = sha256()

    for source in sources:
        revision_hasher.update(
            source.origin.encode("utf-8")
        )
        revision_hasher.update(b"\x00")

        if source.origin == "business_brain":
            source_identity = source.source_id
        else:
            source_identity = str(source.memory_id)

        revision_hasher.update(
            source_identity.encode("utf-8")
        )
        revision_hasher.update(b"\x00")

        revision_hasher.update(
            source.content_hash.encode("ascii")
        )
        revision_hasher.update(b"\n")

    return revision_hasher.hexdigest()
