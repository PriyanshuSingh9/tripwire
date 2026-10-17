"""Pytest bootstrap for the traceaml-graph project (no installed package needed)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
