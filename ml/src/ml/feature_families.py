"""Iteration 2 point-in-time feature families for HI-Small v8.

Every family is a pure DuckDB SQL builder over a target-free transaction view
(``tx_view``) with the columns in ``TX_INPUT_COLUMNS``. Builders never read
target, split, or pattern metadata: :func:`require_target_free` rejects any
input view exposing a forbidden column before a single statement runs.

Point-in-time rule (identical for every family): features for a transaction
at minute ``t`` use only rows whose timestamp is strictly earlier than ``t``.
Rows that share the minute ``t`` (including the scored row's same-minute
peers) are simultaneous and invisible to each other. Transaction-local
attributes of the scored row itself (its own amount, currencies, format,
banks, hour) are allowed. Timestamps must be minute-aligned; the builder
asserts this so a finer-resolution source cannot silently weaken the rule.

Windows are half-open ``[t - w, t)``. Per-account aggregates are formed on
(account, minute) groups first, so a ``ROWS ... 1 PRECEDING`` frame excludes
the whole current minute, and ``RANGE ... INTERVAL 1 MINUTE PRECEDING``
frames end strictly before ``t``. Amount sums are always keyed by a single
currency; nothing sums or compares amounts across currencies.

Families and the state each would need at online serving time:

- ``txlocal``: stateless (scored row only).
- ``velocity``: per-account minute-bucket counts over the last 7 days plus
  first/last-seen timestamps.
- ``amount``: per-(account, currency) running count/sum/sum-of-squares of
  log amounts plus 24h/7d amount sums.
- ``counterparty``: per-ordered-pair count and last timestamp, per-account
  distinct counterparty sets (all-time and 24h/7d expiring sets).
- ``cycle``: per-account in-neighbour sets with first-seen times plus an
  ordered-pair existence index (3-cycle closure lookups).
- ``kyc``: static account registry lookup (entity type, accounts per entity).

``log_received_over_paid`` is defined only when the paid and received amounts
share one currency and both are strictly positive; it is ``null``/NaN
otherwise, so raw amounts from different currencies are never compared. The
``currency_mismatch`` indicator records the mismatched-currency case as a
separate feature instead. No family sums or compares amounts across
currencies.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from duckdb import DuckDBPyConnection

TX_INPUT_COLUMNS: tuple[str, ...] = (
    "transaction_id",
    "timestamp",
    "sender_bank",
    "sender_account",
    "receiver_bank",
    "receiver_account",
    "sender_account_key",
    "receiver_account_key",
    "amount_paid",
    "payment_currency",
    "amount_received",
    "receiving_currency",
    "payment_format",
)

#: Columns that must never be visible to a feature builder.
FORBIDDEN_INPUT_COLUMNS: frozenset[str] = frozenset(
    {"target_is_laundering", "is_laundering", "split", "label", "typology", "pattern"}
)

VELOCITY_WINDOWS: tuple[tuple[str, str], ...] = (
    ("1h", "1 HOUR"),
    ("6h", "6 HOURS"),
    ("24h", "24 HOURS"),
    ("7d", "7 DAYS"),
)
AMOUNT_WINDOWS: tuple[tuple[str, str], ...] = (("24h", "24 HOURS"), ("7d", "7 DAYS"))
DISTINCT_WINDOWS: tuple[tuple[str, int], ...] = (("24h", 24 * 60), ("7d", 7 * 24 * 60))


@dataclass(frozen=True)
class FamilySpec:
    """One feature family: output columns, categoricals, and its SQL builder."""

    name: str
    columns: tuple[str, ...]
    categorical: tuple[str, ...]
    serving_state: str
    builder: Callable[[DuckDBPyConnection, str, str], None]
    needs_accounts: bool = False
    notes: list[str] = field(default_factory=list)


def require_target_free(con: DuckDBPyConnection, tx_view: str) -> None:
    """Fail closed if the input view exposes labels/split or misses inputs."""
    columns = {row[0] for row in con.execute(f"DESCRIBE SELECT * FROM {tx_view}").fetchall()}
    leaked = sorted(columns & FORBIDDEN_INPUT_COLUMNS)
    if leaked:
        raise ValueError(f"forbidden columns visible to feature builder: {leaked}")
    missing = [name for name in TX_INPUT_COLUMNS if name not in columns]
    if missing:
        raise ValueError(f"feature input view missing columns: {missing}")
    misaligned = con.execute(
        f"SELECT COUNT(*) FROM {tx_view} "
        "WHERE timestamp <> date_trunc('minute', timestamp)"
    ).fetchone()[0]
    if misaligned:
        raise ValueError(f"{misaligned} timestamps are not minute-aligned")


def _minutes_between(later: str, earlier: str) -> str:
    return f"(epoch({later}) - epoch({earlier})) / 60.0"


# ---------------------------------------------------------------------------
# base (Iteration 1 feature set, rebuilt here so one table holds everything)
# ---------------------------------------------------------------------------

BASE_COLUMNS: tuple[str, ...] = (
    "log_amount_paid",
    "log_amount_received",
    "payment_currency",
    "receiving_currency",
    "payment_format",
    "sender_prior_out",
    "sender_prior_in",
    "receiver_prior_out",
    "receiver_prior_in",
)


def _account_minute_events(con: DuckDBPyConnection, tx_view: str) -> None:
    con.execute(
        "CREATE OR REPLACE TEMP TABLE ff_acct_ev AS "
        "SELECT account_key, ts, SUM(out_n)::BIGINT AS out_n, SUM(in_n)::BIGINT AS in_n FROM ("
        f"SELECT sender_account_key AS account_key, timestamp AS ts, 1 AS out_n, 0 AS in_n "
        f"FROM {tx_view} UNION ALL "
        f"SELECT receiver_account_key, timestamp, 0, 1 FROM {tx_view}"
        ") GROUP BY account_key, ts"
    )


def build_base(con: DuckDBPyConnection, tx_view: str, out: str) -> None:
    """Iteration 1 features: log amounts, categories, all-time prior counts."""
    _account_minute_events(con, tx_view)
    con.execute(
        "CREATE OR REPLACE TEMP TABLE ff_acct_prior AS "
        "SELECT account_key, ts, "
        "COALESCE(SUM(out_n) OVER w, 0)::BIGINT AS prior_out, "
        "COALESCE(SUM(in_n) OVER w, 0)::BIGINT AS prior_in "
        "FROM ff_acct_ev WINDOW w AS (PARTITION BY account_key ORDER BY ts "
        "ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)"
    )
    con.execute(
        f"CREATE OR REPLACE TEMP TABLE {out} AS SELECT t.transaction_id, "
        "LN(1 + t.amount_paid) AS log_amount_paid, "
        "LN(1 + t.amount_received) AS log_amount_received, "
        "t.payment_currency, t.receiving_currency, t.payment_format, "
        "sp.prior_out AS sender_prior_out, sp.prior_in AS sender_prior_in, "
        "rp.prior_out AS receiver_prior_out, rp.prior_in AS receiver_prior_in "
        f"FROM {tx_view} t "
        "JOIN ff_acct_prior sp ON sp.account_key = t.sender_account_key AND sp.ts = t.timestamp "
        "JOIN ff_acct_prior rp ON rp.account_key = t.receiver_account_key "
        "AND rp.ts = t.timestamp"
    )


# ---------------------------------------------------------------------------
# txlocal
# ---------------------------------------------------------------------------

TXLOCAL_COLUMNS: tuple[str, ...] = (
    "currency_mismatch",
    "cross_bank",
    "self_transfer",
    "amount_paid_round_100",
    "amount_paid_round_1000",
    "amount_paid_has_cents",
    "log_received_over_paid",
    "hour_of_day",
)


def build_txlocal(con: DuckDBPyConnection, tx_view: str, out: str) -> None:
    """Stateless attributes of the scored row only."""
    con.execute(
        f"CREATE OR REPLACE TEMP TABLE {out} AS SELECT transaction_id, "
        "(payment_currency <> receiving_currency)::INTEGER AS currency_mismatch, "
        "(sender_bank <> receiver_bank)::INTEGER AS cross_bank, "
        "(sender_account_key = receiver_account_key)::INTEGER AS self_transfer, "
        "(amount_paid >= 100 AND amount_paid = 100 * ROUND(amount_paid / 100))::INTEGER "
        "AS amount_paid_round_100, "
        "(amount_paid >= 1000 AND amount_paid = 1000 * ROUND(amount_paid / 1000))::INTEGER "
        "AS amount_paid_round_1000, "
        "(ROUND(amount_paid * 100) % 100 <> 0)::INTEGER AS amount_paid_has_cents, "
        "CASE WHEN payment_currency = receiving_currency "
        "AND amount_paid IS NOT NULL AND amount_received IS NOT NULL "
        "AND amount_paid > 0 AND amount_received > 0 "
        "THEN LN(1 + amount_received) - LN(1 + amount_paid) END "
        "AS log_received_over_paid, "
        "hour(timestamp)::INTEGER AS hour_of_day "
        f"FROM {tx_view}"
    )


# ---------------------------------------------------------------------------
# velocity
# ---------------------------------------------------------------------------


def _velocity_columns() -> tuple[str, ...]:
    columns: list[str] = []
    for side in ("s", "r"):
        for direction in ("out", "in"):
            for label, _ in VELOCITY_WINDOWS:
                columns.append(f"{side}_{direction}_n_{label}")
    columns += [
        "s_mins_since_last_out",
        "s_mins_since_last_in",
        "r_mins_since_last_out",
        "r_mins_since_last_in",
        "s_age_mins",
        "r_age_mins",
    ]
    return tuple(columns)


VELOCITY_COLUMNS = _velocity_columns()


def build_velocity(con: DuckDBPyConnection, tx_view: str, out: str) -> None:
    """Multi-window past-only counts and recency per endpoint account."""
    _account_minute_events(con, tx_view)
    window_selects = []
    window_defs = []
    for label, interval in VELOCITY_WINDOWS:
        window_selects.append(f"COALESCE(SUM(out_n) OVER w{label}, 0) AS out_n_{label}")
        window_selects.append(f"COALESCE(SUM(in_n) OVER w{label}, 0) AS in_n_{label}")
        window_defs.append(
            f"w{label} AS (PARTITION BY account_key ORDER BY ts RANGE BETWEEN "
            f"INTERVAL {interval} PRECEDING AND INTERVAL 1 MINUTE PRECEDING)"
        )
    con.execute(
        "CREATE OR REPLACE TEMP TABLE ff_acct_vel AS SELECT account_key, ts, "
        + ", ".join(window_selects)
        + ", MAX(CASE WHEN out_n > 0 THEN ts END) OVER wall AS last_out_ts, "
        "MAX(CASE WHEN in_n > 0 THEN ts END) OVER wall AS last_in_ts, "
        "MIN(ts) OVER wall AS first_ts "
        "FROM ff_acct_ev WINDOW "
        + ", ".join(window_defs)
        + ", wall AS (PARTITION BY account_key ORDER BY ts "
        "ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)"
    )
    selects = []
    for side, alias in (("s", "sv"), ("r", "rv")):
        for direction in ("out", "in"):
            for label, _ in VELOCITY_WINDOWS:
                selects.append(f"{alias}.{direction}_n_{label} AS {side}_{direction}_n_{label}")
    selects += [
        f"{_minutes_between('t.timestamp', 'sv.last_out_ts')} AS s_mins_since_last_out",
        f"{_minutes_between('t.timestamp', 'sv.last_in_ts')} AS s_mins_since_last_in",
        f"{_minutes_between('t.timestamp', 'rv.last_out_ts')} AS r_mins_since_last_out",
        f"{_minutes_between('t.timestamp', 'rv.last_in_ts')} AS r_mins_since_last_in",
        f"{_minutes_between('t.timestamp', 'sv.first_ts')} AS s_age_mins",
        f"{_minutes_between('t.timestamp', 'rv.first_ts')} AS r_age_mins",
    ]
    con.execute(
        f"CREATE OR REPLACE TEMP TABLE {out} AS SELECT t.transaction_id, "
        + ", ".join(selects)
        + f" FROM {tx_view} t "
        "JOIN ff_acct_vel sv ON sv.account_key = t.sender_account_key AND sv.ts = t.timestamp "
        "JOIN ff_acct_vel rv ON rv.account_key = t.receiver_account_key AND rv.ts = t.timestamp"
    )


# ---------------------------------------------------------------------------
# amount (per account and single currency)
# ---------------------------------------------------------------------------

AMOUNT_COLUMNS: tuple[str, ...] = (
    "s_cur_out_n_all",
    "s_amt_z",
    "s_cur_out_log_sum_24h",
    "s_cur_out_log_sum_7d",
    "s_cur_in_log_sum_24h",
    "s_cur_in_log_sum_7d",
    "s_passthrough_ratio_24h",
    "s_passthrough_ratio_7d",
    "r_cur_in_n_all",
    "r_amt_z",
    "r_cur_in_log_sum_24h",
    "r_cur_in_log_sum_7d",
    "r_cur_out_log_sum_24h",
    "r_cur_out_log_sum_7d",
)


def build_amount(con: DuckDBPyConnection, tx_view: str, out: str) -> None:
    """Amount history per (account, currency): z-scores, sums, pass-through.

    The sender is compared with its own prior outgoing amounts in the
    payment currency; the receiver with its own prior incoming amounts in the
    receiving currency. Pass-through ratios divide the outgoing amount by the
    sender's prior inflow in the same currency. No cross-currency arithmetic.
    """
    con.execute(
        "CREATE OR REPLACE TEMP TABLE ff_cur_ev AS "
        "SELECT account_key, cur, ts, SUM(out_n) AS out_n, SUM(out_amt) AS out_amt, "
        "SUM(out_log) AS out_log, SUM(out_log2) AS out_log2, SUM(in_n) AS in_n, "
        "SUM(in_amt) AS in_amt, SUM(in_log) AS in_log, SUM(in_log2) AS in_log2 FROM ("
        "SELECT sender_account_key AS account_key, payment_currency AS cur, timestamp AS ts, "
        "1 AS out_n, amount_paid AS out_amt, LN(1 + amount_paid) AS out_log, "
        "LN(1 + amount_paid) ^ 2 AS out_log2, 0 AS in_n, 0.0 AS in_amt, 0.0 AS in_log, "
        f"0.0 AS in_log2 FROM {tx_view} UNION ALL "
        "SELECT receiver_account_key, receiving_currency, timestamp, 0, 0.0, 0.0, 0.0, 1, "
        "amount_received, LN(1 + amount_received), LN(1 + amount_received) ^ 2 "
        f"FROM {tx_view}"
        ") GROUP BY account_key, cur, ts"
    )
    window_selects = []
    window_defs = []
    for label, interval in AMOUNT_WINDOWS:
        window_selects.append(f"COALESCE(SUM(out_amt) OVER w{label}, 0) AS out_sum_{label}")
        window_selects.append(f"COALESCE(SUM(in_amt) OVER w{label}, 0) AS in_sum_{label}")
        window_defs.append(
            f"w{label} AS (PARTITION BY account_key, cur ORDER BY ts RANGE BETWEEN "
            f"INTERVAL {interval} PRECEDING AND INTERVAL 1 MINUTE PRECEDING)"
        )
    cumulative = []
    for direction in ("out", "in"):
        for stat in ("n", "log", "log2"):
            cumulative.append(
                f"COALESCE(SUM({direction}_{stat}) OVER wall, 0) AS {direction}_{stat}_all"
            )
    con.execute(
        "CREATE OR REPLACE TEMP TABLE ff_cur_hist AS SELECT account_key, cur, ts, "
        + ", ".join(window_selects + cumulative)
        + " FROM ff_cur_ev WINDOW "
        + ", ".join(window_defs)
        + ", wall AS (PARTITION BY account_key, cur ORDER BY ts "
        "ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)"
    )

    def zscore(alias: str, direction: str, value: str) -> str:
        n = f"{alias}.{direction}_n_all"
        mean = f"({alias}.{direction}_log_all / {n})"
        var = f"GREATEST({alias}.{direction}_log2_all / {n} - {mean} ^ 2, 0)"
        return (
            f"CASE WHEN {n} >= 2 THEN ({value} - {mean}) / SQRT({var} + 0.01) END"
        )

    con.execute(
        f"CREATE OR REPLACE TEMP TABLE {out} AS SELECT t.transaction_id, "
        "sh.out_n_all AS s_cur_out_n_all, "
        f"{zscore('sh', 'out', 'LN(1 + t.amount_paid)')} AS s_amt_z, "
        "LN(1 + sh.out_sum_24h) AS s_cur_out_log_sum_24h, "
        "LN(1 + sh.out_sum_7d) AS s_cur_out_log_sum_7d, "
        "LN(1 + sh.in_sum_24h) AS s_cur_in_log_sum_24h, "
        "LN(1 + sh.in_sum_7d) AS s_cur_in_log_sum_7d, "
        "CASE WHEN sh.in_sum_24h > 0 THEN t.amount_paid / sh.in_sum_24h END "
        "AS s_passthrough_ratio_24h, "
        "CASE WHEN sh.in_sum_7d > 0 THEN t.amount_paid / sh.in_sum_7d END "
        "AS s_passthrough_ratio_7d, "
        "rh.in_n_all AS r_cur_in_n_all, "
        f"{zscore('rh', 'in', 'LN(1 + t.amount_received)')} AS r_amt_z, "
        "LN(1 + rh.in_sum_24h) AS r_cur_in_log_sum_24h, "
        "LN(1 + rh.in_sum_7d) AS r_cur_in_log_sum_7d, "
        "LN(1 + rh.out_sum_24h) AS r_cur_out_log_sum_24h, "
        "LN(1 + rh.out_sum_7d) AS r_cur_out_log_sum_7d "
        f"FROM {tx_view} t "
        "JOIN ff_cur_hist sh ON sh.account_key = t.sender_account_key "
        "AND sh.cur = t.payment_currency AND sh.ts = t.timestamp "
        "JOIN ff_cur_hist rh ON rh.account_key = t.receiver_account_key "
        "AND rh.cur = t.receiving_currency AND rh.ts = t.timestamp"
    )


# ---------------------------------------------------------------------------
# counterparty (pairs, reverse flow, distinct counterparties)
# ---------------------------------------------------------------------------


def _counterparty_columns() -> tuple[str, ...]:
    columns = [
        "pair_prior_n",
        "pair_mins_since_last",
        "reverse_prior_n",
        "reverse_mins_since_last",
        "s_distinct_out_all",
        "s_distinct_in_all",
        "r_distinct_out_all",
        "r_distinct_in_all",
    ]
    for label, _ in DISTINCT_WINDOWS:
        columns += [
            f"s_distinct_out_{label}",
            f"s_distinct_in_{label}",
            f"r_distinct_out_{label}",
            f"r_distinct_in_{label}",
        ]
    return tuple(columns)


COUNTERPARTY_COLUMNS = _counterparty_columns()


def _pair_minute_table(con: DuckDBPyConnection, tx_view: str) -> None:
    con.execute(
        "CREATE OR REPLACE TEMP TABLE ff_pair_ev AS "
        "SELECT s, r, ts, n, SUM(n) OVER (PARTITION BY s, r ORDER BY ts "
        "ROWS UNBOUNDED PRECEDING)::BIGINT AS cum_n, "
        "LAG(ts) OVER (PARTITION BY s, r ORDER BY ts) AS prev_ts, "
        "LEAD(ts) OVER (PARTITION BY s, r ORDER BY ts) AS next_ts FROM ("
        "SELECT sender_account_key AS s, receiver_account_key AS r, timestamp AS ts, "
        f"COUNT(*)::BIGINT AS n FROM {tx_view} GROUP BY ALL)"
    )
    con.execute(
        "CREATE OR REPLACE TEMP TABLE ff_pair_first AS "
        "SELECT s, r, MIN(ts) AS first_ts FROM ff_pair_ev GROUP BY s, r"
    )


def build_counterparty(con: DuckDBPyConnection, tx_view: str, out: str) -> None:
    """Pair repetition, reverse flow, and distinct-counterparty fan counts.

    All-time distinct counts accumulate each ordered pair's first-seen
    minute and are read with a strict as-of join (``ts < t``). Windowed
    distinct counts use an exact interval sweep: a pair event at minute
    ``e`` keeps the pair active for query minutes ``[e + 1, e + w]``
    (window ``[t - w, t)``); overlapping intervals of one pair are merged
    into +1/-1 deltas, so a cumulative delta sum at ``t`` equals the number
    of distinct counterparties seen in ``[t - w, t)``.
    """
    _pair_minute_table(con, tx_view)
    # Cumulative distinct counts per account and direction.
    con.execute(
        "CREATE OR REPLACE TEMP TABLE ff_distinct_all AS "
        "SELECT account_key, direction, ts, SUM(n) OVER (PARTITION BY account_key, direction "
        "ORDER BY ts ROWS UNBOUNDED PRECEDING)::BIGINT AS cum_distinct FROM ("
        "SELECT s AS account_key, 'out' AS direction, first_ts AS ts, COUNT(*) AS n "
        "FROM ff_pair_first GROUP BY ALL UNION ALL "
        "SELECT r, 'in', first_ts, COUNT(*) FROM ff_pair_first GROUP BY ALL)"
    )
    delta_parts = []
    for label, minutes in DISTINCT_WINDOWS:
        for direction, key in (("out", "s"), ("in", "r")):
            delta_parts.append(
                f"SELECT {key} AS account_key, '{direction}_{label}' AS kind, "
                "ts + INTERVAL 1 MINUTE AS at_ts, 1 AS d FROM ff_pair_ev "
                f"WHERE prev_ts IS NULL OR ts - prev_ts > INTERVAL {minutes} MINUTE"
            )
            delta_parts.append(
                f"SELECT {key}, '{direction}_{label}', "
                f"ts + INTERVAL {minutes + 1} MINUTE, -1 FROM ff_pair_ev "
                f"WHERE next_ts IS NULL OR next_ts - ts > INTERVAL {minutes} MINUTE"
            )
    con.execute(
        "CREATE OR REPLACE TEMP TABLE ff_distinct_win AS "
        "SELECT account_key, kind, at_ts, SUM(d) OVER (PARTITION BY account_key, kind "
        "ORDER BY at_ts ROWS UNBOUNDED PRECEDING)::BIGINT AS active FROM ("
        "SELECT account_key, kind, at_ts, SUM(d) AS d FROM ("
        + " UNION ALL ".join(delta_parts)
        + ") GROUP BY ALL)"
    )
    # Distinct (account, minute) query points keep the as-of joins small.
    con.execute(
        "CREATE OR REPLACE TEMP TABLE ff_query_pts AS "
        f"SELECT DISTINCT sender_account_key AS account_key, timestamp AS ts FROM {tx_view} "
        f"UNION SELECT DISTINCT receiver_account_key, timestamp FROM {tx_view}"
    )
    kinds = [("out", "all"), ("in", "all")] + [
        (direction, label) for label, _ in DISTINCT_WINDOWS for direction in ("out", "in")
    ]
    point_selects = []
    point_joins = []
    for index, (direction, label) in enumerate(kinds):
        alias = f"k{index}"
        column = f"{direction}_{label}"
        if label == "all":
            point_joins.append(
                f"ASOF LEFT JOIN (SELECT account_key, ts, cum_distinct FROM ff_distinct_all "
                f"WHERE direction = '{direction}') {alias} "
                f"ON {alias}.account_key = q.account_key AND q.ts > {alias}.ts"
            )
            point_selects.append(f"COALESCE({alias}.cum_distinct, 0) AS {column}")
        else:
            point_joins.append(
                f"ASOF LEFT JOIN (SELECT account_key, at_ts, active FROM ff_distinct_win "
                f"WHERE kind = '{column}') {alias} "
                f"ON {alias}.account_key = q.account_key AND q.ts >= {alias}.at_ts"
            )
            point_selects.append(f"COALESCE({alias}.active, 0) AS {column}")
    # Chain the as-of joins through a sequence of narrow tables.
    con.execute(
        "CREATE OR REPLACE TEMP TABLE ff_point_distinct AS SELECT q.account_key, q.ts, "
        + ", ".join(point_selects)
        + " FROM ff_query_pts q "
        + " ".join(point_joins)
    )
    con.execute(
        "CREATE OR REPLACE TEMP TABLE ff_pair_prior AS "
        "SELECT q.s, q.r, q.ts, p.cum_n AS pair_prior_n, p.ts AS pair_last_ts, "
        "v.cum_n AS reverse_prior_n, v.ts AS reverse_last_ts FROM ("
        "SELECT DISTINCT sender_account_key AS s, receiver_account_key AS r, timestamp AS ts "
        f"FROM {tx_view}) q "
        "ASOF LEFT JOIN ff_pair_ev p ON p.s = q.s AND p.r = q.r AND q.ts > p.ts "
        "ASOF LEFT JOIN ff_pair_ev v ON v.s = q.r AND v.r = q.s AND q.ts > v.ts"
    )
    window_selects = []
    for label, _ in DISTINCT_WINDOWS:
        window_selects += [
            f"sp.out_{label} AS s_distinct_out_{label}",
            f"sp.in_{label} AS s_distinct_in_{label}",
            f"rp.out_{label} AS r_distinct_out_{label}",
            f"rp.in_{label} AS r_distinct_in_{label}",
        ]
    con.execute(
        f"CREATE OR REPLACE TEMP TABLE {out} AS SELECT t.transaction_id, "
        "COALESCE(pp.pair_prior_n, 0) AS pair_prior_n, "
        f"{_minutes_between('t.timestamp', 'pp.pair_last_ts')} AS pair_mins_since_last, "
        "COALESCE(pp.reverse_prior_n, 0) AS reverse_prior_n, "
        f"{_minutes_between('t.timestamp', 'pp.reverse_last_ts')} AS reverse_mins_since_last, "
        "sp.out_all AS s_distinct_out_all, sp.in_all AS s_distinct_in_all, "
        "rp.out_all AS r_distinct_out_all, rp.in_all AS r_distinct_in_all, "
        + ", ".join(window_selects)
        + f" FROM {tx_view} t "
        "JOIN ff_pair_prior pp ON pp.s = t.sender_account_key "
        "AND pp.r = t.receiver_account_key AND pp.ts = t.timestamp "
        "JOIN ff_point_distinct sp ON sp.account_key = t.sender_account_key "
        "AND sp.ts = t.timestamp "
        "JOIN ff_point_distinct rp ON rp.account_key = t.receiver_account_key "
        "AND rp.ts = t.timestamp"
    )


# ---------------------------------------------------------------------------
# cycle (3-cycle closure)
# ---------------------------------------------------------------------------

CYCLE_COLUMNS: tuple[str, ...] = ("cycle3_closures", "cycle3_closures_7d")


def build_cycle(con: DuckDBPyConnection, tx_view: str, out: str) -> None:
    """Count third parties X with prior R->X and X->S edges (S->R closes a 3-cycle).

    Both edges must have been first seen strictly before ``t``; the 7-day
    variant additionally requires both first-seen minutes within ``[t - 7d, t)``.
    X excludes the sender and receiver themselves.
    """
    con.execute(
        "CREATE OR REPLACE TEMP TABLE ff_pair_first_c AS "
        "SELECT sender_account_key AS s, receiver_account_key AS r, MIN(timestamp) AS first_ts "
        f"FROM {tx_view} GROUP BY ALL"
    )
    con.execute(
        "CREATE OR REPLACE TEMP TABLE ff_cycle_pts AS "
        "SELECT q.s, q.r, q.ts, COUNT(rx.r) AS cycle3_closures, "
        "COUNT(CASE WHEN rx.first_ts >= q.ts - INTERVAL 7 DAY "
        "AND xs.first_ts >= q.ts - INTERVAL 7 DAY THEN 1 END) AS cycle3_closures_7d FROM ("
        "SELECT DISTINCT sender_account_key AS s, receiver_account_key AS r, timestamp AS ts "
        f"FROM {tx_view}) q "
        "LEFT JOIN ff_pair_first_c rx ON rx.s = q.r AND rx.first_ts < q.ts "
        "AND rx.r <> q.s AND rx.r <> q.r "
        "LEFT JOIN ff_pair_first_c xs ON xs.s = rx.r AND xs.r = q.s AND xs.first_ts < q.ts "
        "WHERE rx.r IS NULL OR xs.s IS NOT NULL "
        "GROUP BY q.s, q.r, q.ts"
    )
    con.execute(
        f"CREATE OR REPLACE TEMP TABLE {out} AS SELECT t.transaction_id, "
        "COALESCE(c.cycle3_closures, 0) AS cycle3_closures, "
        "COALESCE(c.cycle3_closures_7d, 0) AS cycle3_closures_7d "
        f"FROM {tx_view} t LEFT JOIN ff_cycle_pts c ON c.s = t.sender_account_key "
        "AND c.r = t.receiver_account_key AND c.ts = t.timestamp"
    )


# ---------------------------------------------------------------------------
# kyc (static account registry)
# ---------------------------------------------------------------------------

KYC_COLUMNS: tuple[str, ...] = (
    "s_entity_type",
    "r_entity_type",
    "s_entity_accounts",
    "r_entity_accounts",
    "same_entity",
)
ACCOUNTS_VIEW = "ff_accounts"


def build_kyc(con: DuckDBPyConnection, tx_view: str, out: str) -> None:
    """Static registry attributes joined by (bank without leading zeros, account).

    Requires a view ``ff_accounts(bank_id, account_number, entity_id,
    entity_type)``. Entity names are reduced to their type prefix; entity
    sequence numbers and IDs are never emitted as features.
    """
    con.execute(
        "CREATE OR REPLACE TEMP TABLE ff_acct_kyc AS "
        "SELECT ltrim(bank_id, '0') AS bank_norm, account_number, entity_id, entity_type, "
        "COUNT(*) OVER (PARTITION BY entity_id) AS entity_accounts "
        f"FROM {ACCOUNTS_VIEW}"
    )
    con.execute(
        f"CREATE OR REPLACE TEMP TABLE {out} AS SELECT t.transaction_id, "
        "COALESCE(sa.entity_type, 'unknown') AS s_entity_type, "
        "COALESCE(ra.entity_type, 'unknown') AS r_entity_type, "
        "sa.entity_accounts AS s_entity_accounts, ra.entity_accounts AS r_entity_accounts, "
        "(sa.entity_id IS NOT NULL AND sa.entity_id = ra.entity_id)::INTEGER AS same_entity "
        f"FROM {tx_view} t "
        "LEFT JOIN ff_acct_kyc sa ON sa.bank_norm = ltrim(t.sender_bank, '0') "
        "AND sa.account_number = t.sender_account "
        "LEFT JOIN ff_acct_kyc ra ON ra.bank_norm = ltrim(t.receiver_bank, '0') "
        "AND ra.account_number = t.receiver_account"
    )


FAMILIES: dict[str, FamilySpec] = {
    spec.name: spec
    for spec in (
        FamilySpec(
            "base",
            BASE_COLUMNS,
            ("payment_currency", "receiving_currency", "payment_format"),
            "per-account all-time sent/received counters",
            build_base,
        ),
        FamilySpec(
            "txlocal",
            TXLOCAL_COLUMNS,
            (),
            "stateless",
            build_txlocal,
            notes=[
                (
                    "log_received_over_paid is null unless paid and received amounts "
                    "share one currency and both are strictly positive; currency_mismatch "
                    "records the mismatched-currency case instead"
                ),
            ],
        ),
        FamilySpec(
            "velocity",
            VELOCITY_COLUMNS,
            (),
            "per-account minute-bucket counts for 7 days plus first/last-seen times",
            build_velocity,
        ),
        FamilySpec(
            "amount",
            AMOUNT_COLUMNS,
            (),
            "per-(account, currency) running log-amount moments and 24h/7d amount sums",
            build_amount,
        ),
        FamilySpec(
            "counterparty",
            COUNTERPARTY_COLUMNS,
            (),
            "per-pair count/last time; per-account distinct-counterparty sets "
            "(all-time and expiring 24h/7d)",
            build_counterparty,
        ),
        FamilySpec(
            "cycle",
            CYCLE_COLUMNS,
            (),
            "per-account in-neighbour first-seen map plus pair existence index",
            build_cycle,
        ),
        FamilySpec(
            "kyc",
            KYC_COLUMNS,
            ("s_entity_type", "r_entity_type"),
            "static account registry lookup",
            build_kyc,
            needs_accounts=True,
        ),
    )
}


def build_families(
    con: DuckDBPyConnection,
    tx_view: str,
    families: list[str],
    *,
    timings: dict[str, float] | None = None,
) -> str:
    """Build the requested families and return a joined table name.

    The returned table has ``transaction_id`` plus every requested family's
    columns, one row per input transaction, ordered by ``transaction_id``.
    """
    import time

    require_target_free(con, tx_view)
    unknown = [name for name in families if name not in FAMILIES]
    if unknown:
        raise ValueError(f"unknown feature families: {unknown}")
    tables: list[tuple[str, FamilySpec]] = []
    for name in families:
        spec = FAMILIES[name]
        table = f"ff_out_{name}"
        start = time.perf_counter()
        spec.builder(con, tx_view, table)
        if timings is not None:
            timings[name] = time.perf_counter() - start
        tables.append((table, spec))
    selects = ["f0.transaction_id"]
    joins = [f"FROM {tables[0][0]} f0"]
    for index, (table, spec) in enumerate(tables):
        alias = f"f{index}"
        if index:
            joins.append(f"JOIN {table} {alias} USING (transaction_id)")
        selects += [f"{alias}.{column}" for column in spec.columns]
    con.execute(
        "CREATE OR REPLACE TEMP TABLE ff_joined AS SELECT "
        + ", ".join(selects)
        + " "
        + " ".join(joins)
        + " ORDER BY transaction_id"
    )
    return "ff_joined"


def feature_columns(families: list[str]) -> list[str]:
    """Flattened feature column list for the given families, in order."""
    return [column for name in families for column in FAMILIES[name].columns]


def categorical_columns(families: list[str]) -> list[str]:
    """Categorical (string) feature columns for the given families."""
    return [column for name in families for column in FAMILIES[name].categorical]
