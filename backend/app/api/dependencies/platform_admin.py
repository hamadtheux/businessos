from typing import Annotated

from fastapi import Depends, HTTPException, status

from app.api.dependencies.auth import CurrentUserDependency
from app.core.config import settings
from app.models.user import User


async def require_platform_admin(current_user: CurrentUserDependency) -> User:
    if current_user.email.strip().casefold() not in settings.platform_admin_emails:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform administrator access is required.",
        )
    return current_user


PlatformAdminDependency = Annotated[User, Depends(require_platform_admin)]
