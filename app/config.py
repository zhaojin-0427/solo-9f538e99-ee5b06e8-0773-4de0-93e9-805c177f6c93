"""Runtime configuration."""
from __future__ import annotations

import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("XRECON_DATA_DIR", "/data"))
DB_PATH = Path(os.environ.get("XRECON_DB_PATH", str(DATA_DIR / "xrecon.db")))

# Hard limits from the specification.
MAX_FACTS = 30
MAX_EQUATIONS = 30

# Defensive bounds so exact rational arithmetic cannot be driven to huge
# integers by a hostile instance.  Real filings stay many orders of magnitude
# inside these limits.  The bound also keeps every derived amount well below
# Python's default 4300-digit int/str conversion limit.
MAX_AMOUNT_DIGITS = 2000     # significant digits / decimal magnitude
MAX_DECIMALS_ABS = 2000      # |decimals| for a fact

# Verification is exact (rational arithmetic), so no floating-point tolerance is
# applied when checking a witness against its intervals or the equations.


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
