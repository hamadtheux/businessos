from __future__ import annotations

from typing import Annotated, Awaitable
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.business import (
    BusinessAccessDependency,
    require_business_role,
)
from app.api.response_materialization import materialize_response_before_commit
from app.db.session import get_db_session
from app.exceptions.growth_learning import (
    GrowthLearningNotFoundError,
    GrowthLearningPersistenceError,
    GrowthLearningStateError,
    GrowthLearningValidationError,
)
from app.schemas.growth_learning import (
    GrowthExperimentCreate,
    GrowthExperimentPage,
    GrowthExperimentResponse,
    GrowthExperimentResultResponse,
    GrowthExperimentStatus,
    GrowthExperimentUpdate,
    GrowthLearningPage,
)
from app.services import growth_learning as service
from app.services.billing import require_feature


router = APIRouter(
    prefix="/businesses/{business_id}/growth",
    tags=["Growth Learning"],
)
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]
Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]
_PRIVATE_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}


@router.get("/experiments", response_model=GrowthExperimentPage)
async def read_growth_experiments(
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
    page: Page = 1,
    page_size: PageSize = 25,
    experiment_status: Annotated[
        GrowthExperimentStatus | None, Query(alias="status")
    ] = None,
):
    await _guard(session, access.business.id)
    items, total = await _read(
        service.list_growth_experiments(
            session,
            business_id=access.business.id,
            page=page,
            page_size=page_size,
            status=experiment_status,
        )
    )
    _private(response)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post(
    "/experiments",
    response_model=GrowthExperimentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_growth_experiment(
    data: GrowthExperimentCreate,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
):
    require_business_role(access)
    await _guard(session, access.business.id)
    return await _mutate(
        response,
        session,
        service.create_growth_experiment(
            session,
            business_id=access.business.id,
            actor_user_id=access.user.id,
            data=data,
        ),
    )


@router.get(
    "/experiments/{experiment_id}", response_model=GrowthExperimentResponse
)
async def read_growth_experiment(
    experiment_id: UUID,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
):
    await _guard(session, access.business.id)
    value = await _read(
        service.get_growth_experiment(
            session,
            business_id=access.business.id,
            experiment_id=experiment_id,
        )
    )
    _private(response)
    return value


@router.patch(
    "/experiments/{experiment_id}", response_model=GrowthExperimentResponse
)
async def patch_growth_experiment(
    experiment_id: UUID,
    data: GrowthExperimentUpdate,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
):
    require_business_role(access)
    await _guard(session, access.business.id)
    return await _mutate(
        response,
        session,
        service.update_growth_experiment(
            session,
            business_id=access.business.id,
            experiment_id=experiment_id,
            actor_user_id=access.user.id,
            data=data,
        ),
    )


@router.post(
    "/experiments/{experiment_id}/ready", response_model=GrowthExperimentResponse
)
async def ready_growth_experiment(
    experiment_id: UUID,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
):
    return await _transition(
        experiment_id,
        access,
        response,
        session,
        service.mark_growth_experiment_ready,
    )


@router.post(
    "/experiments/{experiment_id}/start", response_model=GrowthExperimentResponse
)
async def start_growth_experiment(
    experiment_id: UUID,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
):
    return await _transition(
        experiment_id,
        access,
        response,
        session,
        service.start_growth_experiment,
    )


@router.post(
    "/experiments/{experiment_id}/complete",
    response_model=GrowthExperimentResponse,
)
async def complete_growth_experiment(
    experiment_id: UUID,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
):
    return await _transition(
        experiment_id,
        access,
        response,
        session,
        service.complete_growth_experiment,
    )


@router.post(
    "/experiments/{experiment_id}/cancel", response_model=GrowthExperimentResponse
)
async def cancel_growth_experiment(
    experiment_id: UUID,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
):
    return await _transition(
        experiment_id,
        access,
        response,
        session,
        service.cancel_growth_experiment,
    )


@router.post(
    "/experiments/{experiment_id}/evaluate",
    response_model=GrowthExperimentResultResponse,
)
async def evaluate_growth_experiment(
    experiment_id: UUID,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
):
    require_business_role(access)
    await _guard(session, access.business.id)
    return await _mutate(
        response,
        session,
        service.evaluate_growth_experiment(
            session,
            business_id=access.business.id,
            experiment_id=experiment_id,
            actor_user_id=access.user.id,
        ),
    )


@router.get("/learnings", response_model=GrowthLearningPage)
async def read_growth_learnings(
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
    page: Page = 1,
    page_size: PageSize = 25,
):
    await _guard(session, access.business.id)
    items, total = await _read(
        service.list_growth_learnings(
            session,
            business_id=access.business.id,
            page=page,
            page_size=page_size,
        )
    )
    _private(response)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def _transition(
    experiment_id: UUID,
    access,
    response: Response,
    session: AsyncSession,
    operation,
):
    require_business_role(access)
    await _guard(session, access.business.id)
    return await _mutate(
        response,
        session,
        operation(
            session,
            business_id=access.business.id,
            experiment_id=experiment_id,
            actor_user_id=access.user.id,
        ),
    )


async def _guard(session: AsyncSession, business_id: UUID) -> None:
    if isinstance(session, AsyncSession):
        await require_feature(session, business_id=business_id, key="advanced_analytics")


async def _read(operation: Awaitable):
    try:
        return await operation
    except GrowthLearningNotFoundError:
        raise _not_found() from None
    except GrowthLearningValidationError as error:
        raise _invalid(str(error)) from None
    except GrowthLearningStateError:
        raise _conflict() from None
    except GrowthLearningPersistenceError:
        raise _unavailable() from None


async def _mutate(
    response: Response,
    session: AsyncSession,
    operation: Awaitable,
):
    try:
        value = await operation
        await materialize_response_before_commit(session, value)
        await session.commit()
    except GrowthLearningNotFoundError:
        await _rollback(session)
        raise _not_found() from None
    except GrowthLearningValidationError as error:
        await _rollback(session)
        raise _invalid(str(error)) from None
    except GrowthLearningStateError:
        await _rollback(session)
        raise _conflict() from None
    except (GrowthLearningPersistenceError, SQLAlchemyError):
        await _rollback(session)
        raise _unavailable() from None
    _private(response)
    return value


async def _rollback(session: AsyncSession) -> None:
    try:
        await session.rollback()
    except SQLAlchemyError:
        pass


def _private(response: Response) -> None:
    for key, value in _PRIVATE_HEADERS.items():
        response.headers[key] = value


def _not_found() -> HTTPException:
    return HTTPException(404, "Growth experiment not found.", headers=_PRIVATE_HEADERS)


def _invalid(reason: str) -> HTTPException:
    safe_messages = {
        "all experiment variants must use one currency": (
            "Experiment variants must use one currency."
        ),
        "variant campaign is invalid": "Choose campaigns owned by this business.",
        "variant content is invalid": "Choose content owned by this business.",
        "variant content must belong to its campaign": (
            "Each content variant must belong to its selected campaign."
        ),
    }
    return HTTPException(
        422,
        {
            "code": "growth_experiment_invalid",
            "message": safe_messages.get(
                reason, "Check the experiment definition and evidence requirements."
            ),
        },
        headers=_PRIVATE_HEADERS,
    )


def _conflict() -> HTTPException:
    return HTTPException(
        409,
        {
            "code": "growth_experiment_state_conflict",
            "message": "The experiment cannot make that lifecycle transition.",
        },
        headers=_PRIVATE_HEADERS,
    )


def _unavailable() -> HTTPException:
    return HTTPException(
        503,
        {
            "code": "growth_learning_unavailable",
            "message": "Growth learning is temporarily unavailable. Please try again.",
        },
        headers=_PRIVATE_HEADERS,
    )
