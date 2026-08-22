from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import CurrentUserDependency
from app.db.session import get_db_session
from app.models.business import Business
from app.models.business_membership import BusinessMembership
from app.models.user import User


SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


@dataclass(frozen=True, slots=True)
class BusinessAccessContext:
    user: User
    business: Business
    membership: BusinessMembership


async def get_business_access(
    business_id: UUID,
    current_user: CurrentUserDependency,
    session: SessionDependency,
) -> BusinessAccessContext:
    """Authorize an authenticated user's access to one explicit business."""
    statement = (
        select(Business, BusinessMembership)
        .join(
            BusinessMembership,
            BusinessMembership.business_id == Business.id,
        )
        .where(
            BusinessMembership.user_id == current_user.id,
            BusinessMembership.business_id == business_id,
        )
    )

    try:
        result = await session.execute(statement)
        access_row = result.one_or_none()
    except SQLAlchemyError:
        raise _business_access_persistence_exception() from None

    if access_row is None:
        raise _business_not_found_exception()

    try:
        business, membership = access_row
    except (TypeError, ValueError):
        raise _business_access_persistence_exception() from None

    if (
        not isinstance(business, Business)
        or not isinstance(membership, BusinessMembership)
    ):
        raise _business_access_persistence_exception()

    if (
        business.id != business_id
        or membership.business_id != business_id
        or membership.user_id != current_user.id
    ):
        raise _business_not_found_exception()

    if membership.status != "active" or business.status != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Business access is unavailable.",
        )

    return BusinessAccessContext(
        user=current_user,
        business=business,
        membership=membership,
    )


def _business_not_found_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Business not found.",
    )


def _business_access_persistence_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Business access is temporarily unavailable.",
    )


BusinessAccessDependency = Annotated[
    BusinessAccessContext,
    Depends(get_business_access),
]
