from __future__ import annotations

from types import MappingProxyType
from typing import Literal, Mapping, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions.action_execution_attempt import (
    DirectActionDispatchDisabledError,
)
from app.models.ai_action import AIAction
from app.schemas.ai_action_payload import ActionPayload
from app.services.action_registry import ACTION_REGISTRY


ActionExecutionFailureCode = Literal[
    "action_failed",
    "connector_rejected",
    "temporary_failure",
]


class ActionExecutionResult(BaseModel):
    succeeded: bool
    result_summary: str | None = Field(default=None, min_length=1, max_length=2_000)
    failure_code: ActionExecutionFailureCode | None = None
    external_reference_id: str | None = Field(default=None, min_length=1, max_length=255)

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    @field_validator("result_summary", "external_reference_id", mode="before")
    @classmethod
    def trim_optional_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def consistent_failure_code(self) -> "ActionExecutionResult":
        if self.succeeded and self.failure_code is not None:
            raise ValueError("successful execution cannot have a failure code")
        if not self.succeeded and self.failure_code is None:
            raise ValueError("failed execution requires a failure code")
        return self


@runtime_checkable
class ActionExecutor(Protocol):
    async def execute(
        self,
        payload: ActionPayload,
        *,
        idempotency_key: str,
    ) -> ActionExecutionResult:
        """Execute one already validated payload using a stable idempotency key."""
        ...


class ActionHandlerRegistry:
    """Immutable action-type to executor mapping assembled by trusted code."""

    __slots__ = ("_handlers",)

    def __init__(self, handlers: Mapping[str, ActionExecutor] | None = None) -> None:
        values: dict[str, ActionExecutor] = {}
        for action_type, handler in (handlers or {}).items():
            ACTION_REGISTRY.require(action_type)
            if not isinstance(handler, ActionExecutor):
                raise TypeError("Action handler does not implement ActionExecutor")
            values[action_type] = handler
        self._handlers: Mapping[str, ActionExecutor] = MappingProxyType(values)

    def get(self, action_type: str) -> ActionExecutor | None:
        return self._handlers.get(action_type)


async def execute_ready_ai_action(
    session: AsyncSession,
    *,
    business_id: UUID,
    action_id: UUID,
    handlers: ActionHandlerRegistry,
) -> AIAction:
    """
    Retired compatibility entry point. Direct handler dispatch is disabled.

    Calling a connector while the transaction that marks an AIAction as
    executing remains uncommitted can duplicate side effects after a process
    crash. Production dispatch must instead use:

    1. prepare_action_execution_attempt() and commit
    2. claim_action_execution_attempt() and commit
    3. invoke a future connector outside those transactions
    4. record success, definite failure, or uncertainty in a new transaction

    No handler is invoked by this function.
    """
    _ = session, business_id, action_id, handlers
    raise DirectActionDispatchDisabledError(
        "Direct action handler dispatch is disabled"
    )
