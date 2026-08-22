from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.business import BusinessAccessDependency
from app.db.session import get_db_session
from app.exceptions.business_brain import BusinessBrainAssemblyError
from app.schemas.business_brain import BusinessBrainManifestResponse
from app.services.business_brain_assembly import (
    BusinessBrainManifest,
    build_business_brain_manifest,
)

router = APIRouter(
    prefix="/businesses/{business_id}/brain",
    tags=["Business Brain"],
)
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


@router.get(
    "/manifest",
    response_model=BusinessBrainManifestResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Business Brain manifest is temporarily unavailable."
        }
    },
)
async def read_business_brain_manifest(
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> BusinessBrainManifest:
    try:
        manifest = await build_business_brain_manifest(
            session,
            access.business.id,
        )
    except BusinessBrainAssemblyError:
        raise _assembly_unavailable_exception() from None
    _set_private_response_headers(response)
    return manifest


def _set_private_response_headers(response: Response) -> None:
    for name, value in _PRIVATE_RESPONSE_HEADERS.items():
        response.headers[name] = value


def _assembly_unavailable_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Business Brain manifest is temporarily unavailable.",
        headers=_PRIVATE_RESPONSE_HEADERS,
    )


_PRIVATE_RESPONSE_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
}
