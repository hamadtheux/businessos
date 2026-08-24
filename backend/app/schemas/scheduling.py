from __future__ import annotations

from datetime import date, time, timedelta
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


ProviderType = str
AvailabilityExceptionKind = Literal["unavailable", "available_override"]
AppointmentStatus = Literal["confirmed", "canceled", "completed", "no_show"]
AppointmentSource = Literal["manual", "api", "ai", "website", "whatsapp", "import"]


def _trim(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    normalized = value.strip()
    return normalized or None


def _validate_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise ValueError("timezone must be a valid IANA timezone") from None
    return value


class ServiceProviderCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=160)
    provider_type: ProviderType = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    title: str | None = Field(default=None, min_length=1, max_length=120)
    specialty: str | None = Field(default=None, min_length=1, max_length=160)
    active: bool = True
    timezone: str = Field(min_length=1, max_length=64)
    location_reference: str | None = Field(default=None, min_length=1, max_length=100)

    model_config = ConfigDict(extra="forbid")

    @field_validator("display_name", "provider_type", "title", "specialty", "timezone", "location_reference", mode="before")
    @classmethod
    def trim_text(cls, value: Any) -> Any:
        return _trim(value)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        return _validate_timezone(value)


class ServiceProviderUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    provider_type: ProviderType | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,31}$")
    title: str | None = Field(default=None, min_length=1, max_length=120)
    specialty: str | None = Field(default=None, min_length=1, max_length=160)
    active: bool | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    location_reference: str | None = Field(default=None, min_length=1, max_length=100)

    model_config = ConfigDict(extra="forbid")

    @field_validator("display_name", "provider_type", "title", "specialty", "timezone", "location_reference", mode="before")
    @classmethod
    def trim_text(cls, value: Any) -> Any:
        return _trim(value)

    @field_validator("display_name", "provider_type", "active", "timezone")
    @classmethod
    def required_updates_are_not_null(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("field cannot be null")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str | None) -> str | None:
        return _validate_timezone(value) if value is not None else value


class ServiceProviderResponse(ServiceProviderCreate):
    id: UUID
    business_id: UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class AppointmentTypeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    duration_minutes: int = Field(ge=5, le=1440)
    buffer_before_minutes: int = Field(default=0, ge=0, le=720)
    buffer_after_minutes: int = Field(default=0, ge=0, le=720)
    slot_interval_minutes: int = Field(default=15, ge=5, le=1440)
    active: bool = True
    minimum_notice_minutes: int = Field(default=0, ge=0, le=525600)
    maximum_future_days: int = Field(default=365, ge=1, le=730)
    allow_same_day: bool = True
    cancellation_cutoff_minutes: int = Field(default=0, ge=0, le=525600)
    reschedule_cutoff_minutes: int = Field(default=0, ge=0, le=525600)

    model_config = ConfigDict(extra="forbid")

    @field_validator("name", "description", mode="before")
    @classmethod
    def trim_text(cls, value: Any) -> Any:
        return _trim(value)


class AppointmentTypeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    duration_minutes: int | None = Field(default=None, ge=5, le=1440)
    buffer_before_minutes: int | None = Field(default=None, ge=0, le=720)
    buffer_after_minutes: int | None = Field(default=None, ge=0, le=720)
    slot_interval_minutes: int | None = Field(default=None, ge=5, le=1440)
    active: bool | None = None
    minimum_notice_minutes: int | None = Field(default=None, ge=0, le=525600)
    maximum_future_days: int | None = Field(default=None, ge=1, le=730)
    allow_same_day: bool | None = None
    cancellation_cutoff_minutes: int | None = Field(default=None, ge=0, le=525600)
    reschedule_cutoff_minutes: int | None = Field(default=None, ge=0, le=525600)

    model_config = ConfigDict(extra="forbid")

    @field_validator("name", "description", mode="before")
    @classmethod
    def trim_text(cls, value: Any) -> Any:
        return _trim(value)

    @field_validator(
        "name", "duration_minutes", "buffer_before_minutes", "buffer_after_minutes",
        "slot_interval_minutes", "active", "minimum_notice_minutes",
        "maximum_future_days", "allow_same_day", "cancellation_cutoff_minutes",
        "reschedule_cutoff_minutes",
    )
    @classmethod
    def required_updates_are_not_null(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("field cannot be null")
        return value


class AppointmentTypeResponse(AppointmentTypeCreate):
    id: UUID
    business_id: UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class CustomerCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=160)
    active: bool = True
    model_config = ConfigDict(extra="forbid")

    @field_validator("display_name", mode="before")
    @classmethod
    def trim_name(cls, value: Any) -> Any:
        return _trim(value)


class CustomerResponse(CustomerCreate):
    id: UUID
    business_id: UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class ProviderAppointmentTypeResponse(BaseModel):
    id: UUID
    business_id: UUID
    provider_id: UUID
    appointment_type_id: UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class AvailabilityRuleCreate(BaseModel):
    weekday: int = Field(ge=0, le=6)
    start_local_time: time
    end_local_time: time
    valid_from: date | None = None
    valid_until: date | None = None
    active: bool = True
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def valid_window(self) -> "AvailabilityRuleCreate":
        if self.start_local_time.tzinfo is not None or self.end_local_time.tzinfo is not None:
            raise ValueError("availability rules use local wall-clock times")
        if self.start_local_time >= self.end_local_time:
            raise ValueError("availability start must be before end")
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            raise ValueError("valid_until must not precede valid_from")
        return self


class AvailabilityRuleUpdate(BaseModel):
    weekday: int | None = Field(default=None, ge=0, le=6)
    start_local_time: time | None = None
    end_local_time: time | None = None
    valid_from: date | None = None
    valid_until: date | None = None
    active: bool | None = None
    model_config = ConfigDict(extra="forbid")


class AvailabilityRuleResponse(AvailabilityRuleCreate):
    id: UUID
    business_id: UUID
    provider_id: UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class AvailabilityExceptionCreate(BaseModel):
    exception_date: date
    exception_kind: AvailabilityExceptionKind
    whole_day: bool = False
    start_local_time: time | None = None
    end_local_time: time | None = None
    active: bool = True
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def valid_window(self) -> "AvailabilityExceptionCreate":
        if self.whole_day:
            if self.start_local_time is not None or self.end_local_time is not None:
                raise ValueError("whole-day exceptions cannot include times")
        elif (
            self.start_local_time is None
            or self.end_local_time is None
            or self.start_local_time >= self.end_local_time
        ):
            raise ValueError("bounded exceptions require a valid local-time window")
        for value in (self.start_local_time, self.end_local_time):
            if value is not None and value.tzinfo is not None:
                raise ValueError("exceptions use local wall-clock times")
        return self


class AvailabilityExceptionUpdate(BaseModel):
    exception_date: date | None = None
    exception_kind: AvailabilityExceptionKind | None = None
    whole_day: bool | None = None
    start_local_time: time | None = None
    end_local_time: time | None = None
    active: bool | None = None
    model_config = ConfigDict(extra="forbid")


class AvailabilityExceptionResponse(AvailabilityExceptionCreate):
    id: UUID
    business_id: UUID
    provider_id: UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class AvailabilitySearchRequest(BaseModel):
    appointment_type_id: UUID
    provider_id: UUID | None = None
    window_start: AwareDatetime
    window_end: AwareDatetime
    desired_results: int = Field(default=10, ge=1, le=50)
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def valid_window(self) -> "AvailabilitySearchRequest":
        if self.window_end <= self.window_start:
            raise ValueError("window_end must be after window_start")
        if self.window_end - self.window_start > timedelta(days=31):
            raise ValueError("availability window cannot exceed 31 days")
        return self


class NextAvailabilitySearchRequest(BaseModel):
    appointment_type_id: UUID
    provider_id: UUID | None = None
    starts_after: AwareDatetime
    desired_results: int = Field(default=3, ge=1, le=50)
    search_days: int = Field(default=30, ge=1, le=90)
    model_config = ConfigDict(extra="forbid")


class AvailabilitySlot(BaseModel):
    provider_id: UUID
    provider_display_name: str
    appointment_type_id: UUID
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    timezone: str
    location_reference: str | None = None
    model_config = ConfigDict(extra="forbid", frozen=True)


class AvailabilityResponse(BaseModel):
    slots: list[AvailabilitySlot]
    model_config = ConfigDict(extra="forbid")


class AppointmentCreate(BaseModel):
    provider_id: UUID
    appointment_type_id: UUID
    customer_id: UUID | None = None
    starts_at: AwareDatetime
    source: AppointmentSource = "manual"
    model_config = ConfigDict(extra="forbid")


class AppointmentRescheduleRequest(BaseModel):
    starts_at: AwareDatetime
    model_config = ConfigDict(extra="forbid")


class AppointmentCancelRequest(BaseModel):
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    model_config = ConfigDict(extra="forbid")

    @field_validator("reason_code", mode="before")
    @classmethod
    def trim_reason(cls, value: Any) -> Any:
        return _trim(value)


class AppointmentResponse(BaseModel):
    id: UUID
    business_id: UUID
    provider_id: UUID
    appointment_type_id: UUID
    customer_id: UUID | None
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    status: AppointmentStatus
    source: AppointmentSource
    created_by_user_id: UUID | None
    cancellation_reason_code: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    model_config = ConfigDict(from_attributes=True, extra="forbid")
