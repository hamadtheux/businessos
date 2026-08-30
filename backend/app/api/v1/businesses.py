import logging
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import CurrentUserDependency
from app.api.dependencies.business import BusinessAccessDependency, require_business_role
from app.db.session import get_db_session
from app.exceptions.business import (
    BusinessBrandingPersistenceError,
    BusinessListingPersistenceError,
    BusinessOnboardingConflictError,
    BusinessOnboardingPersistenceError,
    BusinessProfilePersistenceError,
)
from app.exceptions.logo import (
    BusinessLogoPersistenceError,
    LogoTooLargeError,
    LogoValidationError,
)
from app.schemas.business import (
    BusinessBrandingResponse,
    BusinessBrandingUpdate,
    BusinessOnboardingInput,
    BusinessOnboardingResponse,
    BusinessProfileUpdate,
    BusinessSummary,
)
from app.services.business import (
    CreatedBusinessContext,
    create_business_from_onboarding,
    list_accessible_businesses,
    update_business_profile,
)
from app.services.business_branding import (
    get_business_branding,
    update_business_branding,
)
from app.services.business_logo import (
    PreparedLogoUpload,
    cleanup_storage_object,
    prepare_business_logo_deletion,
    prepare_business_logo_upload,
)
from app.services.automation_intelligence import (
    schedule_competitor_discovery,
    schedule_marketing_automation,
)
from app.services.logo_image import read_and_sanitize_logo
from app.services.operations import record_audit
from app.storage.base import ObjectStorage, StorageError
from app.storage.factory import ObjectStorageDependency


router = APIRouter(prefix="/businesses", tags=["Businesses"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]
LogoFile = Annotated[
    UploadFile,
    File(description="PNG, JPEG, or WebP business logo up to 5 MB."),
]
logger = logging.getLogger(__name__)


@router.post(
    "",
    response_model=BusinessOnboardingResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_200_OK: {
            "model": BusinessOnboardingResponse,
            "description": "Existing business returned for an exact retry.",
        },
        status.HTTP_409_CONFLICT: {
            "description": "Business onboarding conflict.",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Business onboarding is temporarily unavailable.",
        },
    },
)
async def create_business(
    onboarding: BusinessOnboardingInput,
    response: Response,
    current_user: CurrentUserDependency,
    session: SessionDependency,
) -> BusinessOnboardingResponse:
    # CurrentUserDependency has already autobegun the shared session transaction.
    # This route owns its completion so no success response precedes the commit.
    try:
        context = await create_business_from_onboarding(
            session,
            current_user.id,
            onboarding,
        )
        if context.created and isinstance(session, AsyncSession):
            await schedule_competitor_discovery(
                session,
                business_id=context.business.id,
                trigger_type="onboarding",
            )
            await schedule_marketing_automation(
                session, business_id=context.business.id, run_type="content_plan"
            )
            await schedule_marketing_automation(
                session,
                business_id=context.business.id,
                run_type="campaign_opportunities",
            )
        await session.commit()
    except BusinessOnboardingConflictError:
        if not await _rollback_safely(session):
            raise _onboarding_unavailable_exception() from None
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Business onboarding conflicts with an existing resource.",
        ) from None
    except BusinessOnboardingPersistenceError:
        await _rollback_safely(session)
        raise _onboarding_unavailable_exception() from None
    except SQLAlchemyError:
        await _rollback_safely(session)
        raise _onboarding_unavailable_exception() from None

    response.status_code = (
        status.HTTP_201_CREATED if context.created else status.HTTP_200_OK
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return _build_onboarding_response(context)


@router.get(
    "",
    response_model=list[BusinessSummary],
    status_code=status.HTTP_200_OK,
)
async def list_businesses(
    response: Response,
    current_user: CurrentUserDependency,
    session: SessionDependency,
) -> list[BusinessSummary]:
    try:
        accessible_businesses = await list_accessible_businesses(
            session,
            current_user.id,
        )
    except BusinessListingPersistenceError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "temporary_failure",
                "message": (
                    "Business data could not be loaded because the API could "
                    "not read the workspace records. Please try again."
                ),
            },
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        ) from None

    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"

    return [
        _build_business_summary(accessible.business, accessible.membership_role)
        for accessible in accessible_businesses
    ]


@router.put(
    "/{business_id}",
    response_model=BusinessSummary,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_403_FORBIDDEN: {
            "description": "Owner or administrator access is required."
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Business profile persistence is temporarily unavailable."
        },
    },
)
async def replace_business_profile(
    update: BusinessProfileUpdate,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> BusinessSummary:
    require_business_role(access)
    before = (
        f"name={access.business.name}; timezone={access.business.timezone}; "
        f"currency={access.business.currency}; locale={access.business.locale}"
    )
    try:
        business = await update_business_profile(
            session,
            business=access.business,
            update=update,
        )
        record_audit(
            session,
            business_id=business.id,
            actor_user_id=access.user.id,
            event_type="business.profile_updated",
            entity_type="business",
            entity_id=business.id,
            summary="Updated the authoritative business profile.",
            before_value=before,
            after_value=(
                f"name={business.name}; timezone={business.timezone}; "
                f"currency={business.currency}; locale={business.locale}"
            ),
        )
        await session.commit()
    except (BusinessProfilePersistenceError, SQLAlchemyError):
        await _rollback_safely(session)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "temporary_failure",
                "message": "Business profile could not be saved. Please try again.",
            },
        ) from None
    _set_private_response_headers(response)
    return _build_business_summary(business, access.membership.role)


@router.get(
    "/{business_id}/branding",
    response_model=BusinessBrandingResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Business branding is temporarily unavailable.",
        },
    },
)
async def read_business_branding(
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> BusinessBrandingResponse:
    try:
        branding = await get_business_branding(session, access.business.id)
    except BusinessBrandingPersistenceError:
        raise _branding_unavailable_exception() from None

    _set_private_response_headers(response)
    return _build_branding_response(branding)


@router.put(
    "/{business_id}/branding",
    response_model=BusinessBrandingResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Business branding is temporarily unavailable.",
        },
    },
)
async def replace_business_branding(
    update: BusinessBrandingUpdate,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> BusinessBrandingResponse:
    require_business_role(access)
    try:
        branding = await update_business_branding(
            session,
            access.business.id,
            update,
        )
        await session.commit()
    except (BusinessBrandingPersistenceError, SQLAlchemyError):
        await _rollback_safely(session)
        raise _branding_unavailable_exception() from None

    _set_private_response_headers(response)
    return _build_branding_response(branding)


@router.post(
    "/{business_id}/branding/logo",
    response_model=BusinessBrandingResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_413_CONTENT_TOO_LARGE: {
            "description": "Logo exceeds the upload limit.",
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "Logo is not a supported safe raster image.",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Business logo storage is temporarily unavailable.",
        },
    },
)
async def upload_business_logo(
    request: Request,
    file: LogoFile,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
    storage: ObjectStorageDependency,
) -> BusinessBrandingResponse:
    require_business_role(access)
    _reject_unexpected_logo_form_fields(await request.form())
    prepared: PreparedLogoUpload | None = None
    try:
        sanitized_logo = await read_and_sanitize_logo(file)
        prepared = await prepare_business_logo_upload(
            session,
            access.business.id,
            sanitized_logo,
            storage,
        )
        await session.commit()
    except LogoTooLargeError:
        raise _logo_too_large_exception() from None
    except LogoValidationError:
        raise _invalid_logo_exception() from None
    except StorageError:
        await _rollback_safely(session)
        raise _logo_storage_unavailable_exception() from None
    except (BusinessLogoPersistenceError, BusinessBrandingPersistenceError):
        await _rollback_safely(session)
        raise _branding_unavailable_exception() from None
    except SQLAlchemyError:
        await _rollback_safely(session)
        if prepared is not None:
            await _cleanup_with_generic_warning(
                storage,
                prepared.new_storage_key,
            )
        raise _branding_unavailable_exception() from None

    if (
        prepared.previous_storage_key is not None
        and prepared.previous_storage_key != prepared.new_storage_key
    ):
        await _cleanup_with_generic_warning(
            storage,
            prepared.previous_storage_key,
        )

    _set_private_response_headers(response)
    return _build_branding_response(prepared.branding)


@router.delete(
    "/{business_id}/branding/logo",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Business logo deletion is temporarily unavailable.",
        },
    },
)
async def delete_business_logo(
    access: BusinessAccessDependency,
    session: SessionDependency,
    storage: ObjectStorageDependency,
) -> Response:
    require_business_role(access)
    try:
        prepared = await prepare_business_logo_deletion(
            session,
            access.business.id,
        )
        await session.commit()
    except (BusinessLogoPersistenceError, BusinessBrandingPersistenceError):
        await _rollback_safely(session)
        raise _branding_unavailable_exception() from None
    except SQLAlchemyError:
        await _rollback_safely(session)
        raise _branding_unavailable_exception() from None

    await _cleanup_with_generic_warning(
        storage,
        prepared.previous_storage_key,
    )
    return Response(
        status_code=status.HTTP_204_NO_CONTENT,
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
        },
    )


def _build_onboarding_response(
    context: CreatedBusinessContext,
) -> BusinessOnboardingResponse:
    business = context.business
    branding = context.branding
    return BusinessOnboardingResponse(
        business=_build_business_summary(business, context.membership.role),
        branding=(
            BusinessBrandingResponse.model_validate(branding)
            if branding is not None
            else None
        ),
        created=context.created,
    )


def _build_branding_response(
    branding: object | None,
) -> BusinessBrandingResponse:
    if branding is None:
        return BusinessBrandingResponse(
            primary_color=None,
            secondary_color=None,
            accent_color=None,
            logo_url=None,
        )
    return BusinessBrandingResponse.model_validate(branding)


def _build_business_summary(business: object, membership_role: str) -> BusinessSummary:
    return BusinessSummary(
        id=business.id,
        name=business.name,
        slug=business.slug,
        business_type=business.business_type,
        status=business.status,
        timezone=business.timezone,
        currency=business.currency,
        locale=business.locale,
        website_url=getattr(business, "website_url", None),
        location=getattr(business, "location", None),
        description=getattr(business, "description", None),
        brand_voice=getattr(business, "brand_voice", None),
        avoid_keywords=list(getattr(business, "avoid_keywords", None) or []),
        membership_role=membership_role,
        created_at=business.created_at,
    )


def _set_private_response_headers(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


async def _rollback_safely(session: AsyncSession) -> bool:
    try:
        await session.rollback()
    except SQLAlchemyError:
        return False
    return True


def _onboarding_unavailable_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Business onboarding is temporarily unavailable.",
    )


def _branding_unavailable_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Business branding is temporarily unavailable.",
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
        },
    )


def _logo_too_large_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        detail="Logo must be 5 MB or smaller.",
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


def _invalid_logo_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=("Upload a valid PNG, JPEG, or WebP logo within supported dimensions."),
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


def _logo_storage_unavailable_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Business logo storage is temporarily unavailable.",
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


def _reject_unexpected_logo_form_fields(form: object) -> None:
    multi_items = getattr(form, "multi_items", None)
    if not callable(multi_items):
        raise _invalid_logo_exception()
    items = multi_items()
    if len(items) != 1 or items[0][0] != "file":
        raise _invalid_logo_exception()


async def _cleanup_with_generic_warning(
    storage: ObjectStorage,
    object_key: str | None,
) -> None:
    if object_key is None:
        return
    cleaned = await cleanup_storage_object(storage, object_key)
    if not cleaned:
        logger.warning("Deferred cleanup is required for a business logo object.")
