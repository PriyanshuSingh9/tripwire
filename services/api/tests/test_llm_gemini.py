"""Gemini adapter tests against real SDK types with a stubbed client (no network)."""

from __future__ import annotations

import time
from typing import Any, ClassVar

import pytest
from google import genai
from google.genai import types

from api.llm import prompts
from api.llm.gemini import GeminiSession, create_session
from api.llm.provider import ProviderError, ToolResultMessage
from api.settings import Settings

SECRET = "AIza-SECRET-VALUE"


class StubModels:
    def __init__(self, script: list[Any]) -> None:
        self.script = script
        self.requests: list[dict[str, Any]] = []

    def generate_content(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class StubClient:
    last: ClassVar[StubClient | None] = None
    script: ClassVar[list[Any]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.init_kwargs = kwargs
        self.models = StubModels(StubClient.script)
        StubClient.last = self


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> type[StubClient]:
    StubClient.script = []
    StubClient.last = None
    monkeypatch.setattr(genai, "Client", StubClient)
    return StubClient


def response(
    parts: list[types.Part], usage: tuple[int, int, int] = (50, 20, 5)
) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=parts))],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=usage[0],
            candidates_token_count=usage[1],
            thoughts_token_count=usage[2],
        ),
    )


def make_session() -> GeminiSession:
    return GeminiSession(
        api_key=SECRET,
        model="gemini-flash-latest",
        system_prompt="system",
        user_prompt="user",
        tools=prompts.tool_declarations(max_hops=3),
        max_output_tokens=800,
    )


def far_deadline() -> float:
    return time.perf_counter() + 60.0


def test_step_disables_automatic_calling_and_declares_only_approved_tools(
    stub: type[StubClient],
) -> None:
    stub.script = [response([types.Part.from_text(text="done")])]
    session = make_session()
    turn = session.step(None, deadline=far_deadline())
    request = stub.last.models.requests[0]  # type: ignore[union-attr]
    config = request["config"]
    assert request["model"] == "gemini-flash-latest"
    assert config.automatic_function_calling.disable is True
    assert config.system_instruction == "system"
    assert config.max_output_tokens == 800
    declared = [d.name for d in config.tools[0].function_declarations]
    assert declared == [prompts.TRACE_FUNDS, prompts.DETECT_FLOW_PATTERNS]
    assert 1_000 <= config.http_options.timeout <= 60_000
    assert turn.text == "done"
    assert turn.tool_calls == []
    assert (turn.input_tokens, turn.output_tokens) == (50, 25)  # output includes reasoning


def test_function_calls_round_trip_through_history(stub: type[StubClient]) -> None:
    call = types.Part.from_function_call(name="trace_funds", args={"max_hops": 2})
    stub.script = [response([call]), response([types.Part.from_text(text="ok")])]
    session = make_session()
    first = session.step(None, deadline=far_deadline())
    assert [(c.name, c.args) for c in first.tool_calls] == [("trace_funds", {"max_hops": 2})]
    session.step([ToolResultMessage("trace_funds", {"transactions": []})], deadline=far_deadline())
    contents = stub.last.models.requests[1]["contents"]  # type: ignore[union-attr]
    assert [c.role for c in contents] == ["user", "model", "user"]
    assert contents[2].parts[0].function_response.name == "trace_funds"


def test_final_report_requests_json_without_tools(stub: type[StubClient]) -> None:
    stub.script = [response([types.Part.from_text(text='{"hypothesis": "x"}')])]
    session = make_session()
    schema = {"type": "object", "properties": {"hypothesis": {"type": "string"}}}
    turn = session.final_report("prompt", schema, deadline=far_deadline())
    config = stub.last.models.requests[0]["config"]  # type: ignore[union-attr]
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == schema
    assert config.tools is None
    assert turn.text == '{"hypothesis": "x"}'


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (TimeoutError(f"deadline {SECRET}"), "timeout"),
        (RuntimeError(f"boom {SECRET}"), "provider_error"),
        (ConnectionError(SECRET), "provider_error"),
    ],
)
def test_sdk_failures_map_to_coarse_reasons_without_upstream_text(
    stub: type[StubClient], error: Exception, reason: str
) -> None:
    stub.script = [error]
    session = make_session()
    with pytest.raises(ProviderError) as raised:
        session.step(None, deadline=far_deadline())
    assert raised.value.reason == reason
    assert SECRET not in str(raised.value)
    assert raised.value.__cause__ is None


def test_gateway_timeout_status_maps_to_timeout(stub: type[StubClient]) -> None:
    class GatewayTimeout(Exception):
        code = 504

    stub.script = [GatewayTimeout("x")]
    with pytest.raises(ProviderError) as raised:
        make_session().step(None, deadline=far_deadline())
    assert raised.value.reason == "timeout"


def test_expired_deadline_never_calls_the_api(stub: type[StubClient]) -> None:
    session = make_session()
    with pytest.raises(ProviderError) as raised:
        session.step(None, deadline=time.perf_counter() - 1.0)
    assert raised.value.reason == "timeout"
    assert stub.last.models.requests == []  # type: ignore[union-attr]


def test_empty_candidates_are_invalid_output(stub: type[StubClient]) -> None:
    stub.script = [types.GenerateContentResponse(candidates=[])]
    with pytest.raises(ProviderError) as raised:
        make_session().step(None, deadline=far_deadline())
    assert raised.value.reason == "invalid_output"


def test_create_session_requires_provider_and_key(stub: type[StubClient]) -> None:
    off = Settings(_env_file=None, llm_provider="none", llm_api_key=None)
    assert create_session(off, "s", "u", prompts.tool_declarations(2)) is None
    no_key = Settings(_env_file=None, llm_provider="gemini", llm_api_key=None)
    assert create_session(no_key, "s", "u", prompts.tool_declarations(2)) is None
    on = Settings(_env_file=None, llm_provider="gemini", llm_api_key=SECRET, llm_model="m")
    session = create_session(on, "s", "u", prompts.tool_declarations(2))
    assert isinstance(session, GeminiSession)
    assert stub.last.init_kwargs == {"api_key": SECRET}  # type: ignore[union-attr]
