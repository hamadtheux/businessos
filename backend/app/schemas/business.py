import re
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


_HEX_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$", flags=re.ASCII)
_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$", flags=re.ASCII)
_LOCALE_PATTERN = re.compile(
    r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$",
    flags=re.ASCII,
)


def normalize_hex_color(value: Any) -> Any:
    if value is None:
        return None
    if not isinstance(value, str) or not _HEX_COLOR_PATTERN.fullmatch(value):
        raise ValueError("Color must use 6-digit HEX format")
    return value.upper()


class _BusinessBrandingColors(BaseModel):
    primary_color: str | None = None
    secondary_color: str | None = None
    accent_color: str | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator(
        "primary_color",
        "secondary_color",
        "accent_color",
        mode="before",
    )
    @classmethod
    def validate_color(cls, value: Any) -> Any:
        return normalize_hex_color(value)


class BusinessBrandingInput(_BusinessBrandingColors):
    pass


class BusinessBrandingUpdate(_BusinessBrandingColors):
    """Replace the persisted source colors without accepting logo data."""


class BusinessOnboardingInput(BaseModel):
    business_id: UUID
    name: str = Field(min_length=1, max_length=160)
    business_type: str = Field(min_length=1, max_length=80)
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    locale: str = Field(default="en", min_length=2, max_length=16)
    branding: BusinessBrandingInput | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("business_type", mode="before")
    @classmethod
    def normalize_business_type(cls, value: Any) -> Any:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("timezone", mode="before")
    @classmethod
    def validate_timezone(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value

        normalized = value.strip()
        if not normalized or len(normalized) > 64:
            raise ValueError("Timezone must be a valid IANA identifier")
        try:
            ZoneInfo(normalized)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("Timezone must be a valid IANA identifier") from None
        return normalized

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value

        normalized = value.strip().upper()
        if not _CURRENCY_PATTERN.fullmatch(normalized):
            raise ValueError("Currency must contain exactly 3 ASCII letters")
        return normalized

    @field_validator("locale", mode="before")
    @classmethod
    def normalize_locale(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value

        normalized = value.strip()
        if len(normalized) > 16 or not _LOCALE_PATTERN.fullmatch(normalized):
            raise ValueError("Locale must be a practical BCP-47 value")

        parts = normalized.split("-")
        normalized_parts = [parts[0].lower()]
        for part in parts[1:]:
            if len(part) == 2 and part.isalpha():
                normalized_parts.append(part.upper())
            elif len(part) == 4 and part.isalpha():
                normalized_parts.append(part.title())
            else:
                normalized_parts.append(part.lower())
        return "-".join(normalized_parts)


class BusinessSummary(BaseModel):
    id: UUID
    name: str
    slug: str
    business_type: str
    status: Literal["active", "inactive", "suspended"]
    timezone: str
    currency: str
    locale: str
    membership_role: str
    created_at: AwareDatetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class BusinessBrandingResponse(BaseModel):
    primary_color: str | None
    secondary_color: str | None
    accent_color: str | None
    logo_url: str | None

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class BusinessOnboardingResponse(BaseModel):
    business: BusinessSummary
    branding: BusinessBrandingResponse | None
    created: bool

    model_config = ConfigDict(extra="forbid")
