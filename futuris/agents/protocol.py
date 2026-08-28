"""Agent communication protocol: typed messages and structured result handlers."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class AgentMessage(BaseModel):
    """Structured, typed communication message emitted by Futuris agents."""

    message_id: UUID = Field(default_factory=uuid4)
    agent_name: str
    task_context: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[UUID] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    result: dict[str, Any] = Field(default_factory=dict)  # Machine-readable output
    narrative: str  # Human-readable explanation
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AgentResultHandler(Protocol):
    """Protocol for consuming and dispatching agent analysis messages."""

    async def handle_message(self, message: AgentMessage) -> None:
        """Process and persist or route an agent message."""
        ...


class LogResultHandler:
    """Default handler that logs agent findings and invokes registered callbacks."""

    def __init__(self, callback: Callable[[AgentMessage], Any] | None = None) -> None:
        self.callback = callback
        self.messages: list[AgentMessage] = []

    async def handle_message(self, message: AgentMessage) -> None:
        self.messages.append(message)
        if self.callback:
            res = self.callback(message)
            if hasattr(res, "__await__"):
                await res
