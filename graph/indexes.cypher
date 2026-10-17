// Iteration 1 created range indexes on Transaction.timestamp and
// Transaction.timestamp_epoch. They were removed (docs/decisions.md, ADR-017):
// no approved query anchors on time, and their presence let the planner
// replace the indexed account anchor with a window-wide timestamp scan.
// The only indexes are the lookup keys in schema.cypher.
