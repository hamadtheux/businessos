from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


ReadinessState = Literal["ready", "action_needed", "not_applicable"]


class ActivationReadinessCheck(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    label: str = Field(min_length=1, max_length=120)
    state: ReadinessState
    required: bool
    detail: str = Field(min_length=1, max_length=500)
    href: str = Field(pattern=r"^/[A-Za-z0-9/_-]*$")
    evidence: dict[str, str | int | bool | None] = Field(
        default_factory=dict,
        max_length=12,
    )
    model_config = ConfigDict(extra="forbid")


class ActivationReadinessResponse(BaseModel):
    activation_ready: bool
    overall_status: Literal["ready", "action_needed"]
    ready_required_checks: int = Field(ge=0)
    required_checks: int = Field(ge=1)
    checks: list[ActivationReadinessCheck] = Field(min_length=1, max_length=32)
    generated_at: AwareDatetime
    model_config = ConfigDict(extra="forbid")
