from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions.business import BusinessBrandingPersistenceError
from app.models.business_branding import BusinessBranding
from app.schemas.business import BusinessBrandingUpdate


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
