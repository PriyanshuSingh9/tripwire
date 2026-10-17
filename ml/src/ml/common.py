"""Shared HI-Small dataset conventions for Iteration 1 (data version 8).

Centralizes source filenames, expected headers, and streaming helpers so the
inspection and preparation commands agree on schema handling. The source
transaction file uses the header name ``Account`` twice; position 2 is the
sender account and position 4 is the receiver account. All handling here is
positional, never name-based, for those columns.
"""

from __future__ import annotations

import csv
import hashlib
import re
from pathlib import Path

DATA_VERSION = 8

TRANS_FILENAME = "HI-Small_Trans.csv"
ACCOUNTS_FILENAME = "HI-Small_accounts.csv"
PATTERNS_FILENAME = "HI-Small_Patterns.txt"

TRANS_RAW_HEADER: tuple[str, ...] = (
    "Timestamp",
    "From Bank",
    "Account",
    "To Bank",
    "Account",
    "Amount Received",
    "Receiving Currency",
    "Amount Paid",
    "Payment Currency",
    "Payment Format",
    "Is Laundering",
)

TRANS_COLUMNS: tuple[str, ...] = (
    "timestamp",
    "sender_bank",
    "sender_account",
    "receiver_bank",
    "receiver_account",
    "amount_received",
    "receiving_currency",
    "amount_paid",
    "payment_currency",
    "payment_format",
    "is_laundering",
)

ACCOUNTS_RAW_HEADER: tuple[str, ...] = (
    "Bank Name",
    "Bank ID",
    "Account Number",
    "Entity ID",
    "Entity Name",
)

ACCOUNTS_COLUMNS: tuple[str, ...] = (
    "bank_name",
    "bank_id",
    "account_number",
    "entity_id",
    "entity_name",
)

# Zero-padded ``YYYY/MM/DD HH:MM`` (minute resolution). Ordering always uses
# parsed datetimes rather than relying on string sort behavior.
TIMESTAMP_RE = re.compile(r"\d{4}/\d{2}/\d{2} \d{2}:\d{2}")
TIMESTAMP_FORMAT = "%Y/%m/%d %H:%M"
TIMESTAMP_OUTPUT_FORMAT = "%Y-%m-%d %H:%M"

LABEL_VALUES = ("0", "1")


def sha256_of(path: Path, chunk_size: int = 1 << 20) -> str:
    """Stream a file and return its hex sha256 without loading it fully."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_header(path: Path) -> list[str]:
    """Return the first CSV header row as a list of column names."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            return next(reader)
        except StopIteration:
            raise ValueError(f"empty CSV file: {path}") from None


def duplicate_names(header: list[str]) -> list[str]:
    """Return header names occurring more than once, preserving order."""
    seen: set[str] = set()
    duplicates: list[str] = []
    for name in header:
        if name in seen and name not in duplicates:
            duplicates.append(name)
        seen.add(name)
    return duplicates


def require_header(actual: list[str], expected: tuple[str, ...], *, filename: str) -> None:
    """Fail loudly when a source header does not match the pinned expectation."""
    if list(actual) != list(expected):
        raise ValueError(
            f"unexpected header in {filename}: got {actual}, expected {list(expected)}"
        )
