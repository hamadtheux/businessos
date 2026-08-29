from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.business import BusinessAccessDependency
from app.db.session import get_db_session
from app.schemas.activation_readiness import ActivationReadinessResponse
from app.services.activation_readiness import activation_readiness


router = APIRouter(tags=["Production Activation"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


@router.get(
    "/businesses/{business_id}/activation-readiness",
    response_model=ActivationReadinessResponse,
)
async def read_activation_readiness(
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> ActivationReadinessResponse:
    try:
        value = await activation_readiness(session, business=access.business)
    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail="Activation readiness is temporarily unavailable.",
        ) from None
    response.headers.update({"Cache-Control": "no-store", "Pragma": "no-cache"})
    return value
