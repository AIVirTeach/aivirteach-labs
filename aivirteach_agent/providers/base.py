from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence


class ProviderError(RuntimeError):
    """A normalized model-provider failure."""


@dataclass(frozen=True)
class ProviderToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ProviderMessage:
    role: str
    content: str | None = None
    tool_calls: tuple[ProviderToolCall, ...] = field(default_factory=tuple)
    tool_call_id: str | None = None


@dataclass(frozen=True)
class ProviderTool:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ProviderTurn:
    text: str | None = None
    tool_calls: tuple[ProviderToolCall, ...] = field(default_factory=tuple)
    finish_reason: str = "stop"


class ModelProvider(Protocol):
    async def complete(
        self,
        *,
        messages: Sequence[ProviderMessage],
        tools: Sequence[ProviderTool],
    ) -> ProviderTurn: ...

    async def aclose(self) -> None: ...
