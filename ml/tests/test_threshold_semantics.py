"""Threshold-rule vs fixed-capacity semantics tests (user-approved seam).

The validation-selected threshold admits a variable-size alert set (every
score at or above the cut). The fixed-capacity rule admits exactly K alerts
regardless of score values. These tests pin the two semantics distinctly so
a refactor cannot silently convert one into the other.
"""

from __future__ import annotations

import numpy as np

from ml.baselines import select_threshold_max_f1, select_top_k


def test_threshold_rule_selects_variable_sized_set() -> None:
    """The max-F1 cut admits every score at or above it, however many."""
    scores = np.array([0.95, 0.7, 0.7, 0.2, 0.1])
    labels = np.array([1, 1, 0, 0, 0])
    threshold = select_threshold_max_f1(scores, labels)
    alerted = scores >= threshold
    assert threshold in set(scores)
    assert int(alerted.sum()) == 3
    assert list(alerted) == [True, True, True, False, False]


def test_top_k_returns_exactly_k_in_stable_order() -> None:
    """Fixed capacity returns exactly K indices, ties broken by position."""
    scores = np.array([0.9, 0.8, 0.8, 0.1])
    top = select_top_k(scores, 2)
    assert list(top) == [0, 1]
    assert len(select_top_k(scores, 0)) == 0
    assert len(select_top_k(scores, 10)) == 4


def test_threshold_and_capacity_disagree_on_crafted_case() -> None:
    """The two rules are genuinely different selection semantics."""
    scores = np.array([0.99, 0.6, 0.59, 0.58, 0.01])
    labels = np.array([1, 0, 0, 0, 0])
    threshold = select_threshold_max_f1(scores, labels)
    threshold_set = set(np.flatnonzero(scores >= threshold).tolist())
    capacity_set = set(select_top_k(scores, 3).tolist())
    assert threshold_set == {0}
    assert capacity_set == {0, 1, 2}
    assert threshold_set != capacity_set
