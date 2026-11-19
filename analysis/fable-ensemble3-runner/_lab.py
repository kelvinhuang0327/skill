"""Locate the sealed lab and R2 modules once, for every module in this package (stdlib only)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ANALYSIS = HERE.parent
LAB, R1, BANK = ANALYSIS / "fable-ensemble3-lab", ANALYSIS / "fable-ensemble3-r1", ANALYSIS / "fable-ensemble3-bank"
if str(LAB) not in sys.path:
    sys.path.insert(0, str(LAB))

import budget_ledger  # noqa: E402,F401
import decision_engine  # noqa: E402,F401
import exploration_readiness  # noqa: E402,F401
import offline_replay  # noqa: E402,F401
import responses_adapter  # noqa: E402,F401

for _module in (budget_ledger, decision_engine, exploration_readiness, offline_replay, responses_adapter):
    # a worktree must never silently import a sibling checkout's copy of a sealed module
    assert Path(_module.__file__).resolve().parent == LAB, _module.__file__
