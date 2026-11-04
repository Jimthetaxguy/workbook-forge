"""Pin the clock and seed of volatile functions for the whole suite.

NOW, TODAY and the RAND family read one context per recalculation. Without a
pinned context two calculations of the same workbook differ, which would make
every round-trip, parity and fixture comparison over a volatile formula
nondeterministic. Both engines read these variables (the Rust engine through
the native bridge and the CLI subprocesses inherit them). Tests of the
unpinned clock remove them with ``monkeypatch.delenv``.
"""

import os

PINNED_NOW = "2026-01-15T12:00:00Z"
PINNED_SEED = "20260115"

os.environ["FORGE_CALC_NOW"] = PINNED_NOW
os.environ["FORGE_CALC_SEED"] = PINNED_SEED
os.environ.pop("FORGE_CALC_UTC_OFFSET_MINUTES", None)
