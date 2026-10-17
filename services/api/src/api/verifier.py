"""Deterministic evidence verifier for investigation reports.

A report may reference only identifiers returned by executed graph queries:
every supporting transaction ID must appear in the evidence transaction
set, every path transaction must be evidenced, and every path account must
be an evidenced account. Anything else raises; routes map the failure to an
explicit error without raw exception text.
"""

from __future__ import annotations


class EvidenceValidationError(Exception):
    """Raised when a report cites anything outside the retrieved evidence."""


def validate_report(
    supporting_ids: list[str],
    counterevidence_ids: list[str],
    evidence_transaction_ids: set[str],
    paths: list[dict[str, object]],
    evidence_account_keys: set[str],
) -> None:
    """Raise EvidenceValidationError on any unreturned reference."""
    for transaction_id in supporting_ids:
        if transaction_id not in evidence_transaction_ids:
            raise EvidenceValidationError(
                "report cites a transaction absent from the evidence set"
            )
    for transaction_id in counterevidence_ids:
        if transaction_id not in evidence_transaction_ids:
            raise EvidenceValidationError(
                "counterevidence cites a transaction absent from the evidence set"
            )
    for path in paths:
        for transaction_id in path.get("transaction_ids", []):
            if transaction_id not in evidence_transaction_ids:
                raise EvidenceValidationError(
                    "path references a transaction absent from the evidence set"
                )
        for account_key in path.get("account_keys", []):
            if account_key not in evidence_account_keys:
                raise EvidenceValidationError(
                    "path references an account absent from the evidence set"
                )
