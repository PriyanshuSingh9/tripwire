"""Direct verifier tests: unreturned IDs and mismatched paths are rejected."""

from __future__ import annotations

import pytest

from api.verifier import EvidenceValidationError, validate_report

EVIDENCE_IDS = {"tx_1", "tx_2"}
EVIDENCE_KEYS = {"1::a", "2::b"}
GOOD_PATH = {
    "transaction_ids": ["tx_1", "tx_2"],
    "account_keys": ["1::a", "2::b"],
}


def test_consistent_report_passes() -> None:
    """IDs drawn from the evidence set validate cleanly."""
    validate_report(["tx_1"], [], EVIDENCE_IDS, [GOOD_PATH], EVIDENCE_KEYS)


def test_unreturned_supporting_id_rejected() -> None:
    """A supporting ID absent from query results is rejected."""
    with pytest.raises(EvidenceValidationError):
        validate_report(["tx_ghost"], [], EVIDENCE_IDS, [GOOD_PATH], EVIDENCE_KEYS)


def test_unreturned_counterevidence_id_rejected() -> None:
    """Counterevidence IDs face the same evidence-set rule."""
    with pytest.raises(EvidenceValidationError):
        validate_report(["tx_1"], ["tx_ghost"], EVIDENCE_IDS, [GOOD_PATH], EVIDENCE_KEYS)


def test_path_with_unknown_transaction_rejected() -> None:
    """Paths must reference evidenced transactions only."""
    bad = {"transaction_ids": ["tx_1", "tx_ghost"], "account_keys": ["1::a"]}
    with pytest.raises(EvidenceValidationError):
        validate_report(["tx_1"], [], EVIDENCE_IDS, [bad], EVIDENCE_KEYS)


def test_path_with_unknown_account_rejected() -> None:
    """Paths must reference evidenced accounts only."""
    bad = {"transaction_ids": ["tx_1"], "account_keys": ["1::a", "9::ghost"]}
    with pytest.raises(EvidenceValidationError):
        validate_report(["tx_1"], [], EVIDENCE_IDS, [bad], EVIDENCE_KEYS)
