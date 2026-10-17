"""Seeded synthetic transaction Parquet for graph-feature tests and benchmarks.

Writes the same 16-column schema as ``ml.prepare_data`` (including
``target_is_laundering`` and ``split``, so tests can prove the feature
builders never read them). Values are random and carry no AML meaning:
labels are uniform noise and nothing here is derived from IBM AMLworld.

Two shapes are provided:

- :func:`adversarial_table`: a small table with hand-built edge cases (exact
  24-hour boundaries, same-minute chains, duplicate rows, self-transfers,
  reverse flows, sub-minute seconds, timestamp ties) mixed with a dense random
  block over a tiny account pool. Used by the FalkorDB parity test.
- :func:`benchmark_table`: a large table with heavy-tailed (Zipf-like)
  sender/receiver degrees, front-loaded daily volume, midnight bursts, and
  self-transfers, sized like HI-Small for timing runs.

Generated files belong under the ignored ``data/interim/synthetic/`` path::

    uv run --project graph traceaml-synth-transactions \\
        --out data/interim/synthetic/hi-small-shape.parquet --rows 5078345 --days 18
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np
    import pyarrow as pa

START_DEFAULT = datetime(2022, 9, 1)
ACCOUNTS_DEFAULT = 515_000
ZIPF_ALPHA_DEFAULT = 0.8
PAYMENT_FORMATS: tuple[str, ...] = (
    "ACH", "Bitcoin", "Cash", "Cheque", "Credit Card", "Reinvestment", "Wire",
)


def prepared_schema() -> pa.Schema:
    """The ``ml.prepare_data`` Parquet schema (kept in sync by hand)."""
    import pyarrow as pa

    return pa.schema(
        [
            ("transaction_id", pa.string()),
            ("timestamp", pa.timestamp("us")),
            ("sender_bank", pa.string()),
            ("sender_account", pa.string()),
            ("receiver_bank", pa.string()),
            ("receiver_account", pa.string()),
            ("sender_account_key", pa.string()),
            ("receiver_account_key", pa.string()),
            ("amount_paid", pa.float64()),
            ("payment_currency", pa.string()),
            ("amount_received", pa.float64()),
            ("receiving_currency", pa.string()),
            ("payment_format", pa.string()),
            ("target_is_laundering", pa.int8()),
            ("source_row_number", pa.int64()),
            ("split", pa.string()),
        ]
    )


def account_pool(count: int, rng: np.random.Generator) -> tuple[pa.Array, pa.Array, pa.Array]:
    """Return ``(banks, accounts, keys)`` string arrays for ``count`` accounts."""
    import pyarrow as pa

    banks = rng.integers(1, 3_000, size=count)
    bank_strings = [f"{int(bank):03d}" for bank in banks]
    account_strings = [f"{index:09X}" for index in range(count)]
    keys = [f"{bank}::{account}" for bank, account in zip(bank_strings, account_strings, strict=True)]
    return pa.array(bank_strings), pa.array(account_strings), pa.array(keys)


def assemble(
    rng: np.random.Generator,
    *,
    timestamps_us: np.ndarray,
    sender_index: np.ndarray,
    receiver_index: np.ndarray,
    pool: tuple[pa.Array, pa.Array, pa.Array],
) -> pa.Table:
    """Build a full prepared-schema table from index arrays."""
    import numpy as np
    import pyarrow as pa

    rows = timestamps_us.size
    banks, accounts, keys = pool
    sender_take = pa.array(sender_index)
    receiver_take = pa.array(receiver_index)
    amounts = np.round(rng.lognormal(mean=7.0, sigma=1.5, size=rows), 2)
    formats = pa.array(PAYMENT_FORMATS).take(pa.array(rng.integers(0, len(PAYMENT_FORMATS), rows)))
    order = np.argsort(timestamps_us, kind="stable")
    rank = np.empty(rows, dtype=np.int64)
    rank[order] = np.arange(rows)
    split = np.where(rank < int(rows * 0.6), "train",
                     np.where(rank < int(rows * 0.8), "validation", "test"))
    usd = pa.repeat("US Dollar", rows)
    columns = {
        "transaction_id": pa.array([f"hi8-tx-{index:08d}" for index in range(1, rows + 1)]),
        "timestamp": pa.array(timestamps_us, type=pa.timestamp("us")),
        "sender_bank": banks.take(sender_take),
        "sender_account": accounts.take(sender_take),
        "receiver_bank": banks.take(receiver_take),
        "receiver_account": accounts.take(receiver_take),
        "sender_account_key": keys.take(sender_take),
        "receiver_account_key": keys.take(receiver_take),
        "amount_paid": pa.array(amounts),
        "payment_currency": usd,
        "amount_received": pa.array(amounts),
        "receiving_currency": usd,
        "payment_format": formats,
        "target_is_laundering": pa.array((rng.random(rows) < 0.001).astype(np.int8)),
        "source_row_number": pa.array(np.arange(1, rows + 1, dtype=np.int64)),
        "split": pa.array(split),
    }
    schema = prepared_schema()
    return pa.Table.from_arrays(
        [columns[field.name].cast(field.type) for field in schema], schema=schema
    )


def micros_of(moment: datetime) -> int:
    """Naive datetime -> epoch microseconds (UTC interpretation)."""
    return int((moment - datetime(1970, 1, 1)) / timedelta(microseconds=1))


def adversarial_table(seed: int = 7, random_rows: int = 2_000) -> pa.Table:
    """Small table of explicit edge cases plus a dense random block."""
    import numpy as np

    rng = np.random.default_rng(seed)
    base = micros_of(START_DEFAULT)
    minute = 60 * 1_000_000
    day = 24 * 60 * minute
    # Hand-built cases over a dedicated account range (indices 0..19).
    a, b, c, d, x1, x2, x3, s, t, r1, r2 = range(11)
    cases: list[tuple[int, int, int]] = [
        # Exact 24 h boundary: b sees a at +24h (inclusive), not at +24h+1m.
        (0, a, b),
        (day, b, c),
        (day + minute, b, d),
        (day, a, c),  # a->c at +24h: a's own a->b edge is exactly on the bound.
        # Same-minute cycle, then the next minute observes it.
        (10 * minute, x1, x2),
        (10 * minute, x2, x3),
        (10 * minute, x3, x1),
        (11 * minute, x1, x2),
        (11 * minute, x2, x1),
        # Self-transfers, duplicated, then reverse via self loop.
        (20 * minute, s, s),
        (20 * minute, s, s),
        (21 * minute, s, s),
        (22 * minute, s, t),
        (23 * minute, t, s),
        # Reverse flow far outside the window is still all-time prior.
        (30 * minute, r1, r2),
        (3 * day + 30 * minute, r2, r1),
        # Sub-minute seconds collapse into one minute group.
        (40 * minute + 59_000_000, a, d),
        (40 * minute, d, a),
        (41 * minute + 1_000_000, d, a),
    ]
    # Exact duplicate rows (distinct IDs) on a boundary minute.
    cases += [(day, c, d)] * 3
    hand_times = np.array([base + offset for offset, _, _ in cases], dtype=np.int64)
    hand_send = np.array([sender for _, sender, _ in cases], dtype=np.int64)
    hand_recv = np.array([receiver for _, _, receiver in cases], dtype=np.int64)

    # Dense random block: 30 accounts (20..49), about 4 days, bursty minutes so
    # many groups contain several rows and windows expire and refill.
    minutes = np.concatenate([
        rng.integers(0, 4 * 24 * 60, size=random_rows // 2),
        rng.choice(np.arange(0, 4 * 24 * 60, 37), size=random_rows - random_rows // 2),
    ])
    seconds = rng.choice([0, 0, 0, 30, 59], size=random_rows)
    rand_times = base + minutes * minute + seconds * 1_000_000
    weights = 1.0 / np.arange(1, 31) ** 1.2
    weights /= weights.sum()
    rand_send = 20 + rng.choice(30, size=random_rows, p=weights)
    rand_recv = 20 + rng.choice(30, size=random_rows, p=weights[::-1])
    self_mask = rng.random(random_rows) < 0.05
    rand_recv[self_mask] = rand_send[self_mask]
    duplicate_from = rng.integers(0, random_rows, size=random_rows // 50)
    duplicate_to = rng.integers(0, random_rows, size=random_rows // 50)
    rand_times[duplicate_to] = rand_times[duplicate_from]
    rand_send[duplicate_to] = rand_send[duplicate_from]
    rand_recv[duplicate_to] = rand_recv[duplicate_from]

    times = np.concatenate([hand_times, rand_times])
    senders = np.concatenate([hand_send, rand_send])
    receivers = np.concatenate([hand_recv, rand_recv])
    shuffle = rng.permutation(times.size)  # file order is not chronological
    return assemble(
        rng,
        timestamps_us=times[shuffle],
        sender_index=senders[shuffle],
        receiver_index=receivers[shuffle],
        pool=account_pool(50, rng),
    )


def benchmark_table(
    rows: int,
    *,
    days: int = 18,
    accounts: int = ACCOUNTS_DEFAULT,
    zipf_alpha: float = ZIPF_ALPHA_DEFAULT,
    seed: int = 20221001,
) -> pa.Table:
    """Large heavy-tailed table shaped like HI-Small (synthetic, random)."""
    import numpy as np

    rng = np.random.default_rng(seed)
    base = micros_of(START_DEFAULT)
    minute = 60 * 1_000_000
    # Front-loaded daily volume: most rows in the first ten days, thin tail.
    day_weights = np.where(np.arange(days) < min(10, days), 1.0, 0.002)
    day_weights /= day_weights.sum()
    day_index = rng.choice(days, size=rows, p=day_weights)
    minute_of_day = rng.integers(0, 24 * 60, size=rows)
    burst = rng.random(rows) < 0.02  # midnight bursts give ~10k-row minutes
    minute_of_day[burst] = 0
    times = base + (day_index * 24 * 60 + minute_of_day) * minute

    def zipf_indices(size: int) -> np.ndarray:
        ranks = np.arange(1, accounts + 1, dtype=np.float64)
        weights = ranks ** (-zipf_alpha)
        cumulative = np.cumsum(weights)
        cumulative /= cumulative[-1]
        draws = np.searchsorted(cumulative, rng.random(size), side="right")
        return rng.permutation(accounts)[np.minimum(draws, accounts - 1)]

    senders = zipf_indices(rows)
    receivers = zipf_indices(rows)
    self_mask = rng.random(rows) < 0.05
    receivers[self_mask] = senders[self_mask]
    duplicate_count = max(rows // 500_000, 1)
    duplicate_from = rng.integers(0, rows, size=duplicate_count)
    duplicate_to = rng.integers(0, rows, size=duplicate_count)
    times[duplicate_to] = times[duplicate_from]
    senders[duplicate_to] = senders[duplicate_from]
    receivers[duplicate_to] = receivers[duplicate_from]
    return assemble(
        rng,
        timestamps_us=times,
        sender_index=senders,
        receiver_index=receivers,
        pool=account_pool(accounts, rng),
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Build the CLI parser and parse arguments."""
    parser = argparse.ArgumentParser(
        description="Write a seeded synthetic transactions Parquet (random values, "
                    "prepared-data schema) for graph-feature tests and benchmarks."
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rows", type=int, default=5_078_345)
    parser.add_argument("--days", type=int, default=18)
    parser.add_argument("--accounts", type=int, default=ACCOUNTS_DEFAULT)
    parser.add_argument("--zipf-alpha", type=float, default=ZIPF_ALPHA_DEFAULT)
    parser.add_argument("--seed", type=int, default=20221001)
    parser.add_argument("--adversarial", action="store_true",
                        help="Write the small adversarial parity table instead.")
    args = parser.parse_args(argv)
    if args.rows <= 0 or args.days <= 0 or args.accounts <= 1:
        parser.error("--rows, --days must be positive and --accounts > 1")
    return args


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    import pyarrow.parquet as pq

    args = parse_args(argv)
    started = time.perf_counter()
    if args.adversarial:
        table = adversarial_table(seed=args.seed)
    else:
        table = benchmark_table(
            args.rows, days=args.days, accounts=args.accounts,
            zipf_alpha=args.zipf_alpha, seed=args.seed,
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, args.out, row_group_size=1_000_000)
    print(json.dumps({
        "out": str(args.out),
        "rows": table.num_rows,
        "seconds": round(time.perf_counter() - started, 3),
        "note": "synthetic random data; not IBM AMLworld",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
