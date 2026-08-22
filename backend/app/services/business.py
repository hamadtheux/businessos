from collections.abc import Iterator
from dataclasses import dataclass
from typing import Final, Never
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions.business import (
    BusinessListingPersistenceError,
    BusinessOnboardingConflictError,
    BusinessOnboardingPersistenceError,
)
from app.models.business import Business
from app.models.business_branding import BusinessBranding
from app.models.business_membership import BusinessMembership
from app.schemas.business import BusinessOnboardingInput
from app.utils.slug import add_uuid_slug_suffix, create_slug_base


_BUSINESS_RESOURCE_CONSTRAINTS: Final = frozenset(
    {
        "pk_businesses",
        "pk_business_branding",
        "uq_business_memberships_business_user",
    }
)
_BUSINESS_SLUG_CONSTRAINTS: Final = frozenset(
    {
        "ix_businesses_slug",
        "uq_businesses_slug",
    }
)
_ONBOARDING_CONFLICT_MESSAGE: Final = (
    "Business onboarding conflicts with an existing resource"
)
_ONBOARDING_PERSISTENCE_MESSAGE: Final = (
    "Unable to persist business onboarding"
)


@dataclass(frozen=True, slots=True)
class AccessibleBusiness:
    business: Business
    membership_role: str


@dataclass(frozen=True, slots=True)
class CreatedBusinessContext:
    business: Business
    membership: BusinessMembership
    branding: BusinessBranding | None
    created: bool


async def create_business_from_onboarding(
    session: AsyncSession,
    user_id: UUID,
    onboarding: BusinessOnboardingInput,
) -> CreatedBusinessContext:
    """Create an owned business atomically without committing the transaction."""
    existing = await _load_owned_business_context(
        session,
        user_id=user_id,
        business_id=onboarding.business_id,
    )
    if existing is not None:
        _ensure_retry_payload_matches(existing, onboarding)
        return existing

    base_slug = create_slug_base(onboarding.name)
    suffixed_slug = add_uuid_slug_suffix(base_slug, onboarding.business_id)
    base_slug_owner = await _load_business_id_for_slug(session, base_slug)
    slug_candidates = (
        (base_slug, suffixed_slug)
        if base_slug_owner is None
        else (suffixed_slug,)
    )

    for slug in slug_candidates:
        context = _build_new_business_context(
            user_id=user_id,
            onboarding=onboarding,
            slug=slug,
        )
        try:
            async with session.begin_nested():
                session.add(context.business)
                session.add(context.membership)
                if context.branding is not None:
                    session.add(context.branding)
                await session.flush()
        except IntegrityError as error:
            constraint_names = set(_iter_constraint_names(error))
            known_resource_conflict = bool(
                constraint_names & _BUSINESS_RESOURCE_CONSTRAINTS
            )
            known_slug_conflict = bool(
                constraint_names & _BUSINESS_SLUG_CONSTRAINTS
            )

            if known_resource_conflict or known_slug_conflict:
                existing = await _load_owned_business_context(
                    session,
                    user_id=user_id,
                    business_id=onboarding.business_id,
                )
                if existing is not None:
                    _ensure_retry_payload_matches(existing, onboarding)
                    return existing

            if known_slug_conflict:
                if slug == base_slug and slug != suffixed_slug:
                    continue
                _raise_onboarding_conflict()

            if known_resource_conflict:
                _raise_onboarding_conflict()

            raise BusinessOnboardingPersistenceError(
                _ONBOARDING_PERSISTENCE_MESSAGE
            ) from None
        except SQLAlchemyError:
            raise BusinessOnboardingPersistenceError(
                _ONBOARDING_PERSISTENCE_MESSAGE
            ) from None

        return context

    _raise_onboarding_conflict()


async def list_accessible_businesses(
    session: AsyncSession,
    user_id: UUID,
) -> list[AccessibleBusiness]:
    """List a user's active businesses through active memberships."""
    statement = (
        select(Business, BusinessMembership.role)
        .join(
            BusinessMembership,
            BusinessMembership.business_id == Business.id,
        )
        .where(
            BusinessMembership.user_id == user_id,
            BusinessMembership.status == "active",
            Business.status == "active",
        )
        .order_by(Business.created_at.asc(), Business.id.asc())
    )

    try:
        result = await session.execute(statement)
        rows = result.all()
    except SQLAlchemyError:
        raise BusinessListingPersistenceError(
            "Unable to load accessible businesses"
        ) from None

    accessible_businesses: list[AccessibleBusiness] = []
    try:
        for business, membership_role in rows:
            if not isinstance(business, Business) or not isinstance(
                membership_role,
                str,
            ):
                raise TypeError("Unexpected accessible-business result")
            accessible_businesses.append(
                AccessibleBusiness(
                    business=business,
                    membership_role=membership_role,
                )
            )
    except (TypeError, ValueError):
        raise BusinessListingPersistenceError(
            "Unable to load accessible businesses"
        ) from None

    return accessible_businesses


async def _load_owned_business_context(
    session: AsyncSession,
    *,
    user_id: UUID,
    business_id: UUID,
) -> CreatedBusinessContext | None:
    try:
        business = await session.scalar(
            select(Business).where(Business.id == business_id)
        )
        if business is None:
            return None
        if not isinstance(business, Business):
            raise TypeError("Unexpected business result")

        membership = await session.scalar(
            select(BusinessMembership).where(
                BusinessMembership.business_id == business_id,
                BusinessMembership.user_id == user_id,
                BusinessMembership.role == "owner",
            )
        )
        if membership is None:
            _raise_onboarding_conflict()
        if not isinstance(membership, BusinessMembership):
            raise TypeError("Unexpected owner-membership result")

        branding = await session.scalar(
            select(BusinessBranding).where(
                BusinessBranding.business_id == business_id
            )
        )
        if branding is not None and not isinstance(branding, BusinessBranding):
            raise TypeError("Unexpected branding result")
    except BusinessOnboardingConflictError:
        raise
    except (SQLAlchemyError, TypeError, ValueError):
        raise BusinessOnboardingPersistenceError(
            _ONBOARDING_PERSISTENCE_MESSAGE
        ) from None

    return CreatedBusinessContext(
        business=business,
        membership=membership,
        branding=branding,
        created=False,
    )


async def _load_business_id_for_slug(
    session: AsyncSession,
    slug: str,
) -> UUID | None:
    try:
        business_id = await session.scalar(
            select(Business.id).where(Business.slug == slug)
        )
    except SQLAlchemyError:
        raise BusinessOnboardingPersistenceError(
            _ONBOARDING_PERSISTENCE_MESSAGE
        ) from None

    if business_id is not None and not isinstance(business_id, UUID):
        raise BusinessOnboardingPersistenceError(
            _ONBOARDING_PERSISTENCE_MESSAGE
        )
    return business_id


def _build_new_business_context(
    *,
    user_id: UUID,
    onboarding: BusinessOnboardingInput,
    slug: str,
) -> CreatedBusinessContext:
    business = Business(
        id=onboarding.business_id,
        name=onboarding.name,
        slug=slug,
        business_type=onboarding.business_type,
        timezone=onboarding.timezone,
        currency=onboarding.currency,
        locale=onboarding.locale,
    )
    membership = BusinessMembership(
        business_id=onboarding.business_id,
        user_id=user_id,
        role="owner",
        status="active",
    )

    branding_input = onboarding.branding
    branding = None
    if branding_input is not None and any(
        color is not None
        for color in (
            branding_input.primary_color,
            branding_input.secondary_color,
            branding_input.accent_color,
        )
    ):
        branding = BusinessBranding(
            business_id=onboarding.business_id,
            primary_color=branding_input.primary_color,
            secondary_color=branding_input.secondary_color,
            accent_color=branding_input.accent_color,
        )

    return CreatedBusinessContext(
        business=business,
        membership=membership,
        branding=branding,
        created=True,
    )


def _ensure_retry_payload_matches(
    existing: CreatedBusinessContext,
    onboarding: BusinessOnboardingInput,
) -> None:
    business = existing.business
    material_business_values = (
        business.name,
        business.business_type,
        business.timezone,
        business.currency,
        business.locale,
    )
    requested_business_values = (
        onboarding.name,
        onboarding.business_type,
        onboarding.timezone,
        onboarding.currency,
        onboarding.locale,
    )

    branding = existing.branding
    existing_branding_colors = (
        (
            branding.primary_color,
            branding.secondary_color,
            branding.accent_color,
        )
        if branding is not None
        else (None, None, None)
    )
    branding_input = onboarding.branding
    requested_branding_colors = (
        (
            branding_input.primary_color,
            branding_input.secondary_color,
            branding_input.accent_color,
        )
        if branding_input is not None
        else (None, None, None)
    )

    if (
        material_business_values != requested_business_values
        or existing_branding_colors != requested_branding_colors
    ):
        _raise_onboarding_conflict()


def _raise_onboarding_conflict() -> Never:
    raise BusinessOnboardingConflictError(_ONBOARDING_CONFLICT_MESSAGE)


def _iter_constraint_names(error: IntegrityError) -> Iterator[str]:
    current: BaseException | None = error.orig
    visited: set[int] = set()

    while current is not None and id(current) not in visited:
        visited.add(id(current))

        constraint_name = getattr(current, "constraint_name", None)
        if isinstance(constraint_name, str):
            yield constraint_name

        diagnostic = getattr(current, "diag", None)
        diagnostic_constraint = getattr(diagnostic, "constraint_name", None)
        if isinstance(diagnostic_constraint, str):
            yield diagnostic_constraint

        cause = current.__cause__
        context = current.__context__
        if isinstance(cause, BaseException):
            current = cause
        elif isinstance(context, BaseException):
            current = context
        else:
            current = None
