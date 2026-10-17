"""Opt-in live Gemini smoke test. Skipped unless explicitly enabled.

Enable with a key in the repository ``.env`` (or the environment) and::

    TRACEAML_LIVE_LLM_TEST=1 uv run --directory services/api pytest tests/test_llm_live.py -q -s

It sends one small, synthetic, label-free investigation to the Gemini API and
spends a few thousand tokens. It fails when the provider is unreachable or
rejects the key or model (``provider_error`` / ``timeout`` / ``not_configured``).
A ``verification_failed`` or ``invalid_output`` outcome means the connection
works but the model's draft was rejected by the verifier: the test passes and
prints the reason, because that is a model-quality result, not a setup fault.
"""

from __future__ import annotations

import os

import pytest
from test_llm_agent import ANCHOR, LlmFakeTools

from api.llm import create_session, run_llm_investigation
from api.settings import Settings

ENABLED = os.environ.get("TRACEAML_LIVE_LLM_TEST") == "1"


@pytest.mark.skipif(not ENABLED, reason="set TRACEAML_LIVE_LLM_TEST=1 to call the Gemini API")
def test_live_gemini_investigation_round_trip() -> None:
    settings = Settings()
    if not settings.llm_configured:
        pytest.fail("LLM_PROVIDER=gemini and LLM_API_KEY must be set in .env or the environment")
    tools = LlmFakeTools()
    response = run_llm_investigation(
        ANCHOR,
        0.9,
        0.5,
        tools,
        max_hops=2,
        time_window_seconds=3600,
        result_limit=10,
        settings=settings,
        session_factory=create_session,
    )
    report = response.report
    assert report is not None and report.llm is not None
    info = report.llm
    print(
        f"\nlive gemini: status={info.status} reason={info.fallback_reason} "
        f"model={info.model} rounds={info.rounds} tool_calls={info.tool_calls} "
        f"tokens_in={info.input_tokens} tokens_out={info.output_tokens} "
        f"duration_ms={info.duration_ms:.0f}"
    )
    assert info.fallback_reason not in ("provider_error", "timeout", "not_configured"), (
        "provider call failed: check the key, model name, network, and quota"
    )
    assert response.decision == "review"
    assert report.recommendation == "review"
    if info.status == "completed":
        assert report.source == "llm"
        assert set(report.supporting_transaction_ids) <= {
            t.transaction_id for t in response.evidence.transactions
        }
