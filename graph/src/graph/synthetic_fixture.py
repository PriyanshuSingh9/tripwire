"""Seeded synthetic latency fixture for the TraceAML graph layer.

This module writes a Parquet file with exactly the columns and dtypes of the
normalized ``transactions.parquet`` produced by ``ml/src/ml/prepare_data.py``
so the graph loader, queries, and benchmark can be exercised at realistic
scale without the real dataset.

It is a **synthetic latency fixture**, not a model of HI-Small. Numbers
measured on it describe query and load mechanics on a graph of similar size
and degree skew; they are not comparable claims about IBM AMLworld HI-Small
or about AML detection quality.

Label isolation: the fixture carries no labels. ``target_is_laundering`` and
``split`` exist only so the schema matches the normalized table; every value
in them is null. Planted structures (fan-out hubs, fan-in hubs, pass-through
chains) exist so that the pattern queries return non-empty results; their
membership is never written to the Parquet file or the graph. The manifest
records aggregate counts only.

Shape:

- accounts keyed ``{bank}::{account}`` (bank IDs keep leading zeroes,
  account IDs are 9-character upper-case hex), spread over many banks with a
  skewed bank-size distribution;
- heavy-tailed sender/receiver activity (a uniform base plus a Zipf-like
  component), repeated counterparty pairs, and self "Reinvestment" transfers;
- minute-resolution timestamps with a diurnal profile over ``days`` days,
  optionally front-loaded onto the first ``active_days`` (HI-Small style);
- several currencies and payment formats, with a small share of
  cross-currency transfers;
- rows sorted by timestamp, ``source_row_number`` 1..N, and stable
  ``syn-tx-{source_row_number:08d}`` transaction IDs.

Reproducible entries (from the repository root)::

    uv run --project graph traceaml-synthetic-fixture --preset demo-1m
    uv run --project graph traceaml-synthetic-fixture --preset full-5m
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

OUTPUT_DIR_DEFAULT = Path("data/interim/synthetic")
START_DATE = np.datetime64("2022-09-01T00:00", "m")
ID_PREFIX = "syn-tx-"

#: Column order and dtypes of the normalized ``transactions.parquet``.
PARQUET_SCHEMA = pa.schema(
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

CURRENCIES: tuple[str, ...] = (
    "US Dollar", "Euro", "Yuan", "Yen", "UK Pound", "Rupee", "Ruble",
    "Canadian Dollar", "Australian Dollar", "Swiss Franc", "Mexican Peso",
    "Brazil Real", "Shekel", "Saudi Riyal", "Bitcoin",
)
#: Approximate units per US Dollar; only used to keep cross-currency
#: received amounts plausible. Not a valuation basis.
USD_RATES: tuple[float, ...] = (
    1.0, 0.85, 6.7, 105.0, 0.73, 74.0, 72.0, 1.25, 1.35, 0.92, 20.0,
    5.5, 3.2, 3.75, 0.000085,
)
CURRENCY_WEIGHTS: tuple[float, ...] = (
    0.36, 0.22, 0.07, 0.05, 0.04, 0.04, 0.03, 0.03, 0.03, 0.02, 0.03,
    0.03, 0.02, 0.02, 0.01,
)
PAYMENT_FORMATS: tuple[str, ...] = (
    "Cheque", "Credit Card", "ACH", "Cash", "Wire", "Bitcoin", "Reinvestment",
)
FORMAT_WEIGHTS: tuple[float, ...] = (0.36, 0.25, 0.17, 0.10, 0.08, 0.04, 0.0)


@dataclass(frozen=True)
class FixtureSpec:
    """Generator parameters. Everything is derived from these and ``seed``."""

    transactions: int
    accounts: int
    days: int
    active_days: int
    banks: int
    seed: int
    zipf_exponent: float = 1.15
    zipf_offset: float = 8.0
    heavy_share: float = 0.45
    repeat_pair_share: float = 0.25
    self_transfer_share: float = 0.04
    cross_currency_share: float = 0.03
    tail_day_share: float = 0.004
    planted_per_million: int = 150


PRESETS: dict[str, FixtureSpec] = {
    # Mirrors the size of the two-day demo slice (998,021 tx, 354,660 accounts).
    "demo-1m": FixtureSpec(
        transactions=1_000_000, accounts=380_000, days=2, active_days=2,
        banks=4_000, seed=20_221_001,
    ),
    # Mirrors full HI-Small size (5,078,345 tx over 18 days, bulk in ~10 days).
    "full-5m": FixtureSpec(
        transactions=5_078_345, accounts=550_000, days=18, active_days=10,
        banks=4_000, seed=20_221_018,
    ),
    # Small smoke fixture for tests and quick checks.
    "smoke-20k": FixtureSpec(
        transactions=20_000, accounts=6_000, days=2, active_days=2,
        banks=200, seed=7,
    ),
}


def _account_strings(spec: FixtureSpec, rng: np.random.Generator) -> tuple[
    np.ndarray, np.ndarray, np.ndarray, np.ndarray
]:
    """Return per-account bank, account, key strings and home-currency index."""
    bank_rank_weights = 1.0 / np.power(np.arange(1, spec.banks + 1, dtype=np.float64), 0.9)
    bank_rank_weights /= bank_rank_weights.sum()
    bank_of_account = rng.choice(spec.banks, size=spec.accounts, p=bank_rank_weights)
    # Bank IDs keep leading zeroes for small IDs (``"007"``) like the source.
    bank_labels = np.array(
        [f"{b:03d}" if b < 1000 else str(b) for b in range(spec.banks)], dtype=object
    )
    # Odd multiplier mod 2**36 is a bijection, so account IDs are unique.
    multiplier = 0x9E3779B1
    offset = int(rng.integers(0, 2**36))
    account_numbers = (np.arange(spec.accounts, dtype=np.uint64) * np.uint64(multiplier)
                       + np.uint64(offset)) % np.uint64(2**36)
    account_labels = np.array([f"{int(v):09X}" for v in account_numbers], dtype=object)
    banks = bank_labels[bank_of_account]
    keys = np.array([f"{b}::{a}" for b, a in zip(banks, account_labels)], dtype=object)
    bank_currency = rng.choice(len(CURRENCIES), size=spec.banks, p=_normalized(CURRENCY_WEIGHTS))
    home_currency = bank_currency[bank_of_account]
    return banks, account_labels, keys, home_currency


def _normalized(weights: tuple[float, ...]) -> np.ndarray:
    array = np.asarray(weights, dtype=np.float64)
    return array / array.sum()


def _activity_sampler(
    spec: FixtureSpec, rng: np.random.Generator, permutation: np.ndarray, size: int
) -> np.ndarray:
    """Sample ``size`` account indices: uniform base plus a Zipf-like tail."""
    ranks = np.arange(spec.accounts, dtype=np.float64)
    zipf = np.power(ranks + spec.zipf_offset, -spec.zipf_exponent)
    zipf /= zipf.sum()
    heavy = rng.random(size) < spec.heavy_share
    out = rng.integers(0, spec.accounts, size=size)
    n_heavy = int(heavy.sum())
    out[heavy] = permutation[rng.choice(spec.accounts, size=n_heavy, p=zipf)]
    return out


def _minute_offsets(spec: FixtureSpec, rng: np.random.Generator, size: int) -> np.ndarray:
    """Minute offsets from ``START_DATE`` with a diurnal and day profile."""
    day_weights = np.full(spec.days, spec.tail_day_share, dtype=np.float64)
    day_weights[: spec.active_days] = 1.0
    day_weights /= day_weights.sum()
    days = rng.choice(spec.days, size=size, p=day_weights)
    hours = np.arange(24, dtype=np.float64)
    diurnal = 1.0 + 0.8 * np.sin((hours - 8.0) / 24.0 * 2.0 * np.pi)
    hour = rng.choice(24, size=size, p=diurnal / diurnal.sum())
    minute = rng.integers(0, 60, size=size)
    return days * 1440 + hour * 60 + minute


def _amounts(rng: np.random.Generator, size: int) -> np.ndarray:
    return np.round(rng.lognormal(mean=7.0, sigma=1.9, size=size), 2) + 0.01


@dataclass
class _Rows:
    sender: np.ndarray
    receiver: np.ndarray
    minute: np.ndarray
    amount: np.ndarray
    fmt: np.ndarray


def _planted(spec: FixtureSpec, rng: np.random.Generator) -> tuple[_Rows, dict[str, int]]:
    """Plant fan-out, fan-in, and multi-hop pass-through structures.

    Each structure draws its accounts from the shared pool and is placed at
    a random time; only aggregate counts are reported.
    """
    per_kind = max(3, spec.planted_per_million * spec.transactions // 1_000_000)
    horizon = spec.active_days * 1440 - 1440
    sender: list[int] = []
    receiver: list[int] = []
    minute: list[int] = []
    counts = {"fan_out": 0, "fan_in": 0, "pass_through": 0, "rows": 0}
    for kind in ("fan_out", "fan_in"):
        for _ in range(per_kind):
            hub = int(rng.integers(0, spec.accounts))
            fan = int(rng.integers(4, 13))
            others = rng.choice(spec.accounts, size=fan, replace=False)
            start = int(rng.integers(60, max(61, horizon)))
            times = start + np.sort(rng.integers(0, 360, size=fan))
            for other, at in zip(others, times):
                if kind == "fan_out":
                    sender.append(hub)
                    receiver.append(int(other))
                else:
                    sender.append(int(other))
                    receiver.append(hub)
                minute.append(int(at))
            counts[kind] += 1
    for _ in range(per_kind):
        length = int(rng.integers(2, 5))  # 2-4 transfers, i.e. 1-3 pass-through hops
        chain = rng.choice(spec.accounts, size=length + 1, replace=False)
        at = int(rng.integers(60, max(61, horizon)))
        for i in range(length):
            sender.append(int(chain[i]))
            receiver.append(int(chain[i + 1]))
            minute.append(at)
            at += int(rng.integers(5, 720))
        counts["pass_through"] += 1
    size = len(sender)
    counts["rows"] = size
    base_amount = _amounts(rng, size)
    rows = _Rows(
        sender=np.asarray(sender, dtype=np.int64),
        receiver=np.asarray(receiver, dtype=np.int64),
        minute=np.asarray(minute, dtype=np.int64),
        amount=base_amount,
        fmt=rng.choice(len(PAYMENT_FORMATS), size=size, p=_normalized(FORMAT_WEIGHTS)),
    )
    return rows, counts


def _background(spec: FixtureSpec, rng: np.random.Generator, size: int) -> _Rows:
    send_perm = rng.permutation(spec.accounts)
    # Receivers share half of the sender ranking so hubs tend to be active
    # in both directions, as in real payment networks.
    recv_perm = send_perm.copy()
    mixed = rng.random(spec.accounts) < 0.5
    recv_perm[mixed] = rng.permutation(recv_perm[mixed])
    sender = _activity_sampler(spec, rng, send_perm, size)
    receiver = _activity_sampler(spec, rng, recv_perm, size)
    # Repeated relationships: copy an earlier-or-later pair.
    repeat = rng.random(size) < spec.repeat_pair_share
    source = rng.integers(0, size, size=int(repeat.sum()))
    sender[repeat] = sender[source]
    receiver[repeat] = receiver[source]
    fmt = rng.choice(len(PAYMENT_FORMATS), size=size, p=_normalized(FORMAT_WEIGHTS))
    self_transfer = rng.random(size) < spec.self_transfer_share
    receiver[self_transfer] = sender[self_transfer]
    fmt[self_transfer] = PAYMENT_FORMATS.index("Reinvestment")
    return _Rows(
        sender=sender,
        receiver=receiver,
        minute=_minute_offsets(spec, rng, size),
        amount=_amounts(rng, size),
        fmt=fmt,
    )


def generate(spec: FixtureSpec, output: Path, *, chunk_rows: int = 250_000) -> dict[str, Any]:
    """Generate the fixture and write it to ``output``; return a manifest."""
    started = time.perf_counter()
    rng = np.random.default_rng(spec.seed)
    banks, account_ids, keys, home_currency = _account_strings(spec, rng)
    planted, planted_counts = _planted(spec, rng)
    if planted_counts["rows"] >= spec.transactions:
        raise ValueError("transactions too small for the planted structures")
    background = _background(spec, rng, spec.transactions - planted_counts["rows"])

    sender = np.concatenate([background.sender, planted.sender])
    receiver = np.concatenate([background.receiver, planted.receiver])
    minute = np.concatenate([background.minute, planted.minute])
    minute = np.minimum(minute, spec.days * 1440 - 1)
    amount_paid = np.concatenate([background.amount, planted.amount])
    fmt = np.concatenate([background.fmt, planted.fmt])
    order = np.lexsort((rng.random(len(minute)), minute))
    sender, receiver, minute, amount_paid, fmt = (
        sender[order], receiver[order], minute[order], amount_paid[order], fmt[order]
    )
    n = len(sender)

    pay_cur = home_currency[sender]
    recv_cur = pay_cur.copy()
    cross = rng.random(n) < spec.cross_currency_share
    recv_cur[cross] = home_currency[receiver[cross]]
    rates = np.asarray(USD_RATES, dtype=np.float64)
    amount_received = np.round(amount_paid * rates[recv_cur] / rates[pay_cur], 2)
    amount_received = np.maximum(amount_received, 0.01)

    timestamps = (START_DATE + minute.astype("timedelta64[m]")).astype("datetime64[us]")
    currency_names = np.array(CURRENCIES, dtype=object)
    format_names = np.array(PAYMENT_FORMATS, dtype=object)

    output.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with pq.ParquetWriter(output, PARQUET_SCHEMA, compression="zstd") as writer:
        for lo in range(0, n, chunk_rows):
            hi = min(n, lo + chunk_rows)
            row_numbers = np.arange(lo + 1, hi + 1, dtype=np.int64)
            s, r = sender[lo:hi], receiver[lo:hi]
            ids = [f"{ID_PREFIX}{int(v):08d}" for v in row_numbers]
            table = pa.Table.from_arrays(
                [
                    pa.array(ids, type=pa.string()),
                    pa.array(timestamps[lo:hi], type=pa.timestamp("us")),
                    pa.array(banks[s], type=pa.string()),
                    pa.array(account_ids[s], type=pa.string()),
                    pa.array(banks[r], type=pa.string()),
                    pa.array(account_ids[r], type=pa.string()),
                    pa.array(keys[s], type=pa.string()),
                    pa.array(keys[r], type=pa.string()),
                    pa.array(amount_paid[lo:hi], type=pa.float64()),
                    pa.array(currency_names[pay_cur[lo:hi]], type=pa.string()),
                    pa.array(amount_received[lo:hi], type=pa.float64()),
                    pa.array(currency_names[recv_cur[lo:hi]], type=pa.string()),
                    pa.array(format_names[fmt[lo:hi]], type=pa.string()),
                    pa.nulls(hi - lo, type=pa.int8()),
                    pa.array(row_numbers, type=pa.int64()),
                    pa.nulls(hi - lo, type=pa.string()),
                ],
                schema=PARQUET_SCHEMA,
            )
            writer.write_table(table)
            digest.update(np.ascontiguousarray(s).tobytes())
            digest.update(np.ascontiguousarray(r).tobytes())
            digest.update(np.ascontiguousarray(minute[lo:hi]).tobytes())

    out_degree = np.bincount(sender, minlength=spec.accounts)
    in_degree = np.bincount(receiver, minlength=spec.accounts)
    total_degree = out_degree + in_degree
    active = total_degree > 0
    return {
        "kind": "synthetic-latency-fixture",
        "disclaimer": (
            "Synthetic latency fixture. Not derived from HI-Small; numbers measured "
            "on it are not claims about HI-Small or AML detection quality. "
            "Carries no labels: target_is_laundering and split are all null."
        ),
        "spec": asdict(spec),
        "output": output.as_posix(),
        "rows": n,
        "accounts_active": int(active.sum()),
        "max_out_degree": int(out_degree.max()),
        "max_in_degree": int(in_degree.max()),
        "median_active_degree": float(np.median(total_degree[active])),
        "p99_active_degree": float(np.percentile(total_degree[active], 99)),
        "self_transfers": int((sender == receiver).sum()),
        "first_timestamp": str(timestamps[0]),
        "last_timestamp": str(timestamps[-1]),
        "planted_structure_counts": planted_counts,
        "structure_sha256": digest.hexdigest(),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Write a seeded synthetic latency fixture with the normalized "
            "transactions.parquet schema. No labels; not HI-Small."
        )
    )
    parser.add_argument("--preset", choices=sorted(PRESETS), default="demo-1m")
    parser.add_argument("--transactions", type=int, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.transactions is not None and args.transactions <= 0:
        parser.error("--transactions must be positive")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    spec = PRESETS[args.preset]
    overrides: dict[str, Any] = {}
    if args.transactions is not None:
        overrides["transactions"] = args.transactions
    if args.seed is not None:
        overrides["seed"] = args.seed
    if overrides:
        spec = FixtureSpec(**{**asdict(spec), **overrides})
    output = args.output or OUTPUT_DIR_DEFAULT / args.preset / "transactions.parquet"
    manifest = generate(spec, output)
    manifest_path = output.with_name("manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
