"""Leakage and correctness tests for the Iteration 2 feature families.

Synthetic data only. Every family is checked for:

- future invisibility: rewriting/adding/removing rows after a cutoff leaves
  features of rows at or before the cutoff unchanged;
- same-minute invisibility: adding, removing, or rewriting same-minute peers
  leaves a row's features unchanged;
- target/split rejection: the builder fails closed if labels are visible;
- past sensitivity: perturbing strictly earlier rows does change features
  (so the invariance tests are not passing vacuously);
- a brute-force Python reference for representative columns, including the
  interval-sweep windowed distinct counts and 3-cycle closures.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta

import duckdb
import pytest

from ml.feature_families import (
    ACCOUNTS_VIEW,
    FAMILIES,
    build_families,
    feature_columns,
)

ALL = list(FAMILIES)
START = datetime(2022, 9, 1, 0, 0)
BANKS = ("010", "20", "0300")
ACCOUNTS = [(bank, f"A{i}") for i, bank in enumerate(BANKS * 3)]  # 9 accounts
CURRENCIES = ("US Dollar", "Euro")
FORMATS = ("ACH", "Cheque", "Wire")

Row = dict[str, object]


def make_rows(seed: int = 7, n: int = 260) -> list[Row]:
    """Random transfers over ~9 days with many same-minute collisions."""
    rng = random.Random(seed)
    minutes = sorted(rng.sample(range(9 * 24 * 60), 90))
    rows: list[Row] = []
    for index in range(n):
        sender = rng.choice(ACCOUNTS)
        receiver = sender if rng.random() < 0.05 else rng.choice(ACCOUNTS)
        cur_paid = rng.choice(CURRENCIES)
        cur_received = cur_paid if rng.random() < 0.9 else rng.choice(CURRENCIES)
        amount = rng.choice([100.0, 1000.0, 2500.0, rng.uniform(1, 5000)])
        rows.append(
            {
                "transaction_id": f"tx-{index:05d}",
                "timestamp": START + timedelta(minutes=rng.choice(minutes)),
                "sender": sender,
                "receiver": receiver,
                "amount_paid": round(amount, 2),
                "payment_currency": cur_paid,
                "amount_received": round(amount * (1.0 if cur_paid == cur_received else 0.9), 2),
                "receiving_currency": cur_received,
                "payment_format": rng.choice(FORMATS),
            }
        )
    return rows


def load(con: duckdb.DuckDBPyConnection, rows: list[Row], extra: dict[str, str] | None = None):
    """Materialize rows as the target-free ``tx`` table plus a registry view."""
    con.execute(
        "CREATE OR REPLACE TABLE tx (transaction_id VARCHAR, timestamp TIMESTAMP, "
        "sender_bank VARCHAR, sender_account VARCHAR, receiver_bank VARCHAR, "
        "receiver_account VARCHAR, sender_account_key VARCHAR, receiver_account_key VARCHAR, "
        "amount_paid DOUBLE, payment_currency VARCHAR, amount_received DOUBLE, "
        "receiving_currency VARCHAR, payment_format VARCHAR"
        + "".join(f", {name} {sql_type}" for name, sql_type in (extra or {}).items())
        + ")"
    )
    values = []
    for row in rows:
        (sb, sa), (rb, ra) = row["sender"], row["receiver"]
        record = [
            row["transaction_id"],
            row["timestamp"],
            sb,
            sa,
            rb,
            ra,
            f"{sb}::{sa}",
            f"{rb}::{ra}",
            row["amount_paid"],
            row["payment_currency"],
            row["amount_received"],
            row["receiving_currency"],
            row["payment_format"],
        ]
        record += [0 for _ in (extra or {})]
        values.append(record)
    placeholders = ", ".join("?" for _ in values[0])
    con.executemany(f"INSERT INTO tx VALUES ({placeholders})", values)
    registry = [
        (bank.lstrip("0"), account, f"E{i // 2}", "Corporation" if i % 3 else "Individual")
        for i, (bank, account) in enumerate(ACCOUNTS)
    ]
    con.execute(
        f"CREATE OR REPLACE TABLE {ACCOUNTS_VIEW} "
        "(bank_id VARCHAR, account_number VARCHAR, entity_id VARCHAR, entity_type VARCHAR)"
    )
    con.executemany(f"INSERT INTO {ACCOUNTS_VIEW} VALUES (?, ?, ?, ?)", registry)


def compute(rows: list[Row], families: list[str] | None = None) -> dict[str, tuple]:
    families = families or ALL
    con = duckdb.connect()
    try:
        load(con, rows)
        table = build_families(con, "tx", families)
        columns = feature_columns(families)
        fetched = con.execute(
            f"SELECT transaction_id, {', '.join(columns)} FROM {table}"
        ).fetchall()
    finally:
        con.close()
    assert len(fetched) == len(rows)
    return {record[0]: tuple(record[1:]) for record in fetched}


def same(a: tuple, b: tuple) -> bool:
    for x, y in zip(a, b, strict=True):
        if isinstance(x, float) and isinstance(y, float):
            if math.isnan(x) and math.isnan(y):
                continue
            if not math.isclose(x, y, rel_tol=1e-9, abs_tol=1e-9):
                return False
        elif x != y:
            return False
    return True


@pytest.fixture(scope="module")
def rows() -> list[Row]:
    return make_rows()


@pytest.fixture(scope="module")
def baseline(rows: list[Row]) -> dict[str, tuple]:
    return compute(rows)


@pytest.mark.parametrize("family", ALL)
def test_future_rows_invisible(rows: list[Row], family: str) -> None:
    """Rewriting, dropping, and adding rows after a cutoff changes nothing before it."""
    base = compute(rows, [family])
    cutoff = sorted(row["timestamp"] for row in rows)[len(rows) // 2]
    rng = random.Random(3)
    perturbed: list[Row] = []
    for row in rows:
        if row["timestamp"] <= cutoff:
            perturbed.append(row)
        elif rng.random() < 0.3:
            continue  # drop a future row
        else:
            changed = dict(row)
            changed["receiver"] = rng.choice(ACCOUNTS)
            changed["amount_paid"] = float(row["amount_paid"]) * 7 + 1
            changed["amount_received"] = float(row["amount_received"]) * 7 + 1
            perturbed.append(changed)
    for index in range(40):  # brand-new future rows touching every account
        perturbed.append(
            {
                **rows[index],
                "transaction_id": f"fut-{index:03d}",
                "timestamp": cutoff + timedelta(minutes=1 + index),
                "sender": ACCOUNTS[index % len(ACCOUNTS)],
                "receiver": ACCOUNTS[(index * 5 + 1) % len(ACCOUNTS)],
            }
        )
    after = compute(perturbed, [family])
    checked = 0
    for row in rows:
        if row["timestamp"] <= cutoff:
            assert same(base[row["transaction_id"]], after[row["transaction_id"]]), (
                family,
                row["transaction_id"],
            )
            checked += 1
    assert checked > 50


def test_same_minute_rows_invisible(rows: list[Row], baseline: dict[str, tuple]) -> None:
    """Adding/removing/rewriting same-minute peers leaves a row unchanged.

    All families are built together, so every feature column is covered;
    the failure message names the minute and the differing columns.
    """
    base = baseline
    columns = feature_columns(ALL)
    by_minute: dict[datetime, list[Row]] = {}
    for row in rows:
        by_minute.setdefault(row["timestamp"], []).append(row)
    busy = [group for group in by_minute.values() if len(group) >= 3]
    assert busy, "fixture must contain same-minute groups"
    for group in busy[:4]:
        keep = group[0]
        minute = keep["timestamp"]
        peers = {row["transaction_id"] for row in group[1:]}
        # Variant 1: remove all peers in the minute.
        removed = [row for row in rows if row["transaction_id"] not in peers]
        # Variant 2: rewrite peers and add new peers sharing keep's accounts.
        rewritten = []
        for row in rows:
            if row["transaction_id"] in peers:
                rewritten.append({**row, "receiver": keep["sender"], "amount_paid": 9.0})
            else:
                rewritten.append(row)
        rewritten += [
            {
                **keep,
                "transaction_id": f"peer-{n}",
                "sender": sender,
                "receiver": receiver,
                "amount_paid": 1234.0,
            }
            for n, (sender, receiver) in enumerate(
                [
                    (keep["sender"], keep["receiver"]),
                    (keep["receiver"], keep["sender"]),
                    (keep["receiver"], ACCOUNTS[0]),
                    (ACCOUNTS[0], keep["sender"]),
                ]
            )
        ]
        for variant in (removed, rewritten):
            after = compute(variant)
            before_row, after_row = base[keep["transaction_id"]], after[keep["transaction_id"]]
            differing = [
                name
                for name, x, y in zip(columns, before_row, after_row, strict=True)
                if not same((x,), (y,))
            ]
            assert not differing, (minute, differing)


@pytest.mark.parametrize("forbidden", ["target_is_laundering", "split", "is_laundering"])
def test_target_or_split_visible_fails_closed(rows: list[Row], forbidden: str) -> None:
    sql_type = "VARCHAR" if forbidden == "split" else "INTEGER"
    con = duckdb.connect()
    try:
        load(con, rows[:20], extra={forbidden: sql_type})
        with pytest.raises(ValueError, match="forbidden columns"):
            build_families(con, "tx", ALL)
    finally:
        con.close()


def test_sub_minute_timestamps_rejected(rows: list[Row]) -> None:
    shifted = [dict(row) for row in rows[:20]]
    shifted[3]["timestamp"] = shifted[3]["timestamp"] + timedelta(seconds=30)
    con = duckdb.connect()
    try:
        load(con, shifted)
        with pytest.raises(ValueError, match="minute-aligned"):
            build_families(con, "tx", ["velocity"])
    finally:
        con.close()


def test_target_never_in_family_columns() -> None:
    for spec in FAMILIES.values():
        assert "target_is_laundering" not in spec.columns
        assert "split" not in spec.columns


@pytest.mark.parametrize("family", ["velocity", "amount", "counterparty", "cycle", "base"])
def test_past_rows_change_features(rows: list[Row], family: str) -> None:
    """Sanity: history-based families do react to strictly earlier rows."""
    base = compute(rows, [family])
    first_minute = min(row["timestamp"] for row in rows)
    extra = [
        {
            **rows[i],
            "transaction_id": f"past-{i}",
            "timestamp": first_minute - timedelta(minutes=5 + i),
            "sender": ACCOUNTS[(i * 2) % len(ACCOUNTS)],
            "receiver": ACCOUNTS[(i * 3 + 1) % len(ACCOUNTS)],
        }
        for i in range(30)
    ]
    after = compute(rows + extra, [family])
    differs = sum(not same(base[k], after[k]) for k in base)
    assert differs > 0


# ---------------------------------------------------------------------------
# Currency semantics of log_received_over_paid
# ---------------------------------------------------------------------------


def txlocal_row(
    tx_id: str,
    *,
    paid: float | None,
    received: float | None,
    payment_currency: str,
    receiving_currency: str,
) -> Row:
    return {
        "transaction_id": tx_id,
        "timestamp": START,
        "sender": ACCOUNTS[0],
        "receiver": ACCOUNTS[1],
        "amount_paid": paid,
        "payment_currency": payment_currency,
        "amount_received": received,
        "receiving_currency": receiving_currency,
        "payment_format": "ACH",
    }


def txlocal_value(row: Row, column: str) -> object:
    index = feature_columns(["txlocal"]).index(column)
    return compute([row], ["txlocal"])[row["transaction_id"]][index]


def test_log_received_over_paid_same_currency() -> None:
    """Equal currencies and positive amounts give the declared log ratio."""
    row = txlocal_row(
        "same",
        paid=100.0,
        received=250.0,
        payment_currency="US Dollar",
        receiving_currency="US Dollar",
    )
    expected = math.log1p(250.0) - math.log1p(100.0)
    assert txlocal_value(row, "log_received_over_paid") == pytest.approx(expected)
    assert txlocal_value(row, "currency_mismatch") == 0


def test_log_received_over_paid_currency_mismatch_is_null() -> None:
    """Mismatched currencies never compare raw amounts; the indicator covers it."""
    row = txlocal_row(
        "mismatch",
        paid=100.0,
        received=250.0,
        payment_currency="US Dollar",
        receiving_currency="Euro",
    )
    assert txlocal_value(row, "log_received_over_paid") is None
    assert txlocal_value(row, "currency_mismatch") == 1


def test_log_received_over_paid_zero_paid_is_null() -> None:
    """A zero paid amount is not a valid ratio denominator."""
    row = txlocal_row(
        "zero",
        paid=0.0,
        received=250.0,
        payment_currency="US Dollar",
        receiving_currency="US Dollar",
    )
    assert txlocal_value(row, "log_received_over_paid") is None


def test_log_received_over_paid_missing_amount_is_null() -> None:
    """Missing paid or received amounts leave the ratio undefined."""
    missing_paid = txlocal_row(
        "no_paid",
        paid=None,
        received=250.0,
        payment_currency="US Dollar",
        receiving_currency="US Dollar",
    )
    missing_received = txlocal_row(
        "no_received",
        paid=100.0,
        received=None,
        payment_currency="US Dollar",
        receiving_currency="US Dollar",
    )
    table = compute([missing_paid, missing_received], ["txlocal"])
    index = feature_columns(["txlocal"]).index("log_received_over_paid")
    assert table["no_paid"][index] is None
    assert table["no_received"][index] is None


# ---------------------------------------------------------------------------
# Brute-force reference
# ---------------------------------------------------------------------------


def key(account: tuple[str, str]) -> str:
    return f"{account[0]}::{account[1]}"


def reference(rows: list[Row], row: Row) -> dict[str, float | int | None]:
    t = row["timestamp"]
    s, r = key(row["sender"]), key(row["receiver"])
    past = [p for p in rows if p["timestamp"] < t]

    def in_window(p: Row, minutes: int) -> bool:
        return t - timedelta(minutes=minutes) <= p["timestamp"] < t

    day, week = 24 * 60, 7 * 24 * 60
    s_out_24h = sum(1 for p in past if key(p["sender"]) == s and in_window(p, day))
    r_in_1h = sum(1 for p in past if key(p["receiver"]) == r and in_window(p, 60))
    s_distinct_out_24h = len(
        {key(p["receiver"]) for p in past if key(p["sender"]) == s and in_window(p, day)}
    )
    r_distinct_in_7d = len(
        {key(p["sender"]) for p in past if key(p["receiver"]) == r and in_window(p, week)}
    )
    s_distinct_in_all = len({key(p["sender"]) for p in past if key(p["receiver"]) == s})
    pair_prior = sum(1 for p in past if key(p["sender"]) == s and key(p["receiver"]) == r)
    reverse_prior = sum(1 for p in past if key(p["sender"]) == r and key(p["receiver"]) == s)
    edges = {(key(p["sender"]), key(p["receiver"])) for p in past}
    others = {key(a) for a in ACCOUNTS} - {s, r}
    cycle3 = sum(1 for x in others if (r, x) in edges and (x, s) in edges)
    s_cur_in_24h = sum(
        float(p["amount_received"])
        for p in past
        if key(p["receiver"]) == s
        and p["receiving_currency"] == row["payment_currency"]
        and in_window(p, day)
    )
    s_last_in = max((p["timestamp"] for p in past if key(p["receiver"]) == s), default=None)
    return {
        "s_out_n_24h": s_out_24h,
        "r_in_n_1h": r_in_1h,
        "s_distinct_out_24h": s_distinct_out_24h,
        "r_distinct_in_7d": r_distinct_in_7d,
        "s_distinct_in_all": s_distinct_in_all,
        "pair_prior_n": pair_prior,
        "reverse_prior_n": reverse_prior,
        "cycle3_closures": cycle3,
        "s_cur_in_log_sum_24h": math.log1p(s_cur_in_24h),
        "s_mins_since_last_in": (
            None if s_last_in is None else (t - s_last_in).total_seconds() / 60.0
        ),
    }


def test_matches_brute_force_reference(rows: list[Row], baseline: dict[str, tuple]) -> None:
    columns = feature_columns(ALL)
    rng = random.Random(11)
    sample = rng.sample(rows, 80)
    for row in sample:
        expected = reference(rows, row)
        actual = dict(zip(columns, baseline[row["transaction_id"]], strict=True))
        for name, value in expected.items():
            got = actual[name]
            if value is None:
                assert got is None, (name, row["transaction_id"])
            else:
                assert got is not None and math.isclose(
                    float(got), float(value), rel_tol=1e-9, abs_tol=1e-9
                ), (name, row["transaction_id"], got, value)
