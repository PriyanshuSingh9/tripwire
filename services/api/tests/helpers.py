"""Shared fakes and assertion helpers for API tests (no Docker, no data)."""

from __future__ import annotations

from typing import Any

FORBIDDEN_KEYS = (
    "target_is_laundering",
    "is_laundering",
    "is_illicit",
    "label",
    "typology",
    "pattern",
)


def assert_no_labels(payload: Any) -> None:
    """Recursively assert no label/typology key appears in a JSON payload."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            assert key not in FORBIDDEN_KEYS, f"label key leaked: {key}"
            assert_no_labels(value)
    elif isinstance(payload, list):
        for value in payload:
            assert_no_labels(value)


def make_evidence(transaction_id: str = "tx_1") -> dict[str, Any]:
    """Canned tool evidence for one anchor with a single transfer."""
    return {
        "transactions": [
            {
                "transaction_id": transaction_id,
                "timestamp": "2022-09-01 00:00",
                "source_account_key": "1::a",
                "destination_account_key": "2::b",
                "amount_paid": 10.0,
                "payment_currency": "US Dollar",
                "amount_received": 10.0,
                "receiving_currency": "US Dollar",
                "payment_format": "Wire",
            }
        ],
        "accounts": [{"account_key": "1::a"}, {"account_key": "2::b"}],
        "paths": [],
        "query_duration_ms": 1.0,
    }


class FakeScoreRepository:
    """In-memory score lookup standing in for the parquet artifact."""

    def __init__(self, scores: dict[str, tuple[float, str]] | None = None) -> None:
        self._scores = scores or {
            "tx_1": (0.9, "2022-09-01 00:00"),
            "tx_2": (0.1, "2022-09-01 00:01"),
        }

    def score_for(self, transaction_id: str) -> float | None:
        entry = self._scores.get(transaction_id)
        return entry[0] if entry is not None else None

    def ranked(self, limit: int, offset: int) -> list[tuple[str, float, str]]:
        ordered = sorted(self._scores.items(), key=lambda kv: (-kv[1][0], kv[0]))
        return [
            (transaction_id, score, timestamp)
            for transaction_id, (score, timestamp) in ordered[offset : offset + limit]
        ]

    def __len__(self) -> int:
        return len(self._scores)


class FakeGraphTools:
    """Canned graph tools returning fixed evidence (never touches FalkorDB)."""

    def __init__(
        self,
        evidence: dict[str, Any] | None = None,
        pattern_matches: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        self._evidence = evidence if evidence is not None else make_evidence()
        self._pattern_matches = pattern_matches if pattern_matches is not None else {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def trace_funds(
        self,
        transaction_id: str,
        max_hops: int,
        time_window_seconds: int,
        result_limit: int,
    ) -> dict[str, Any]:
        self.calls.append(
            ("trace_funds",
             {"max_hops": max_hops, "time_window_seconds": time_window_seconds,
              "result_limit": result_limit})
        )
        return {
            **self._evidence,
            "anchor_transaction_id": transaction_id,
            "direction": "both",
            "max_hops": max_hops,
        }

    def inspect_counterparties(
        self,
        transaction_id: str,
        time_window_seconds: int,
        result_limit: int,
    ) -> dict[str, Any]:
        self.calls.append(
            ("inspect_counterparties",
             {"time_window_seconds": time_window_seconds, "result_limit": result_limit})
        )
        return {
            "transactions": [],
            "accounts": [],
            "query_duration_ms": 0.5,
        }

    def detect_flow_patterns(
        self,
        account_key: str,
        pattern: str,
        time_window_seconds: int,
        result_limit: int,
    ) -> dict[str, Any]:
        self.calls.append(
            (f"pattern:{pattern}",
             {"account_key": account_key, "time_window_seconds": time_window_seconds,
              "result_limit": result_limit})
        )
        return {
            "anchor": {"account_key": account_key},
            "pattern": pattern,
            "matches": self._pattern_matches.get(pattern, []),
            "query_duration_ms": 0.25,
        }
