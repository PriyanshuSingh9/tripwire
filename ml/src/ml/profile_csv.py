"""Read-only CSV profiler CLI.

Reports aggregate metadata only: row count, column names, dtypes, and null
counts. It never prints row values or samples, so labeled data cannot leak
through profiler output. Files with duplicate header names are rejected with
a clear error instead of being silently misread.
"""

import argparse
import json
import sys
from pathlib import Path

from ml.common import duplicate_names, read_header


def profile_csv(path: Path) -> dict[str, object]:
    """Return aggregate metadata for the CSV at path (no row values)."""
    import polars as pl

    if not path.is_file():
        raise FileNotFoundError(f"CSV not found: {path}")

    header = read_header(path)
    duplicates = duplicate_names(header)
    if duplicates:
        raise ValueError(
            f"duplicate header names {duplicates} in {path}; "
            "profile with positional handling instead"
        )

    frame = pl.scan_csv(path)
    schema = frame.collect_schema()
    summary = frame.select(
        pl.len().alias("num_rows"),
        pl.all().null_count(),
    ).collect(engine="streaming")
    null_counts = summary.row(0, named=True)
    return {
        "path": str(path),
        "num_rows": int(null_counts.pop("num_rows")),
        "num_columns": len(schema),
        "columns": [
            {"name": name, "dtype": str(dtype)} for name, dtype in schema.items()
        ],
        "null_counts": {str(k): int(v) for k, v in null_counts.items()},
    }


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Profile a local CSV file (aggregate metadata only, no row values)."
    )
    parser.add_argument("csv_path", type=Path, help="Path to a local CSV file")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    args = build_parser().parse_args(argv)
    try:
        profile = profile_csv(args.csv_path)
    except FileNotFoundError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    except ValueError as exc:
        # Raised only for schema-level problems (empty file, duplicate
        # headers); these messages contain column names, never row values.
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 -- CLI boundary; type name only, no data
        print(json.dumps({"error": f"profiling failed: {type(exc).__name__}"}), file=sys.stderr)
        return 1
    print(json.dumps(profile, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
