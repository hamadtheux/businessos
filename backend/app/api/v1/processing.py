from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Awaitable, Callable, TypeVar
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.business import BusinessAccessDependency
from app.api.response_materialization import materialize_response_before_commit
from app.core.config import settings
from app.db.session import get_db_session
from app.exceptions.background_jobs import (
    BackgroundJobNotFoundError,
    BackgroundJobPersistenceError,
    BackgroundJobStateError,
    BackgroundJobValidationError,
)
from app.schemas.background_jobs import (
    BackgroundJobPageResponse,
    BackgroundJobResponse,
    JobStatus,
    JobType,
    ProcessingHealthResponse,
)
from app.services.background_jobs import (
    cancel_job,
    get_job,
    list_jobs,
    processing_health,
    retry_job,
)


router = APIRouter(
    prefix="/businesses/{business_id}/processing",
    tags=["Automation Processing"],
)
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]
T = TypeVar("T")


@router.get("/jobs", response_model=BackgroundJobPageResponse)
async def read_jobs(
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
    job_status: Annotated[JobStatus | None, Query(alias="status")] = None,
    job_type: JobType | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, object]:
    items, total = await _read(lambda: list_jobs(
        session,
        business_id=access.business.id,
        status=job_status,
        job_type=job_type,
        page=page,
        page_size=page_size,
    ))
    _private(response)
    return {"items": items, "page": page, "page_size": page_size, "total": total}


@router.get("/jobs/{job_id}", response_model=BackgroundJobResponse)
async def read_job(
    job_id: UUID,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
):
    value = await _read(lambda: get_job(
        session, business_id=access.business.id, job_id=job_id,
    ))
    _private(response)
    return value


@router.post("/jobs/{job_id}/retry", response_model=BackgroundJobResponse)
async def manually_retry_job(
    job_id: UUID,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
):
    value = await _write(session, lambda: retry_job(
        session,
        business_id=access.business.id,
        job_id=job_id,
        actor_user_id=access.user.id,
    ))
    _private(response)
    return value


@router.post("/jobs/{job_id}/cancel", response_model=BackgroundJobResponse)
async def manually_cancel_job(
    job_id: UUID,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
):
    value = await _write(session, lambda: cancel_job(
        session,
        business_id=access.business.id,
        job_id=job_id,
        actor_user_id=access.user.id,
    ))
    _private(response)
    return value


@router.get("/health", response_model=ProcessingHealthResponse)
async def read_processing_health(
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> dict[str, object]:
    value = await _read(lambda: processing_health(
        session, business_id=access.business.id,
    ))
    now = datetime.now(UTC)
    worker_fresh = _fresh(
        value["worker_last_heartbeat_at"],
        now=now,
        seconds=max(settings.worker_heartbeat_seconds * 3, 30),
    )
    scheduler_fresh = _fresh(
        value["scheduler_last_heartbeat_at"],
        now=now,
        seconds=max(int(settings.scheduler_poll_interval_seconds * 3), 30),
    )
    value["status"] = (
        "healthy" if worker_fresh and scheduler_fresh
        else "degraded" if worker_fresh or scheduler_fresh
        else "unavailable"
    )
    _private(response)
    return value


def _fresh(value: object, *, now: datetime, seconds: int) -> bool:
    return isinstance(value, datetime) and value >= now - timedelta(seconds=seconds)


async def _read(operation: Callable[[], Awaitable[T]]) -> T:
    try:
        return await operation()
    except BackgroundJobNotFoundError:
        raise HTTPException(status_code=404, detail="Processing job not found.") from None
    except BackgroundJobValidationError:
        raise HTTPException(status_code=422, detail="Processing request is invalid.") from None
    except (BackgroundJobPersistenceError, SQLAlchemyError):
        raise HTTPException(status_code=503, detail="Processing state is temporarily unavailable.") from None


async def _write(session: AsyncSession, operation: Callable[[], Awaitable[T]]) -> T:
    try:
        value = await operation()
        await materialize_response_before_commit(session, value)
        await session.commit()
        return value
    except BackgroundJobNotFoundError:
        await session.rollback()
        raise HTTPException(status_code=404, detail="Processing job not found.") from None
    except BackgroundJobStateError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Processing job state conflicts with this request.") from None
    except BackgroundJobValidationError:
        await session.rollback()
        raise HTTPException(status_code=422, detail="Processing request is invalid.") from None
    except (BackgroundJobPersistenceError, SQLAlchemyError):
        await session.rollback()
        raise HTTPException(status_code=503, detail="Processing state is temporarily unavailable.") from None


def _private(response: Response) -> None:
    response.headers.update({"Cache-Control": "no-store", "Pragma": "no-cache"})
