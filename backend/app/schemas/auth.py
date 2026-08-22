from typing import Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    field_validator,
)


class UserRegistrationInput(BaseModel):
    email: EmailStr = Field(max_length=320)
    password: SecretStr = Field(min_length=12, max_length=128)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)

    model_config = ConfigDict(extra="forbid")

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("first_name", mode="before")
    @classmethod
    def normalize_first_name(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("last_name", mode="before")
    @classmethod
    def normalize_last_name(cls, value: object) -> object:
        if isinstance(value, str):
            normalized_value = value.strip()
            return normalized_value or None
        return value


class UserLoginInput(BaseModel):
    email: EmailStr = Field(max_length=320)
    password: SecretStr = Field(max_length=128)

    model_config = ConfigDict(extra="forbid")

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value


class UserPublic(BaseModel):
    id: UUID
    email: EmailStr = Field(max_length=320)
    first_name: str
    last_name: str | None
    status: Literal["active", "inactive", "suspended"]
    is_email_verified: bool
    created_at: AwareDatetime

    model_config = ConfigDict(from_attributes=True)


class UserLoginResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(gt=0)
    user: UserPublic

    model_config = ConfigDict(extra="forbid")
