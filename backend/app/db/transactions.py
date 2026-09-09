from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession


logger = logging.getLogger("aibos.db")


async def rollback_session(session: AsyncSession) -> None:
    """Return a session to a usable transaction state after persistence failure."""
    rollback = getattr(session, "rollback", None)
    if rollback is None:
        return
    try:
        await rollback()
    except Exception as error:
        # Never replace the primary persistence error with rollback details.
        logger.error(
            "session_rollback_failed exception_type=%s",
            type(error).__name__,
            extra={"exception_type": type(error).__name__},
        )
