from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import (
    AnyHttpUrl,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    WithJsonSchema,
    field_validator,
    model_validator,
)


Reference = Annotated[str, Field(min_length=1, max_length=255)]
ShortText = Annotated[str, Field(min_length=1, max_length=200)]
MessageText = Annotated[str, Field(min_length=1, max_length=10_000)]
Money = Annotated[
    Decimal,
    Field(
        ge=Decimal("0.00"),
        le=Decimal("1000000.00"),
        max_digits=14,
        decimal_places=2,
    ),
    WithJsonSchema(
        {
            "type": "string",
            "description": (
                "Decimal monetary amount from 0.00 through "
                "1000000.00 with at most two decimal places."
            ),
        }
    ),
]

SocialPlatform = Literal[
    "facebook",
    "instagram",
    "linkedin",
    "x",
    "tiktok",
]

CampaignObjective = Literal[
    "awareness",
    "traffic",
    "engagement",
    "leads",
    "sales",
    "app_promotion",
]


class ActionPayload(BaseModel):
    """Immutable, connector-safe base for every supported action payload."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    @field_validator("*", mode="before")
    @classmethod
    def trim_direct_strings(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class SendEmailPayload(ActionPayload):
    recipient_ref: Reference
    subject: str = Field(min_length=1, max_length=200)
    body: MessageText
    conversation_ref: Reference | None = None
    reply_to_ref: Reference | None = None
    thread_ref: Reference | None = None


class SendWhatsAppMessagePayload(ActionPayload):
    customer_ref: Reference
    message: MessageText
    conversation_ref: Reference | None = None


class SendCustomerMessagePayload(ActionPayload):
    customer_ref: Reference
    message: MessageText
    conversation_ref: Reference | None = None


class PublishSocialPostPayload(ActionPayload):
    platform: SocialPlatform
    content: MessageText
    media_refs: list[Reference] = Field(default_factory=list, max_length=10)

    @field_validator("media_refs")
    @classmethod
    def unique_media_refs(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("media_refs cannot contain duplicates")
        return value


class CampaignAudience(ActionPayload):
    countries: list[str] = Field(min_length=1, max_length=25)
    min_age: int | None = Field(default=None, ge=18, le=100)
    max_age: int | None = Field(default=None, ge=18, le=100)
    languages: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("countries", mode="before")
    @classmethod
    def normalize_countries(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        return [item.strip().upper() if isinstance(item, str) else item for item in value]

    @field_validator("countries")
    @classmethod
    def validate_countries(cls, value: list[str]) -> list[str]:
        if any(len(item) != 2 or not item.isalpha() for item in value):
            raise ValueError("countries must contain ISO 3166-1 alpha-2 codes")
        if len(value) != len(set(value)):
            raise ValueError("countries cannot contain duplicates")
        return value

    @field_validator("languages", mode="before")
    @classmethod
    def normalize_languages(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        return [item.strip().lower() if isinstance(item, str) else item for item in value]

    @field_validator("languages")
    @classmethod
    def validate_languages(cls, value: list[str]) -> list[str]:
        if any(not item or len(item) > 16 for item in value):
            raise ValueError("languages contains an invalid locale")
        if len(value) != len(set(value)):
            raise ValueError("languages cannot contain duplicates")
        return value

    @model_validator(mode="after")
    def validate_age_range(self) -> "CampaignAudience":
        if (self.min_age is None) != (self.max_age is None):
            raise ValueError("min_age and max_age must both be provided or both omitted")
        if (
            self.min_age is not None
            and self.max_age is not None
            and self.max_age < self.min_age
        ):
            raise ValueError("max_age cannot be less than min_age")
        return self


class CampaignCreative(ActionPayload):
    creative_refs: list[Reference] = Field(min_length=1, max_length=20)

    # Keep the provider-facing JSON Schema compatible with OpenAI Structured
    # Outputs by exposing this as a bounded string instead of emitting
    # `format: uri`.
    #
    # The value is still validated server-side as a real HTTP/HTTPS URL below.
    destination_url: str | None = Field(
        default=None,
        min_length=1,
        max_length=2_083,
    )

    @field_validator("creative_refs")
    @classmethod
    def unique_creative_refs(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("creative_refs cannot contain duplicates")
        return value

    @field_validator("destination_url")
    @classmethod
    def validate_destination_url(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        try:
            parsed = TypeAdapter(
                AnyHttpUrl
            ).validate_python(
                value
            )
        except Exception:
            raise ValueError(
                "destination_url must be a valid HTTP or HTTPS URL"
            ) from None

        if (
            parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError(
                "destination_url cannot contain credentials"
            )

        return str(parsed)


class CreateCampaignPayload(ActionPayload):
    campaign_name: str = Field(min_length=1, max_length=200)
    objective: CampaignObjective
    budget: Money
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    budget_period: Literal["daily", "lifetime"]
    audience: CampaignAudience
    creative: CampaignCreative

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: Any) -> Any:
        return value.strip().upper() if isinstance(value, str) else value


class CreateMetaCampaignPayload(CreateCampaignPayload):
    catalog_ref: Reference | None = None
    product_set_ref: Reference | None = None
    page_ref: Reference | None = None
    conversion_dataset_ref: Reference | None = None
    primary_text: str | None = Field(default=None, max_length=2_000)
    headline: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=500)
    call_to_action: str = Field(default="SHOP_NOW", min_length=1, max_length=64)


class CreateGoogleAdsCampaignPayload(CreateCampaignPayload):
    network: Literal["search", "display", "shopping", "video", "performance_max"]
    merchant_account_ref: Reference | None = None
    conversion_action_ref: Reference | None = None
    product_offer_ids: list[Reference] = Field(default_factory=list, max_length=1_000)
    business_name: str | None = Field(default=None, max_length=25)
    headlines: list[str] = Field(default_factory=list, max_length=15)
    descriptions: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("product_offer_ids", "headlines", "descriptions")
    @classmethod
    def unique_campaign_values(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("campaign values cannot contain duplicates")
        return value


class LaunchCampaignPayload(ActionPayload):
    campaign_ref: Reference


class LaunchMetaCampaignPayload(LaunchCampaignPayload):
    pass


class LaunchGoogleAdsCampaignPayload(LaunchCampaignPayload):
    pass


class ChangeAdBudgetPayload(ActionPayload):
    campaign_ref: Reference
    budget: Money
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    budget_period: Literal["daily", "lifetime"]

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: Any) -> Any:
        return value.strip().upper() if isinstance(value, str) else value


class PauseAdCampaignPayload(ActionPayload):
    campaign_ref: Reference
    reason: ShortText | None = None


class UpdateCRMPayload(ActionPayload):
    customer_ref: Reference
    stage: Literal[
        "new",
        "qualified",
        "proposal",
        "negotiation",
        "won",
        "lost",
    ] | None = None
    owner_ref: Reference | None = None
    note: str | None = Field(default=None, min_length=1, max_length=2_000)
    next_follow_up_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def require_supported_update(self) -> "UpdateCRMPayload":
        if all(
            value is None
            for value in (
                self.stage,
                self.owner_ref,
                self.note,
                self.next_follow_up_at,
            )
        ):
            raise ValueError("at least one supported CRM update is required")
        return self


class OrderLineItem(ActionPayload):
    catalog_item_ref: Reference
    quantity: int = Field(ge=1, le=10_000)


class CreateOrderPayload(ActionPayload):
    customer_ref: Reference
    line_items: list[OrderLineItem] = Field(min_length=1, max_length=100)
    customer_note: str | None = Field(default=None, min_length=1, max_length=2_000)

    @field_validator("line_items")
    @classmethod
    def unique_line_items(cls, value: list[OrderLineItem]) -> list[OrderLineItem]:
        refs = [item.catalog_item_ref for item in value]
        if len(refs) != len(set(refs)):
            raise ValueError("line_items cannot contain duplicate catalog references")
        return value


ActionPayloadType = (
    SendEmailPayload
    | SendWhatsAppMessagePayload
    | SendCustomerMessagePayload
    | PublishSocialPostPayload
    | CreateMetaCampaignPayload
    | LaunchMetaCampaignPayload
    | CreateGoogleAdsCampaignPayload
    | LaunchGoogleAdsCampaignPayload
    | ChangeAdBudgetPayload
    | PauseAdCampaignPayload
    | UpdateCRMPayload
    | CreateOrderPayload
)
