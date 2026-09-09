from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.domain.business_industries import (
    get_business_industry,
    is_healthcare_business_type,
)
from app.schemas.business_brain import BusinessBrainSourceType


@dataclass(frozen=True, slots=True)
class CMOContextPolicy:
    """Shared source boundary used by CMO reasoning and creative authority."""

    brain_source_types: tuple[BusinessBrainSourceType, ...] | None = None
    include_memory: bool = True
    privacy_instruction: str | None = None


_PUBLIC_SERVICE_SOURCES: Final[tuple[BusinessBrainSourceType, ...]] = (
    "business_profile",
    "branding",
    "appointment_type",
)


def cmo_context_policy(business_type: str | None) -> CMOContextPolicy:
    """Return the server-owned CMO/creative Business Brain boundary."""
    if isinstance(business_type, str) and is_healthcare_business_type(business_type):
        return CMOContextPolicy(
            brain_source_types=_PUBLIC_SERVICE_SOURCES,
            include_memory=False,
            privacy_instruction=(
                " Never use patient identities, clinical details, diagnoses, "
                "notes, or other PHI."
            ),
        )

    industry = (
        get_business_industry(business_type)
        if isinstance(business_type, str)
        else None
    )
    if industry is not None and industry.group == "professional_services":
        return CMOContextPolicy(
            brain_source_types=_PUBLIC_SERVICE_SOURCES,
            include_memory=False,
            privacy_instruction=(
                " Use only public service descriptions; never infer client "
                "identities or confidential client matters."
            ),
        )

    if (
        isinstance(business_type, str)
        and business_type.strip().casefold() == "real estate"
    ):
        return CMOContextPolicy(
            brain_source_types=("business_profile", "branding", "knowledge_entry"),
            privacy_instruction=(
                " Do not interpret generic catalog items as properties and do "
                "not invent property inventory."
            ),
        )

    return CMOContextPolicy()
