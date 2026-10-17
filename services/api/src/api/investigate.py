"""Investigation orchestration over graph tools and target-free scores.

Flow per request: look up the deterministic risk score, map it through the
locked validation threshold to ``allow``/``review``, run the approved graph
tools, shape their rows through explicit field allowlists (so no upstream
label key can pass through), and attach a deterministic report citing only
returned IDs after verifier approval.

Tool calls run concurrently on a bounded thread pool. ``trace_funds`` and
``inspect_counterparties`` start together (both need only the transaction
ID); the three ``detect_flow_patterns`` variants need the anchor's account
keys and start as soon as either stage-one tool has returned them, so they
overlap the slower traversal. Each worker uses its own pooled FalkorDB
connection. Results are merged in a fixed order, so the response is
identical to serial execution. ``query_duration_ms`` is the wall time of the
whole evidence phase; ``tool_durations_ms`` carries each tool's own measured
duration.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Executor, Future, ThreadPoolExecutor, wait
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from uuid import uuid4

from api import schemas
from api.exceptions import (
    GraphTimeoutError,
    GraphUnavailableError,
    InvestigationError,
    TraceAMLError,
    TransactionNotFoundError,
)
from api.tooltrace import TracedTools, classify_outcome
from api.verifier import EvidenceValidationError, validate_report

POLICY_VERSION = "v1"
REVIEW_THRESHOLD_FALLBACK_NOTE = "lgbm"

# Shared worker pool for graph tool calls. Each investigation uses at most
# three workers at once; the pool bounds total concurrent graph queries.
TOOL_EXECUTOR: Executor = ThreadPoolExecutor(max_workers=32, thread_name_prefix="graph-tool")

PATTERN_ORDER: tuple[Literal["fan_out", "fan_in", "pass_through"], ...] = (
    "fan_out",
    "fan_in",
    "pass_through",
)
PATTERN_TOOL_NAMES: dict[str, schemas.ToolName] = {
    "fan_out": "detect_flow_patterns.fan_out",
    "fan_in": "detect_flow_patterns.fan_in",
    "pass_through": "detect_flow_patterns.pass_through",
}


class GraphTools(Protocol):
    """Seam for graph evidence: production adapter or test fake.

    Implementations must be safe to call from several threads at once.
    """

    def trace_funds(
        self,
        transaction_id: str,
        max_hops: int,
        time_window_seconds: int,
        result_limit: int,
    ) -> dict[str, Any]: ...

    def inspect_counterparties(
        self,
        transaction_id: str,
        time_window_seconds: int,
        result_limit: int,
    ) -> dict[str, Any]: ...

    def detect_flow_patterns(
        self,
        account_key: str,
        pattern: str,
        time_window_seconds: int,
        result_limit: int,
    ) -> dict[str, Any]: ...


class FalkorGraphTools:
    """Production adapter over the traceaml-graph query functions.

    ``graph_provider`` returns a graph handle for the calling thread (see
    ``api.graph.GraphPool.graph``), so concurrent tool calls never share a
    handle while still reusing pooled connections.
    """

    def __init__(self, graph_provider: Callable[[], Any], timeout_ms: int = 5_000) -> None:
        self._graph = graph_provider
        self._timeout_ms = timeout_ms

    @property
    def timeout_ms(self) -> int:
        return self._timeout_ms

    def trace_funds(
        self,
        transaction_id: str,
        max_hops: int,
        time_window_seconds: int,
        result_limit: int,
    ) -> dict[str, Any]:
        from graph.queries import trace_funds

        return trace_funds(
            self._graph(),
            transaction_id,
            direction="both",
            max_hops=max_hops,
            time_window_seconds=time_window_seconds,
            result_limit=result_limit,
            timeout_ms=self._timeout_ms,
        )

    def inspect_counterparties(
        self,
        transaction_id: str,
        time_window_seconds: int,
        result_limit: int,
    ) -> dict[str, Any]:
        from graph.queries import inspect_counterparties

        return inspect_counterparties(
            self._graph(),
            transaction_id=transaction_id,
            time_window_seconds=time_window_seconds,
            result_limit=result_limit,
            timeout_ms=self._timeout_ms,
        )

    def detect_flow_patterns(
        self,
        account_key: str,
        pattern: str,
        time_window_seconds: int,
        result_limit: int,
    ) -> dict[str, Any]:
        from graph.queries import detect_flow_patterns

        return detect_flow_patterns(
            self._graph(),
            account_key=account_key,
            pattern=pattern,
            time_window_seconds=time_window_seconds,
            dwell_seconds=time_window_seconds,
            result_limit=result_limit,
            timeout_ms=self._timeout_ms,
        )


def classify_tool_error(exc: BaseException) -> TraceAMLError:
    """Map a tool failure to a typed domain error with a fixed public detail.

    Connection loss maps to 503, query/socket timeouts to 504, an anchor the
    graph does not hold to 404, and anything else to a generic evidence
    failure. Raw exception text is never propagated.
    """
    if isinstance(exc, TraceAMLError):
        return exc
    try:
        from redis import exceptions as redis_errors
    except ImportError:  # pragma: no cover -- redis ships with falkordb
        redis_errors = None
    if isinstance(exc, FutureTimeoutError):
        return GraphTimeoutError()
    if redis_errors is not None:
        if isinstance(exc, redis_errors.TimeoutError):
            return GraphTimeoutError()
        if isinstance(exc, redis_errors.ConnectionError):
            return GraphUnavailableError()
        if isinstance(exc, redis_errors.ResponseError) and "timed out" in str(exc).lower():
            return GraphTimeoutError()
    if isinstance(exc, ConnectionError):
        return GraphUnavailableError()
    if isinstance(exc, ValueError) and str(exc).startswith("unknown transaction_id"):
        return TransactionNotFoundError("transaction not found in graph")
    return InvestigationError()


def _gather(futures: list[Future[dict[str, Any]]], deadline: float) -> list[dict[str, Any]]:
    """Wait for every future in order; on the first failure cancel the rest."""
    results: list[dict[str, Any]] = []
    try:
        for future in futures:
            remaining = max(0.0, deadline - time.perf_counter())
            results.append(future.result(timeout=remaining))
    except Exception as exc:
        for future in futures:
            future.cancel()
        raise classify_tool_error(exc) from exc
    return results


def _anchor_keys(result: dict[str, Any], transaction_id: str) -> tuple[str, str] | None:
    """Sender and receiver keys of the anchor transfer, if this result holds it."""
    try:
        for row in result.get("transactions", []):
            if str(row["transaction_id"]) == transaction_id:
                return str(row["source_account_key"]), str(row["destination_account_key"])
    except (KeyError, TypeError, AttributeError):
        return None
    return None


def _first_anchor(
    stage_one: list[Future[dict[str, Any]]], transaction_id: str, deadline: float
) -> tuple[str, str] | None:
    """Return anchor keys from whichever stage-one tool finishes first with them.

    Lets pattern queries start as soon as the cheap counterparty lookup
    returns instead of waiting for the slower traversal. Both tools read the
    anchor from the same ``SENT``/``RECEIVED`` edges, so the keys are the
    same whichever tool supplies them.
    """
    pending = set(stage_one)
    try:
        while pending:
            remaining = max(0.0, deadline - time.perf_counter())
            done, pending = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
            if not done:
                raise FutureTimeoutError()
            for future in stage_one:
                if future in done:
                    keys = _anchor_keys(future.result(), transaction_id)
                    if keys is not None:
                        return keys
    except Exception as exc:
        for future in stage_one:
            future.cancel()
        raise classify_tool_error(exc) from exc
    return None


def shape_transaction(row: dict[str, Any]) -> schemas.TransactionEvidence:
    """Copy only evidence fields; unknown keys (labels included) are dropped."""
    return schemas.TransactionEvidence(
        transaction_id=str(row["transaction_id"]),
        timestamp=str(row["timestamp"]),
        source_account_key=str(row["source_account_key"]),
        destination_account_key=str(row["destination_account_key"]),
        amount_paid=row.get("amount_paid"),
        payment_currency=row.get("payment_currency"),
        amount_received=row.get("amount_received"),
        receiving_currency=row.get("receiving_currency"),
        payment_format=row.get("payment_format"),
    )


def shape_path(path: dict[str, Any]) -> schemas.GraphPath:
    """Copy only path references returned by the executed query."""
    return schemas.GraphPath(
        anchor_transaction_id=str(path.get("anchor_transaction_id", "")),
        direction=str(path.get("direction", "both")),
        depth=int(path.get("depth", 0)),
        account_keys=[str(key) for key in path.get("account_keys", [])],
        transaction_ids=[str(tid) for tid in path.get("transaction_ids", [])],
    )


def shape_fan_transfer(
    transfer: dict[str, Any], *, sender_key: str, receiver_key: str
) -> schemas.TransactionEvidence:
    """Shape one fan-match transfer with anchor-derived counterparty keys."""
    return schemas.TransactionEvidence(
        transaction_id=str(transfer["transaction_id"]),
        timestamp=str(transfer["timestamp"]),
        source_account_key=sender_key,
        destination_account_key=receiver_key,
        amount_paid=transfer.get("amount_paid"),
        payment_currency=transfer.get("payment_currency"),
        amount_received=transfer.get("amount_received"),
        receiving_currency=transfer.get("receiving_currency"),
        payment_format=None,
    )


def merge_pattern_matches(
    matches: Any,
    pattern: str,
    anchor_key: str,
    transaction_id: str,
    transactions: dict[str, schemas.TransactionEvidence],
    accounts: dict[str, schemas.AccountEvidence],
    paths: list[schemas.GraphPath],
) -> None:
    """Merge validated pattern references into the shared evidence sets.

    Fan matches group transfers per counterparty; pass-through matches carry
    ordered entry/middle/exit keys with two hops. Every referenced ID is
    added to the evidence sets, so the verifier checks pattern paths against
    the same records as traversal paths. All paths anchor on the
    investigated transaction: fan findings are stars (depth 1) and
    pass-through findings are two-hop chains (depth 2).
    """
    if pattern in ("fan_out", "fan_in"):
        for group in matches:
            counterparty_key = str(group["counterparty_key"])
            transfer_ids: list[str] = []
            for transfer in group.get("transfers", []):
                if pattern == "fan_out":
                    sender_key, receiver_key = anchor_key, counterparty_key
                else:
                    sender_key, receiver_key = counterparty_key, anchor_key
                record = shape_fan_transfer(
                    transfer, sender_key=sender_key, receiver_key=receiver_key
                )
                transactions.setdefault(record.transaction_id, record)
                transfer_ids.append(record.transaction_id)
            accounts.setdefault(anchor_key, schemas.AccountEvidence(account_key=anchor_key))
            accounts.setdefault(
                counterparty_key, schemas.AccountEvidence(account_key=counterparty_key)
            )
            paths.append(
                schemas.GraphPath(
                    anchor_transaction_id=transaction_id,
                    direction=f"pattern:{pattern}",
                    depth=1,
                    account_keys=(
                        [anchor_key, counterparty_key]
                        if pattern == "fan_out"
                        else [counterparty_key, anchor_key]
                    ),
                    transaction_ids=transfer_ids,
                )
            )
    elif pattern == "pass_through":
        for match in matches:
            account_keys = [str(key) for key in match["account_keys"]]
            transaction_ids = [str(tid) for tid in match["transaction_ids"]]
            hops = [
                (match["first_hop"], account_keys[0], account_keys[1]),
                (match["second_hop"], account_keys[1], account_keys[2]),
            ]
            for hop, sender_key, receiver_key in hops:
                record = shape_fan_transfer(
                    hop, sender_key=sender_key, receiver_key=receiver_key
                )
                transactions.setdefault(record.transaction_id, record)
            for key in account_keys:
                accounts.setdefault(key, schemas.AccountEvidence(account_key=key))
            paths.append(
                schemas.GraphPath(
                    anchor_transaction_id=transaction_id,
                    direction="pattern:pass_through",
                    depth=2,
                    account_keys=account_keys,
                    transaction_ids=transaction_ids,
                )
            )
    else:
        raise ValueError(f"unknown pattern: {pattern}")


class EvidenceBuilder:
    """Accumulate shaped, de-duplicated evidence from approved tool results.

    Shared by the deterministic investigation and the LLM agent, so both
    produce evidence through the same field allowlists. Malformed tool rows
    raise a typed ``InvestigationError`` with no upstream text.
    """

    def __init__(self, transaction_id: str) -> None:
        self.transaction_id = transaction_id
        self.transactions: dict[str, schemas.TransactionEvidence] = {}
        self.accounts: dict[str, schemas.AccountEvidence] = {}
        self.paths: list[schemas.GraphPath] = []
        self.tool_durations: list[schemas.ToolDuration] = []

    def add_traversal(
        self,
        tool: Literal["trace_funds", "inspect_counterparties"],
        result: dict[str, Any],
    ) -> None:
        """Merge a ``trace_funds`` or ``inspect_counterparties`` result."""
        try:
            self.tool_durations.append(
                schemas.ToolDuration(
                    tool=tool,
                    query_duration_ms=float(result.get("query_duration_ms", 0.0)),
                )
            )
            for row in result.get("transactions", []):
                record = shape_transaction(row)
                self.transactions.setdefault(record.transaction_id, record)
            for entry in result.get("accounts", []):
                key = str(entry["account_key"])
                self.accounts.setdefault(key, schemas.AccountEvidence(account_key=key))
            self.paths.extend(shape_path(path) for path in result.get("paths", []))
        except (KeyError, TypeError, ValueError) as exc:
            raise InvestigationError() from exc

    def add_pattern(
        self,
        pattern: Literal["fan_out", "fan_in", "pass_through"],
        anchor_key: str,
        found: dict[str, Any],
    ) -> None:
        """Merge one ``detect_flow_patterns`` result anchored at ``anchor_key``."""
        try:
            self.tool_durations.append(
                schemas.ToolDuration(
                    tool=PATTERN_TOOL_NAMES[pattern],
                    query_duration_ms=float(found.get("query_duration_ms", 0.0)),
                )
            )
            merge_pattern_matches(
                found.get("matches", []),
                pattern,
                anchor_key,
                self.transaction_id,
                self.transactions,
                self.accounts,
                self.paths,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise InvestigationError() from exc

    def anchor_keys(self) -> tuple[str, str] | None:
        """Sender and receiver keys of the investigated transfer, once evidenced."""
        record = self.transactions.get(self.transaction_id)
        if record is None:
            return None
        return record.source_account_key, record.destination_account_key


def build_investigation(
    transaction_id: str,
    score: float,
    threshold: float,
    tools: GraphTools,
    max_hops: int,
    time_window_seconds: int,
    result_limit: int,
    *,
    executor: Executor | None = None,
    wait_timeout_seconds: float = 60.0,
) -> schemas.InvestigationResponse:
    """Assemble evidence concurrently, validate the deterministic report, and respond.

    ``wait_timeout_seconds`` bounds the whole evidence phase on top of the
    per-query timeouts the tools enforce; exceeding it is a graph timeout.
    """
    decision: Literal["allow", "review"] = "review" if score >= threshold else "allow"
    pool = executor if executor is not None else TOOL_EXECUTOR
    started = time.perf_counter()
    deadline = started + wait_timeout_seconds

    stage_one = [
        pool.submit(
            tools.trace_funds,
            transaction_id,
            max_hops=max_hops,
            time_window_seconds=time_window_seconds,
            result_limit=result_limit,
        ),
        pool.submit(
            tools.inspect_counterparties,
            transaction_id,
            time_window_seconds=time_window_seconds,
            result_limit=result_limit,
        ),
    ]
    # Pattern findings anchored at the investigated transfer's endpoints:
    # fan-out from the sender, fan-in and pass-through at the receiver.
    # Anchor keys come only from already-returned evidence; the pattern
    # queries start as soon as either stage-one tool has returned them.
    anchor_keys = _first_anchor(stage_one, transaction_id, deadline)
    anchors: dict[str, str] = {}
    pattern_futures: list[Future[dict[str, Any]]] = []
    if anchor_keys is not None:
        sender_key, receiver_key = anchor_keys
        anchors = {"fan_out": sender_key, "fan_in": receiver_key, "pass_through": receiver_key}
        pattern_futures = [
            pool.submit(
                tools.detect_flow_patterns,
                anchors[pattern],
                pattern=pattern,
                time_window_seconds=time_window_seconds,
                result_limit=result_limit,
            )
            for pattern in PATTERN_ORDER
        ]
    gathered = _gather(stage_one + pattern_futures, deadline)
    traced, counterparts = gathered[0], gathered[1]
    found_by_pattern = gathered[2:]

    builder = EvidenceBuilder(transaction_id)
    builder.add_traversal("trace_funds", traced)
    builder.add_traversal("inspect_counterparties", counterparts)
    if found_by_pattern:
        for pattern, found in zip(PATTERN_ORDER, found_by_pattern, strict=True):
            builder.add_pattern(pattern, anchors[pattern], found)
    query_duration_ms = (time.perf_counter() - started) * 1000.0

    transactions = builder.transactions
    accounts = builder.accounts
    paths = builder.paths
    tool_durations = builder.tool_durations
    evidence_ids = set(transactions)
    evidence_keys = set(accounts)
    supporting_ids = sorted(evidence_ids)
    hypothesis = (
        f"{len(evidence_ids)} related transfers across {len(evidence_keys)} "
        f"accounts within the bounded window around {transaction_id}."
    )
    try:
        validate_report(
            supporting_ids,
            [],
            evidence_ids,
            [
                {"transaction_ids": path.transaction_ids, "account_keys": path.account_keys}
                for path in paths
            ],
            evidence_keys,
        )
    except EvidenceValidationError as exc:
        raise InvestigationError("evidence validation failed") from exc

    trace = tools.trace_snapshot() if isinstance(tools, TracedTools) else []
    return schemas.InvestigationResponse(
        investigation_id=f"inv-{uuid4().hex[:12]}",
        transaction_id=transaction_id,
        risk_score=score,
        decision=decision,
        policy_version=POLICY_VERSION,
        evidence=schemas.InvestigationEvidence(
            transactions=list(transactions.values()),
            accounts=list(accounts.values()),
            paths=paths,
            query_duration_ms=query_duration_ms,
            tool_durations_ms=tool_durations,
        ),
        report=schemas.InvestigationReport(
            hypothesis=hypothesis,
            supporting_transaction_ids=supporting_ids,
            counterevidence_ids=[],
            recommendation="review",
            validation="validated",
        ),
        created_at=datetime.now(UTC),
        outcome=classify_outcome(paths, trace),
        tool_trace=trace,
    )
