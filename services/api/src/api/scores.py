"""Target-free replay score repository.

Reads per-transaction scores from the published inference artifact. The
artifact must carry ``transaction_id``, ``timestamp``, and ``score`` columns
and must not contain any label, typology, or split column; a violating
artifact fails closed so labels can never flow into API responses. A missing
artifact maps to an explicit service error at the route layer, never a raw
exception.

Ranking is deterministic: score descending with ``transaction_id`` ascending
as the tie-break, so paged queue reads are stable. Loading is cached per
artifact path so the 761k-row file is read once per process, not per
request; a changed path naturally loads a fresh repository.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

REQUIRED_COLUMNS: tuple[str, ...] = ("transaction_id", "timestamp", "score")
FORBIDDEN_COLUMNS: tuple[str, ...] = (
    "target_is_laundering",
    "is_laundering",
    "is_illicit",
    "label",
    "typology",
    "pattern",
    "split",
)


class ScoreArtifactUnavailable(Exception):
    """Raised when the score artifact is missing or unreadable."""


class ScoreArtifactInvalid(Exception):
    """Raised when the score artifact violates the target-free schema."""


class ScoreRepository:
    """In-memory lookup over the target-free inference score artifact."""

    def __init__(self, entries: dict[str, tuple[float, str]]) -> None:
        self._scores = {key: score for key, (score, _) in entries.items()}
        self._timestamps = {key: timestamp for key, (_, timestamp) in entries.items()}
        self._ranked = sorted(entries.items(), key=lambda kv: (-kv[1][0], kv[0]))

    @classmethod
    def load(cls, path: Path) -> ScoreRepository:
        """Load and validate the artifact; fail closed on any violation."""
        import pyarrow.parquet as pq

        if not path.is_file():
            raise ScoreArtifactUnavailable(f"score artifact not found: {path}")
        try:
            schema_names = pq.read_schema(path).names
        except Exception as exc:
            raise ScoreArtifactUnavailable(f"score artifact unreadable: {path}") from exc
        missing = [name for name in REQUIRED_COLUMNS if name not in schema_names]
        if missing:
            raise ScoreArtifactInvalid(f"score artifact missing columns: {missing}")
        leaked = [name for name in FORBIDDEN_COLUMNS if name in schema_names]
        if leaked:
            raise ScoreArtifactInvalid(f"score artifact carries label columns: {leaked}")
        table = pq.read_table(path, columns=list(REQUIRED_COLUMNS))
        columns = table.to_pydict()
        entries = {
            str(transaction_id): (float(score), str(timestamp))
            for transaction_id, timestamp, score in zip(
                columns["transaction_id"], columns["timestamp"], columns["score"]
            )
        }
        return cls(entries)

    def score_for(self, transaction_id: str) -> float | None:
        """Return the score or None when the transaction is not scored."""
        return self._scores.get(transaction_id)

    def timestamp_for(self, transaction_id: str) -> str | None:
        """Return the queued timestamp or None when not scored."""
        return self._timestamps.get(transaction_id)

    def ranked(self, limit: int, offset: int) -> list[tuple[str, float, str]]:
        """Return (transaction_id, score, timestamp) ranked by risk, paged."""
        return [
            (transaction_id, score, self._timestamps[transaction_id])
            for transaction_id, (score, _) in self._ranked[offset : offset + limit]
        ]

    def __len__(self) -> int:
        return len(self._scores)


@lru_cache(maxsize=8)
def load_scores(path_str: str) -> ScoreRepository:
    """Cached artifact loader keyed by path; isolates file IO for tests."""
    return ScoreRepository.load(Path(path_str))
