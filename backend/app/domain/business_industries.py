from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Mapping


@dataclass(frozen=True, slots=True)
class BusinessIndustryDefinition:
    """Server-owned metadata for a supported AI Business OS industry."""

    code: str
    label: str
    group: str
    is_healthcare: bool = False
    supports_scheduling: bool = False


_BUSINESS_INDUSTRIES = {
    "farm/agriculture": BusinessIndustryDefinition(
        code="farm/agriculture",
        label="Farm/Agriculture",
        group="agriculture",
    ),
    "real estate": BusinessIndustryDefinition(
        code="real estate",
        label="Real Estate",
        group="real_estate",
    ),
    "e-commerce": BusinessIndustryDefinition(
        code="e-commerce",
        label="E-commerce",
        group="commerce",
    ),
    "hospital": BusinessIndustryDefinition(
        code="hospital",
        label="Hospital",
        group="healthcare",
        is_healthcare=True,
        supports_scheduling=True,
    ),
    "clinic": BusinessIndustryDefinition(
        code="clinic",
        label="Clinic",
        group="healthcare",
        is_healthcare=True,
        supports_scheduling=True,
    ),
    "medical practice": BusinessIndustryDefinition(
        code="medical practice",
        label="Medical Practice",
        group="healthcare",
        is_healthcare=True,
        supports_scheduling=True,
    ),
    "dental": BusinessIndustryDefinition(
        code="dental",
        label="Dental",
        group="healthcare",
        is_healthcare=True,
        supports_scheduling=True,
    ),
    "professional services": BusinessIndustryDefinition(
        code="professional services",
        label="Professional Services",
        group="professional_services",
        supports_scheduling=True,
    ),
    "other": BusinessIndustryDefinition(
        code="other",
        label="Other",
        group="other",
    ),
}

BUSINESS_INDUSTRIES: Final[
    Mapping[str, BusinessIndustryDefinition]
] = MappingProxyType(_BUSINESS_INDUSTRIES)

BUSINESS_TYPE_ALIASES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "agriculture": "farm/agriculture",
        "farm": "farm/agriculture",
        "farm/agriculture": "farm/agriculture",
        "real estate": "real estate",
        "real-estate": "real estate",
        "ecommerce": "e-commerce",
        "e commerce": "e-commerce",
        "e-commerce": "e-commerce",
        "hospital": "hospital",
        "clinic": "clinic",
        "medical": "medical practice",
        "medical practice": "medical practice",
        "dental": "dental",
        "professional services": "professional services",
        "professional service": "professional services",
        "other": "other",
    }
)

HEALTHCARE_BUSINESS_TYPES: Final[frozenset[str]] = frozenset(
    definition.code
    for definition in BUSINESS_INDUSTRIES.values()
    if definition.is_healthcare
)

SCHEDULING_BUSINESS_TYPES: Final[frozenset[str]] = frozenset(
    definition.code
    for definition in BUSINESS_INDUSTRIES.values()
    if definition.supports_scheduling
)


def normalize_business_type(value: str) -> str:
    """
    Normalize a business type while preserving forward compatibility.

    Known aliases resolve to their canonical server-owned code. Unknown values
    remain normalized strings rather than being rejected, because the existing
    Business domain intentionally supports bounded custom/future industries.
    """

    normalized = " ".join(value.strip().casefold().split())
    return BUSINESS_TYPE_ALIASES.get(normalized, normalized)


def get_business_industry(
    value: str,
) -> BusinessIndustryDefinition | None:
    return BUSINESS_INDUSTRIES.get(normalize_business_type(value))


def is_healthcare_business_type(value: str) -> bool:
    return normalize_business_type(value) in HEALTHCARE_BUSINESS_TYPES


def business_type_supports_scheduling(value: str) -> bool:
    return normalize_business_type(value) in SCHEDULING_BUSINESS_TYPES
