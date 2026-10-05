#!/usr/bin/env python3
"""Does a nominal 90% interval for a mean over clustered items cover 90% of the time?

Stdlib, seeded, about half a minute:  python3 interval_check.py

Compares the item-level bootstrap, the hierarchical bootstrap (families, then items) and
the cluster-robust t(G-1) interval that ensemble_scoring.cluster_interval uses.  Data are
normal with a family random effect (intra-family correlation `icc`) and equal family
sizes: a stress test of the interval choice, not a claim about the real distribution of
per-item effects.
"""

from __future__ import annotations

import importlib.util
import math
import random
import sys
from pathlib import Path
from typing import Dict

sys.dont_write_bytecode = True


def _scoring():
    module = sys.modules.get("ensemble_scoring")
    if module is None:
        spec = importlib.util.spec_from_file_location(
            "ensemble_scoring", Path(__file__).with_name("ensemble_scoring.py")
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["ensemble_scoring"] = module  # dataclasses resolve annotations through it
        spec.loader.exec_module(module)
    return module


def coverage(
    g: int, icc: float, reps: int, boots: int, seed: int, m: int = 6, mu: float = 0.02
) -> Dict[str, float]:
    """Share of `reps` simulated studies whose 90% interval contains the true mean `mu`."""
    scoring = _scoring()
    rng = random.Random(seed)
    sd_family, sd_item = math.sqrt(icc), math.sqrt(1 - icc)
    lo_at, hi_at = int(0.05 * boots), min(boots - 1, int(0.95 * boots))
    hits = {"item_bootstrap": 0, "hier_bootstrap": 0, "cluster_t": 0}
    for _ in range(reps):
        families = []
        for _ in range(g):
            shift = rng.gauss(0, sd_family)
            families.append([mu + shift + rng.gauss(0, sd_item) for _ in range(m)])
        flat = [x for family in families for x in family]
        n = len(flat)

        item_means = sorted(sum(rng.choices(flat, k=n)) / n for _ in range(boots))
        hits["item_bootstrap"] += item_means[lo_at] <= mu <= item_means[hi_at]

        hier_means = []
        for _ in range(boots):
            total = 0.0
            for family in rng.choices(families, k=g):
                total += sum(rng.choices(family, k=m))
            hier_means.append(total / n)
        hier_means.sort()
        hits["hier_bootstrap"] += hier_means[lo_at] <= mu <= hier_means[hi_at]

        interval = scoring.cluster_interval({str(i): f for i, f in enumerate(families)})
        hits["cluster_t"] += interval.lo <= mu <= interval.hi
    return {name: count / reps for name, count in hits.items()}


def main() -> int:
    print("Coverage of nominal 90% intervals; 6 items per family, 400 studies each")
    print(f"{'G':>3} {'icc':>4}  {'item_bootstrap':>15} {'hier_bootstrap':>15} {'cluster_t':>10}")
    for icc in (0.3, 0.7):
        for g in (4, 6, 8, 12, 20):
            row = coverage(g, icc, reps=400, boots=200, seed=20261002 + g)
            print(
                f"{g:>3} {icc:>4}  {row['item_bootstrap']:>15.3f} "
                f"{row['hier_bootstrap']:>15.3f} {row['cluster_t']:>10.3f}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
