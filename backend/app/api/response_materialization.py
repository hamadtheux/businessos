from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.ext.asyncio import AsyncSession


async def materialize_response_before_commit(
    session: AsyncSession,
    value: Any,
) -> Any:
    """Load server-managed ORM fields before a committed response escapes.

    SQL expression ``onupdate`` values such as ``updated_at`` are expired after
    flush. Accessing them later during FastAPI serialization would otherwise
    attempt implicit async I/O outside SQLAlchemy's greenlet context.
    """
    if not isinstance(session, AsyncSession):
        return value

    await session.flush()
    seen: set[int] = set()
    for instance in _mapped_instances(value):
        identity = id(instance)
        if identity in seen:
            continue
        seen.add(identity)
        await session.refresh(instance)
    return value


def _mapped_instances(value: Any):
    if sqlalchemy_inspect(type(value), raiseerr=False) is not None:
        yield value
        return
    if isinstance(value, Mapping):
        for item in value.values():
            yield from _mapped_instances(item)
        return
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        for item in value:
            yield from _mapped_instances(item)
