"""MCP server over the approved graph tools, and the bridge the agent calls it through.

The server exposes exactly three read-only tools, ``trace_funds``,
``inspect_counterparties`` and ``detect_flow_patterns``. They delegate to the
existing ``GraphTools`` (the parameterized Cypher in ``graph.queries``); no
query text is written here and no model-provided string reaches Cypher.
Arguments are bounded twice: by the published tool schema, and again by the
``graph.queries`` validators before any database call.

Every result is a typed envelope: the tool's own result plus an ``outcome``
(``evidence_found``, ``no_matching_path``, ``unknown_anchor`` or
``inconclusive``), a ``tool_run_id``, the duration, counts, and a truncation
flag.

``McpGraphTools`` is the client side. It implements ``GraphTools`` by calling
the server through a real MCP client session (in-memory transport), so the
agent runtime executes graph tools through the MCP boundary and not through a
direct function call. ``python -m api.mcp_graph`` runs the same server over
stdio for external MCP clients.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import timedelta
from typing import Annotated, Any, Literal

import anyio.to_thread
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import Field

from api import schemas
from api.exceptions import (
    GraphTimeoutError,
    GraphUnavailableError,
    InvestigationError,
    TraceAMLError,
    TransactionNotFoundError,
)
from api.investigate import GraphTools, classify_tool_error
from api.tooltrace import (
    TracedTools,
    error_outcome,
    new_tool_run_id,
    summarize_result,
    trace_arguments,
)

logging.getLogger("mcp").setLevel(logging.WARNING)

SERVER_NAME = "traceaml-graph"
Pattern = Literal["fan_out", "fan_in", "pass_through"]
ErrorKind = Literal["unknown_anchor", "timeout", "unavailable", "invalid_input", "failed"]

TransactionId = Annotated[str, Field(min_length=1, max_length=128)]
AccountKey = Annotated[str, Field(min_length=3, max_length=160, pattern=r"^[^:]+::[^:]+$")]
Hops = Annotated[int, Field(ge=1, le=3, description="Traversal depth, 1 to 3.")]
Window = Annotated[
    int, Field(ge=1, le=2_592_000, description="Look-back/forward window in seconds.")
]
Limit = Annotated[int, Field(ge=1, le=100, description="Maximum records returned.")]

_ERROR_KINDS: dict[type[TraceAMLError], ErrorKind] = {
    TransactionNotFoundError: "unknown_anchor",
    GraphTimeoutError: "timeout",
    GraphUnavailableError: "unavailable",
}


def _error_kind(exc: BaseException) -> ErrorKind:
    domain = classify_tool_error(exc)
    for error_type, kind in _ERROR_KINDS.items():
        if isinstance(domain, error_type):
            return kind
    return "failed"


async def _run(
    tool: Literal["trace_funds", "inspect_counterparties", "detect_flow_patterns"],
    result_limit: int,
    call: Any,
) -> dict[str, Any]:
    """Run one blocking graph call off the event loop and wrap it in the envelope."""
    run_id = new_tool_run_id()
    started = time.perf_counter()
    try:
        result: dict[str, Any] = await anyio.to_thread.run_sync(call)
    except Exception as exc:  # noqa: BLE001 -- every failure becomes a typed envelope
        return {
            "tool_run_id": run_id,
            "outcome": error_outcome(exc),
            "error_kind": _error_kind(exc),
            "duration_ms": (time.perf_counter() - started) * 1000.0,
            "result_count": 0,
            "path_count": 0,
            "truncated": False,
            "result": None,
        }
    outcome, count, paths, truncated = summarize_result(tool, result, result_limit)
    return {
        "tool_run_id": run_id,
        "outcome": outcome,
        "error_kind": None,
        "duration_ms": (time.perf_counter() - started) * 1000.0,
        "result_count": count,
        "path_count": paths,
        "truncated": truncated,
        "result": result,
    }


def build_server(tools: GraphTools) -> FastMCP:
    """MCP server whose tools delegate to ``tools`` after schema validation."""
    server = FastMCP(SERVER_NAME)

    @server.tool(
        name="trace_funds",
        description=(
            "Trace bounded money flow before and after a scored transaction. "
            "Returns transfers, accounts and ordered paths from FalkorDB."
        ),
    )
    async def trace_funds(
        transaction_id: TransactionId,
        max_hops: Hops = 2,
        time_window_seconds: Window = 86_400,
        result_limit: Limit = 50,
    ) -> dict[str, Any]:
        return await _run(
            "trace_funds",
            result_limit,
            lambda: tools.trace_funds(
                transaction_id,
                max_hops=max_hops,
                time_window_seconds=time_window_seconds,
                result_limit=result_limit,
            ),
        )

    @server.tool(
        name="inspect_counterparties",
        description=(
            "List the transfers around a scored transaction's accounts within a "
            "bounded window, with counterparties."
        ),
    )
    async def inspect_counterparties(
        transaction_id: TransactionId,
        time_window_seconds: Window = 86_400,
        result_limit: Limit = 50,
    ) -> dict[str, Any]:
        return await _run(
            "inspect_counterparties",
            result_limit,
            lambda: tools.inspect_counterparties(
                transaction_id,
                time_window_seconds=time_window_seconds,
                result_limit=result_limit,
            ),
        )

    @server.tool(
        name="detect_flow_patterns",
        description=(
            "Detect one explicitly implemented structural pattern at an account: "
            "fan_out, fan_in or pass_through. Other pattern names are rejected."
        ),
    )
    async def detect_flow_patterns(
        account_key: AccountKey,
        pattern: Pattern,
        time_window_seconds: Window = 86_400,
        result_limit: Limit = 50,
    ) -> dict[str, Any]:
        return await _run(
            "detect_flow_patterns",
            result_limit,
            lambda: tools.detect_flow_patterns(
                account_key,
                pattern=pattern,
                time_window_seconds=time_window_seconds,
                result_limit=result_limit,
            ),
        )

    return server


class McpGraphTools(TracedTools):
    """``GraphTools`` implemented as calls to the MCP server above.

    A private event loop thread hosts the MCP client session, so the
    synchronous investigation code (and its worker threads) can call tools
    while the protocol stays asynchronous. Close it when the request ends.
    """

    transport: schemas.ToolTransport = "mcp"

    def __init__(self, inner: GraphTools, *, call_timeout_seconds: float = 60.0) -> None:
        super().__init__()
        self._call_timeout = call_timeout_seconds
        self._server = build_server(inner)
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._failed: BaseException | None = None
        self._session: Any = None
        self._stop: asyncio.Event | None = None
        self._thread = threading.Thread(target=self._serve, name="mcp-bridge", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=10.0) or self._session is None:
            self.close()
            raise InvestigationError("tool server unavailable") from self._failed

    def _serve(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except BaseException as exc:  # noqa: BLE001 -- surfaced to the constructor
            self._failed = exc
            self._ready.set()
        finally:
            self._loop.close()

    async def _main(self) -> None:
        self._stop = asyncio.Event()
        async with create_connected_server_and_client_session(
            self._server._mcp_server
        ) as session:
            self._session = session
            self._ready.set()
            await self._stop.wait()

    def close(self) -> None:
        stop = self._stop
        if stop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(stop.set)
        self._thread.join(timeout=5.0)

    def list_tool_names(self) -> list[str]:
        """Tool names the server advertises (used to prove the real boundary)."""
        future = asyncio.run_coroutine_threadsafe(self._session.list_tools(), self._loop)
        return [tool.name for tool in future.result(timeout=self._call_timeout).tools]

    def _invoke(
        self,
        tool: Literal["trace_funds", "inspect_counterparties", "detect_flow_patterns"],
        arguments: dict[str, Any],
        trace_args: dict[str, schemas.TraceArgument],
    ) -> dict[str, Any]:
        origin = self.origin
        started = time.perf_counter()
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._session.call_tool(
                    tool,
                    arguments,
                    read_timeout_seconds=timedelta(seconds=self._call_timeout),
                ),
                self._loop,
            )
            reply = future.result(timeout=self._call_timeout + 5.0)
        except TimeoutError as exc:
            self._record_failure(tool, trace_args, origin, started, "inconclusive")
            raise GraphTimeoutError() from exc
        except Exception as exc:
            self._record_failure(tool, trace_args, origin, started, "inconclusive")
            raise InvestigationError() from exc
        if reply.isError:
            # Schema validation rejected the arguments; no graph query ran.
            self._record_failure(tool, trace_args, origin, started, "invalid_input")
            raise InvestigationError("tool input rejected")
        envelope = _envelope(reply)
        run_id = str(envelope["tool_run_id"])
        error_kind = envelope.get("error_kind")
        entry = schemas.ToolTraceEntry(
            tool_run_id=run_id,
            tool=tool,
            transport=self.transport,
            origin=origin,
            arguments=trace_args,
            outcome=envelope["outcome"],
            status="error" if error_kind else "ok",
            duration_ms=(time.perf_counter() - started) * 1000.0,
            result_count=int(envelope["result_count"]),
            path_count=int(envelope["path_count"]),
            truncated=bool(envelope["truncated"]),
        )
        self.record(entry)
        if error_kind is not None:
            raise _error_from_kind(str(error_kind))
        result = envelope["result"]
        if not isinstance(result, dict):
            raise InvestigationError()
        return result

    def _record_failure(
        self,
        tool: Literal["trace_funds", "inspect_counterparties", "detect_flow_patterns"],
        trace_args: dict[str, schemas.TraceArgument],
        origin: schemas.ToolOrigin,
        started: float,
        outcome: schemas.ToolRunOutcome,
    ) -> None:
        self.record(
            schemas.ToolTraceEntry(
                tool_run_id=new_tool_run_id(),
                tool=tool,
                transport=self.transport,
                origin=origin,
                arguments=trace_args,
                outcome=outcome,
                status="error",
                duration_ms=(time.perf_counter() - started) * 1000.0,
                result_count=0,
                path_count=0,
                truncated=False,
            )
        )

    def trace_funds(
        self, transaction_id: str, max_hops: int, time_window_seconds: int, result_limit: int
    ) -> dict[str, Any]:
        arguments = {
            "transaction_id": transaction_id,
            "max_hops": max_hops,
            "time_window_seconds": time_window_seconds,
            "result_limit": result_limit,
        }
        return self._invoke("trace_funds", arguments, trace_arguments(**arguments))

    def inspect_counterparties(
        self, transaction_id: str, time_window_seconds: int, result_limit: int
    ) -> dict[str, Any]:
        arguments = {
            "transaction_id": transaction_id,
            "time_window_seconds": time_window_seconds,
            "result_limit": result_limit,
        }
        return self._invoke("inspect_counterparties", arguments, trace_arguments(**arguments))

    def detect_flow_patterns(
        self, account_key: str, pattern: str, time_window_seconds: int, result_limit: int
    ) -> dict[str, Any]:
        arguments = {
            "account_key": account_key,
            "pattern": pattern,
            "time_window_seconds": time_window_seconds,
            "result_limit": result_limit,
        }
        trace_args = trace_arguments(
            account_key=account_key,
            flow_pattern=pattern,
            time_window_seconds=time_window_seconds,
            result_limit=result_limit,
        )
        return self._invoke("detect_flow_patterns", arguments, trace_args)


def _envelope(reply: Any) -> dict[str, Any]:
    """Decode the server envelope from structured or text content."""
    structured = getattr(reply, "structuredContent", None)
    if isinstance(structured, dict):
        payload: Any = structured.get("result", structured)
        if isinstance(payload, dict) and "tool_run_id" in payload:
            return payload
    for block in reply.content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            try:
                decoded = json.loads(text)
            except ValueError:
                continue
            if isinstance(decoded, dict) and "tool_run_id" in decoded:
                return decoded
    raise InvestigationError()


def _error_from_kind(kind: str) -> TraceAMLError:
    if kind == "unknown_anchor":
        return TransactionNotFoundError("transaction not found in graph")
    if kind == "timeout":
        return GraphTimeoutError()
    if kind == "unavailable":
        return GraphUnavailableError()
    return InvestigationError()


def main() -> None:
    """Serve the graph tools over stdio for external MCP clients."""
    from api.graph import get_graph_pool
    from api.investigate import FalkorGraphTools
    from api.settings import Settings

    settings = Settings()
    pool = get_graph_pool(
        settings.falkordb_host,
        settings.falkordb_port,
        settings.graph_name,
        max_connections=settings.graph_pool_size,
        query_timeout_ms=settings.graph_query_timeout_ms,
    )
    build_server(FalkorGraphTools(pool.graph, timeout_ms=settings.graph_query_timeout_ms)).run()


if __name__ == "__main__":
    main()
