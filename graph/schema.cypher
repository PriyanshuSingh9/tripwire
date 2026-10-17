// Execute these indexes before creating UNIQUE NODE constraints with the
// FalkorDB client's create_constraint API.
CREATE INDEX FOR (a:Account) ON (a.account_key);
CREATE INDEX FOR (t:Transaction) ON (t.transaction_id);

// Target labels and typology ground truth are intentionally absent from this schema.
