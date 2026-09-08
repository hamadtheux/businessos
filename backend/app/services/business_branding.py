import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions.business import BusinessBrandingPersistenceError
from app.models.business_branding import BusinessBranding
from app.schemas.business import BusinessBrandingResponse, BusinessBrandingUpdate
from app.storage.base import ObjectStorage, StorageError, validate_storage_key


_PERSISTENCE_MESSAGE = "Unable to persist business branding"


async def get_business_branding(
    session: AsyncSession,
    business_id: UUID,
) -> BusinessBranding | None:
    """Load branding for one explicitly authorized business."""
    try:
        branding = await session.scalar(
            select(BusinessBranding).where(BusinessBranding.business_id == business_id)
        )
    except SQLAlchemyError:
        raise BusinessBrandingPersistenceError(_PERSISTENCE_MESSAGE) from None

    if branding is not None and not isinstance(branding, BusinessBranding):
        raise BusinessBrandingPersistenceError(_PERSISTENCE_MESSAGE)
    return branding


async def update_business_branding(
    session: AsyncSession,
    business_id: UUID,
    update: BusinessBrandingUpdate,
) -> BusinessBranding | None:
    """Replace source colors without committing or changing the logo URL."""
    branding = await get_business_branding(session, business_id)
    colors = (
        update.primary_color,
        update.secondary_color,
        update.accent_color,
    )

    try:
        if not any(color is not None for color in colors):
            if branding is None:
                return None
            if branding.logo_url is None and branding.logo_storage_key is None:
                await session.delete(branding)
                await session.flush()
                return None

        if branding is None:
            branding = BusinessBranding(business_id=business_id)
            session.add(branding)

        branding.primary_color = update.primary_color
        branding.secondary_color = update.secondary_color
        branding.accent_color = update.accent_color
        await session.flush()
    except SQLAlchemyError:
        raise BusinessBrandingPersistenceError(_PERSISTENCE_MESSAGE) from None

    return branding


def validated_business_logo_key(
    branding: BusinessBranding | None,
    *,
    business_id: UUID,
) -> str | None:
    """Accept only a stored key in this business's flat logo directory."""
    if branding is None or branding.business_id != business_id:
        return None
    key = branding.logo_storage_key
    if not isinstance(key, str) or not key:
        return None
    try:
        path = validate_storage_key(key)
    except StorageError:
        return None
    if (
        path.parts[:-1] != ("businesses", str(business_id), "branding", "logo")
        or ".." in path.name
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", path.name) is None
    ):
        return None
    return key


def materialize_business_branding_response(
    branding: BusinessBranding | None,
    *,
    business_id: UUID,
    storage: ObjectStorage,
    signed_url_ttl_seconds: int,
) -> BusinessBrandingResponse:
    """Project already-authorized branding; never mutate its durable values.

    logo_url is legacy canonical metadata, never evidence of ownership. Signing
    does not check object existence: missing objects require migration/re-upload.
    """
    if branding is not None and branding.business_id != business_id:
        raise BusinessBrandingPersistenceError(_PERSISTENCE_MESSAGE)
    logo_url = None
    key = validated_business_logo_key(branding, business_id=business_id)
    if key is not None:
        try:
            logo_url = storage.presentation_url(
                key, expires_in_seconds=signed_url_ttl_seconds,
            )
        except (StorageError, ValueError):
            # Keep optional branding usable without leaking the stale reference.
            logo_url = None
    return BusinessBrandingResponse(
        primary_color=branding.primary_color if branding else None,
        secondary_color=branding.secondary_color if branding else None,
        accent_color=branding.accent_color if branding else None,
        logo_url=logo_url,
    )
