"""Gemini adapter for the investigation report agent (google-genai SDK).

Automatic function calling is disabled: the agent, not the SDK, decides
whether a requested call runs, so only approved tools with validated
arguments ever execute. The final report is a separate stateless request
that returns JSON for a schema, which avoids combining tool use with
structured output and avoids replaying function-call history.

Every failure becomes a ``ProviderError`` with a coarse reason. Upstream
messages are never propagated or logged, because they can echo request
details.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import Any

from api.llm.provider import (
    LlmSession,
    ProviderError,
    ToolCall,
    ToolDeclaration,
    ToolResultMessage,
    Turn,
)
from api.settings import Settings

logger = logging.getLogger("api.llm")

TIMEOUT_STATUS_CODES = frozenset({408, 504})

# The provider rejects requests whose manual deadline is under ten seconds
# (HTTP 400 INVALID_ARGUMENT), so a short remaining budget must still be sent
# as at least this floor or the call is guaranteed to fail.
MIN_PROVIDER_TIMEOUT_MS = 10_000


def _classify(exc: BaseException) -> ProviderError:
    """Map an SDK or transport exception to a coarse provider failure."""
    code = getattr(exc, "code", None)
    name = type(exc).__name__
    logger.warning("llm provider call failed: %s code=%s", name, code)
    if isinstance(exc, TimeoutError) or "timeout" in name.lower() or code in TIMEOUT_STATUS_CODES:
        return ProviderError("timeout")
    return ProviderError("provider_error")


def _usage(response: Any) -> tuple[int | None, int | None]:
    """Prompt and output token counts (output includes reasoning tokens)."""
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return None, None
    prompt = getattr(usage, "prompt_token_count", None)
    output = getattr(usage, "candidates_token_count", None)
    thoughts = getattr(usage, "thoughts_token_count", None)
    total_output = None if output is None and thoughts is None else (output or 0) + (thoughts or 0)
    return prompt, total_output


class GeminiSession:
    """One tool-calling conversation with a Gemini model."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
        tools: Sequence[ToolDeclaration],
        max_output_tokens: int,
    ) -> None:
        from google import genai
        from google.genai import types

        self._types = types
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._system_prompt = system_prompt
        self._max_output_tokens = max_output_tokens
        self._tool = types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name=tool.name,
                    description=tool.description,
                    parameters_json_schema=tool.parameters,
                )
                for tool in tools
            ]
        )
        self._contents: list[Any] = [
            types.Content(role="user", parts=[types.Part.from_text(text=user_prompt)])
        ]

    def _http_options(self, deadline: float) -> Any:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise ProviderError("timeout")
        return self._types.HttpOptions(timeout=max(MIN_PROVIDER_TIMEOUT_MS, int(remaining * 1000)))

    def step(self, tool_results: Sequence[ToolResultMessage] | None, *, deadline: float) -> Turn:
        types = self._types
        if tool_results:
            self._contents.append(
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_function_response(name=r.name, response=r.payload)
                        for r in tool_results
                    ],
                )
            )
        config = types.GenerateContentConfig(
            system_instruction=self._system_prompt,
            tools=[self._tool],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="AUTO")
            ),
            max_output_tokens=self._max_output_tokens,
            http_options=self._http_options(deadline),
        )
        try:
            response = self._client.models.generate_content(
                model=self._model, contents=list(self._contents), config=config
            )
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 -- map any SDK failure to a coarse reason
            raise _classify(exc) from None
        candidates = getattr(response, "candidates", None) or []
        if not candidates or getattr(candidates[0], "content", None) is None:
            raise ProviderError("invalid_output")
        # Keep the model turn verbatim: it carries provider-side state needed
        # when function results are sent back.
        self._contents.append(candidates[0].content)
        calls = [
            ToolCall(name=str(call.name), args=dict(call.args or {}))
            for call in (response.function_calls or [])
            if call.name
        ]
        input_tokens, output_tokens = _usage(response)
        return Turn(
            text=None if calls else _safe_text(response),
            tool_calls=calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def final_report(self, prompt: str, schema: dict[str, Any], *, deadline: float) -> Turn:
        types = self._types
        config = types.GenerateContentConfig(
            system_instruction=self._system_prompt,
            response_mime_type="application/json",
            response_json_schema=schema,
            max_output_tokens=self._max_output_tokens,
            http_options=self._http_options(deadline),
        )
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=[types.Content(role="user", parts=[types.Part.from_text(text=prompt)])],
                config=config,
            )
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 -- map any SDK failure to a coarse reason
            raise _classify(exc) from None
        input_tokens, output_tokens = _usage(response)
        return Turn(text=_safe_text(response), input_tokens=input_tokens, output_tokens=output_tokens)


def _safe_text(response: Any) -> str | None:
    """Response text, or None when the SDK cannot produce it (blocked, empty)."""
    try:
        text = response.text
    except Exception:  # noqa: BLE001 -- blocked or empty responses raise in the SDK
        return None
    return text if isinstance(text, str) else None


def create_session(
    settings: Settings,
    system_prompt: str,
    user_prompt: str,
    tools: Sequence[ToolDeclaration],
) -> LlmSession | None:
    """Production session factory: a Gemini session, or None if not configured."""
    if not settings.llm_configured or settings.llm_provider != "gemini":
        return None
    secret = settings.llm_api_key
    if secret is None:
        return None
    return GeminiSession(
        api_key=secret.get_secret_value(),
        model=settings.llm_model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        tools=tools,
        max_output_tokens=settings.llm_max_output_tokens,
    )
