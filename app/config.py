"""Runtime configuration."""
from __future__ import annotations

import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("XRECON_DATA_DIR", "/data"))
DB_PATH = Path(os.environ.get("XRECON_DB_PATH", str(DATA_DIR / "xrecon.db")))

# Hard limits from the specification.
MAX_FACTS = 30
MAX_EQUATIONS = 30

# Tolerance used when verifying a witness against the closed rounding intervals
# and the (weighted) summation equations.
WITNESS_TOL = 1e-9


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
