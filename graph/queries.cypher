// TraceAML bounded investigation queries (declarative reference).
//
// Generated from graph/src/graph/queries.py, which is the executable
// authority; tests/test_queries.py asserts this file contains every
// statement verbatim. queries.py selects among these fixed variants using
// validated integers and binds every value as a parameter.
//
// Bounds applied to every tool query:
//   hop depth ......... 1-3 (fixed MATCH variants UNIONed; no dynamic bounds)
//   time window ....... integer seconds within 1-2592000 (30 days)
//   row limit ......... LIMIT $result_limit, integer within 1-100 (per UNION
//                       branch for trace_funds)
//   execution timeout . client-side timeout= milliseconds (default 5000,
//                       max 30000) passed to graph.query; not a Cypher clause.
//
// Stored property allowlist (inference-safe only):
//   Transaction: transaction_id, timestamp, timestamp_epoch,
//     sender_account_key, receiver_account_key, amount_paid,
//     payment_currency, amount_received, receiving_currency, payment_format
//   Account: account_key, bank, account_id, last_epoch
// Forbidden by design: target_is_laundering, split, pattern metadata,
// typology ground truth.
//
// Timestamps: Transaction.timestamp is a "%Y-%m-%d %H:%M" display string;
// Transaction.timestamp_epoch (integer seconds) carries every range filter.
// Account.last_epoch is the loader-maintained latest sent-or-received epoch.
//
// Account-anchored statements bind the indexed anchor with WITH before
// expanding, so the planner never starts from a Transaction time scan.

// -- trace_funds: example branch (direction outgoing, depth 2) --
// build_trace_query UNIONs one such branch per (direction, depth).
MATCH (anchor:Transaction {transaction_id: $transaction_id})
WITH anchor, anchor.timestamp_epoch - $time_window_seconds AS lo,
  anchor.timestamp_epoch + $time_window_seconds AS hi
MATCH (anchor)-[:RECEIVED]->(a0)-[:SENT]->(t1)-[:RECEIVED]->(a1)-[:SENT]->(t2)-[:RECEIVED]->(a2)
WHERE t1.timestamp_epoch >= lo AND t1.timestamp_epoch <= hi AND t2.timestamp_epoch >= lo AND t2.timestamp_epoch <= hi
RETURN anchor.transaction_id AS anchor_id, 2 AS depth,
  t1.transaction_id AS transaction_id_1, t1.timestamp AS timestamp_1, t1.amount_paid AS amount_paid_1, t1.payment_currency AS payment_currency_1, t1.amount_received AS amount_received_1, t1.receiving_currency AS receiving_currency_1, t1.sender_account_key AS sender_account_key_1, t1.receiver_account_key AS receiver_account_key_1,
  t2.transaction_id AS transaction_id_2, t2.timestamp AS timestamp_2, t2.amount_paid AS amount_paid_2, t2.payment_currency AS payment_currency_2, t2.amount_received AS amount_received_2, t2.receiving_currency AS receiving_currency_2, t2.sender_account_key AS sender_account_key_2, t2.receiver_account_key AS receiver_account_key_2,
  NULL AS transaction_id_3, NULL AS timestamp_3, NULL AS amount_paid_3, NULL AS payment_currency_3, NULL AS amount_received_3, NULL AS receiving_currency_3, NULL AS sender_account_key_3, NULL AS receiver_account_key_3,
  a0.account_key AS account_key_0, a1.account_key AS account_key_1, a2.account_key AS account_key_2, NULL AS account_key_3
LIMIT $result_limit

// -- TRACE_COUNTERPARTIES_BY_TX --
MATCH (anchor:Transaction {transaction_id: $transaction_id})
MATCH (sender:Account)-[:SENT]->(anchor)-[:RECEIVED]->(receiver:Account)
RETURN anchor.transaction_id AS transaction_id, anchor.timestamp AS timestamp,
  sender.account_key AS sender_key, receiver.account_key AS receiver_key,
  anchor.amount_paid AS amount_paid, anchor.payment_currency AS payment_currency,
  anchor.amount_received AS amount_received,
  anchor.receiving_currency AS receiving_currency,
  anchor.payment_format AS payment_format
LIMIT 1

// -- TRACE_SIBLINGS_BY_TX --
MATCH (anchor:Transaction {transaction_id: $transaction_id})
MATCH (sender:Account)-[:SENT]->(anchor)
WITH anchor, sender, anchor.timestamp_epoch - $time_window_seconds AS lo,
  anchor.timestamp_epoch + $time_window_seconds AS hi
MATCH (sender)-[:SENT]->(t)
WHERE t.transaction_id <> anchor.transaction_id
  AND t.timestamp_epoch >= lo AND t.timestamp_epoch <= hi
RETURN t.transaction_id AS transaction_id, t.timestamp AS timestamp,
  sender.account_key AS sender_key, t.receiver_account_key AS receiver_key,
  t.amount_paid AS amount_paid, t.payment_currency AS payment_currency,
  t.amount_received AS amount_received, t.receiving_currency AS receiving_currency,
  t.payment_format AS payment_format
ORDER BY t.timestamp_epoch
LIMIT $result_limit

// -- TRACE_ACCOUNT_LAST_EPOCH --
MATCH (a:Account {account_key: $account_key})
RETURN a.last_epoch AS epoch
LIMIT 1

// -- TRACE_ACCOUNT_LATEST_EPOCH --
MATCH (a:Account {account_key: $account_key})
OPTIONAL MATCH (a)-[:SENT]->(s:Transaction)
WITH a, max(s.timestamp_epoch) AS sent_max
OPTIONAL MATCH (a)<-[:RECEIVED]-(r:Transaction)
WITH sent_max, max(r.timestamp_epoch) AS received_max
WITH CASE WHEN received_max IS NULL OR sent_max >= received_max
  THEN sent_max ELSE received_max END AS epoch
WHERE epoch IS NOT NULL
RETURN epoch
LIMIT 1

// -- TRACE_COUNTERPARTIES_BY_ACCOUNT --
MATCH (a:Account {account_key: $account_key})
WITH a
MATCH (a)-[:SENT]->(t)
WHERE t.timestamp_epoch >= $min_epoch AND t.timestamp_epoch <= $max_epoch
RETURN t.transaction_id AS transaction_id, t.timestamp AS timestamp,
  a.account_key AS sender_key, t.receiver_account_key AS receiver_key,
  t.amount_paid AS amount_paid, t.payment_currency AS payment_currency,
  t.amount_received AS amount_received, t.receiving_currency AS receiving_currency,
  t.payment_format AS payment_format, 'sent' AS involvement
ORDER BY t.timestamp_epoch DESC
LIMIT $result_limit

// -- TRACE_COUNTERPARTIES_BY_ACCOUNT_INCOMING --
MATCH (a:Account {account_key: $account_key})
WITH a
MATCH (a)<-[:RECEIVED]-(t)
WHERE t.timestamp_epoch >= $min_epoch AND t.timestamp_epoch <= $max_epoch
RETURN t.transaction_id AS transaction_id, t.timestamp AS timestamp,
  t.sender_account_key AS sender_key, a.account_key AS receiver_key,
  t.amount_paid AS amount_paid, t.payment_currency AS payment_currency,
  t.amount_received AS amount_received, t.receiving_currency AS receiving_currency,
  t.payment_format AS payment_format, 'received' AS involvement
ORDER BY t.timestamp_epoch DESC
LIMIT $result_limit

// -- TRACE_FAN_OUT --
MATCH (a:Account {account_key: $account_key})
WITH a
MATCH (a)-[:SENT]->(t)
WHERE t.timestamp_epoch >= $min_epoch AND t.timestamp_epoch <= $max_epoch
WITH t ORDER BY t.timestamp_epoch
WITH count(DISTINCT t.receiver_account_key) AS distinct_counterparties,
  count(t) AS transfers, collect(t) AS ordered
RETURN distinct_counterparties, transfers,
  CASE WHEN distinct_counterparties >= $min_fanout
    THEN [x IN ordered[0..$result_limit] | [x.receiver_account_key, x.transaction_id,
      x.timestamp, x.amount_paid, x.payment_currency, x.amount_received,
      x.receiving_currency]]
    ELSE [] END AS details

// -- TRACE_FAN_IN --
MATCH (a:Account {account_key: $account_key})
WITH a
MATCH (a)<-[:RECEIVED]-(t)
WHERE t.timestamp_epoch >= $min_epoch AND t.timestamp_epoch <= $max_epoch
WITH t ORDER BY t.timestamp_epoch
WITH count(DISTINCT t.sender_account_key) AS distinct_counterparties,
  count(t) AS transfers, collect(t) AS ordered
RETURN distinct_counterparties, transfers,
  CASE WHEN distinct_counterparties >= $min_fanout
    THEN [x IN ordered[0..$result_limit] | [x.sender_account_key, x.transaction_id,
      x.timestamp, x.amount_paid, x.payment_currency, x.amount_received,
      x.receiving_currency]]
    ELSE [] END AS details

// -- TRACE_PASS_THROUGH_OUT_HISTOGRAM --
MATCH (b:Account {account_key: $account_key})
WITH b
MATCH (b)-[:SENT]->(t)
WHERE t.timestamp_epoch >= $min_epoch AND t.timestamp_epoch <= $max_epoch
WITH t.timestamp_epoch AS e, count(t) AS c
RETURN collect((e - $min_epoch) * 4294967296 + c) AS histogram

// -- TRACE_PASS_THROUGH_FIRST_INCOMING --
MATCH (b:Account {account_key: $account_key})
WITH b
MATCH (b)<-[:RECEIVED]-(t)
WHERE t.timestamp_epoch >= $min_epoch AND t.timestamp_epoch <= $max_epoch
  AND any(iv IN $intervals WHERE t.timestamp_epoch >= iv[0] AND t.timestamp_epoch <= iv[1])
WITH t ORDER BY t.timestamp_epoch, t.transaction_id LIMIT $result_limit
RETURN collect([t.transaction_id, t.timestamp_epoch]) AS incoming

// -- TRACE_PASS_THROUGH_DETAIL --
MATCH (b:Account {account_key: $account_key})
WITH b
MATCH (b)-[:SENT]->(t2)
WHERE t2.timestamp_epoch IN $out_epochs
WITH t2 ORDER BY t2.transaction_id
WITH t2.timestamp_epoch AS e2, collect(t2)[0..$result_limit] AS legs
UNWIND $items AS item
WITH item, legs WHERE e2 = item[2]
UNWIND legs AS t2
MATCH (t1:Transaction {transaction_id: item[1]})
RETURN item[0] AS ordinal, t1.sender_account_key AS entry_key,
  $account_key AS middle_key, t2.receiver_account_key AS exit_key,
  t1.transaction_id AS transaction_id_1, t1.timestamp AS timestamp_1,
  t1.amount_paid AS amount_paid_1, t1.payment_currency AS payment_currency_1,
  t1.amount_received AS amount_received_1, t1.receiving_currency AS receiving_currency_1,
  t2.transaction_id AS transaction_id_2, t2.timestamp AS timestamp_2,
  t2.amount_paid AS amount_paid_2, t2.payment_currency AS payment_currency_2,
  t2.amount_received AS amount_received_2, t2.receiving_currency AS receiving_currency_2
ORDER BY ordinal, transaction_id_2
LIMIT $result_limit
