"""Provider-neutral types for the investigation report agent.

The agent talks to any provider through ``LlmSession``. Keeping these types
free of provider SDK imports lets tests drive the whole agent with a fake
session and no network access.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from api.settings import Settings

ProviderFailure = Literal["provider_error", "timeout", "invalid_output"]


class ProviderError(Exception):
    """A provider call failed. Carries a coarse reason and never upstream text."""

    def __init__(self, reason: ProviderFailure) -> None:
        self.reason: ProviderFailure = reason
        super().__init__(reason)


@dataclass(frozen=True)
class ToolDeclaration:
    """One approved tool exposed to the model."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    """A tool invocation requested by the model."""

    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class ToolResultMessage:
    """A tool result returned to the model."""

    name: str
    payload: dict[str, Any]


@dataclass
class Turn:
    """One model response: optional text, requested tool calls, token usage."""

    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int | None = None
    output_tokens: int | None = None


class LlmSession(Protocol):
    """A tool-calling conversation with a model.

    ``step`` advances the tool loop; ``final_report`` is a stateless call that
    returns JSON conforming to ``schema``. Both raise ``ProviderError`` on any
    failure and must not include upstream error text.
    """

    def step(self, tool_results: Sequence[ToolResultMessage] | None, *, deadline: float) -> Turn: ...

    def final_report(self, prompt: str, schema: dict[str, Any], *, deadline: float) -> Turn: ...


SessionFactory = Callable[
    [Settings, str, str, Sequence[ToolDeclaration]],
    "LlmSession | None",
]
"""Build a session from (settings, system prompt, user prompt, tools).

Returns ``None`` when no provider is configured."""
