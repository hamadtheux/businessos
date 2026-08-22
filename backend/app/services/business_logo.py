from dataclasses import dataclass
import logging
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions.logo import BusinessLogoPersistenceError
from app.models.business_branding import BusinessBranding
from app.services.business_branding import get_business_branding
from app.services.logo_image import SanitizedLogo
from app.storage.base import ObjectStorage, StorageError, StorageOperationError


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PreparedLogoUpload:
    branding: BusinessBranding
    new_storage_key: str
    previous_storage_key: str | None


@dataclass(frozen=True, slots=True)
class PreparedLogoDeletion:
    previous_storage_key: str | None


async def prepare_business_logo_upload(
    session: AsyncSession,
    business_id: UUID,
    logo: SanitizedLogo,
    storage: ObjectStorage,
) -> PreparedLogoUpload:
    branding = await get_business_branding(session, business_id)
    previous_storage_key = branding.logo_storage_key if branding is not None else None
    new_storage_key = _new_logo_storage_key(business_id, logo.extension)

    await storage.put(new_storage_key, logo.content, logo.content_type)
    try:
        logo_url = storage.public_url(new_storage_key)
        if len(logo_url) > 2048:
            raise StorageOperationError("Generated public URL is too long")
        if branding is None:
            branding = BusinessBranding(business_id=business_id)
            session.add(branding)
        branding.logo_storage_key = new_storage_key
        branding.logo_url = logo_url
        await session.flush()
    except StorageError:
        await _cleanup_staged_object(storage, new_storage_key)
        raise
    except SQLAlchemyError:
        await _cleanup_staged_object(storage, new_storage_key)
        raise BusinessLogoPersistenceError("Unable to persist business logo") from None

    return PreparedLogoUpload(
        branding=branding,
        new_storage_key=new_storage_key,
        previous_storage_key=previous_storage_key,
    )


async def prepare_business_logo_deletion(
    session: AsyncSession,
    business_id: UUID,
) -> PreparedLogoDeletion:
    branding = await get_business_branding(session, business_id)
    if branding is None:
        return PreparedLogoDeletion(previous_storage_key=None)

    previous_storage_key = branding.logo_storage_key
    if branding.logo_url is None and previous_storage_key is None:
        return PreparedLogoDeletion(previous_storage_key=None)

    try:
        branding.logo_url = None
        branding.logo_storage_key = None
        if all(
            color is None
            for color in (
                branding.primary_color,
                branding.secondary_color,
                branding.accent_color,
            )
        ):
            await session.delete(branding)
        await session.flush()
    except SQLAlchemyError:
        raise BusinessLogoPersistenceError(
            "Unable to persist business logo deletion"
        ) from None

    return PreparedLogoDeletion(previous_storage_key=previous_storage_key)


async def cleanup_storage_object(
    storage: ObjectStorage,
    object_key: str | None,
) -> bool:
    if object_key is None:
        return True
    try:
        await storage.delete(object_key)
    except StorageError:
        return False
    return True


async def _cleanup_staged_object(
    storage: ObjectStorage,
    object_key: str,
) -> None:
    if not await cleanup_storage_object(storage, object_key):
        logger.warning("Business logo compensation cleanup failed")


def _new_logo_storage_key(business_id: UUID, extension: str) -> str:
    return f"businesses/{business_id}/branding/logo/{uuid4().hex}.{extension}"
