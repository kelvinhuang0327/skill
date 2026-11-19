#!/usr/bin/env python3
"""Offline planning numbers for FABLE_ENSEMBLE3_R1 (stdlib only, no model calls).

Model: runs are conditionally independent given the item (fresh sessions), and
the single-run success probability p_i varies across items as Beta(a, b).
Everything below is closed-form or an exact finite sum -- no sampling.

    python3 design_check.py
"""

from __future__ import annotations

import math
import sys
from math import comb, exp, lgamma
from statistics import NormalDist
from typing import Dict, Iterable, List, Optional, Tuple

sys.dont_write_bytecode = True

# name -> (a, b, reading).  A reading of the difficulty mix, not a fit: nothing
# here is estimated from Luna+Fable data.
SCENARIOS: Dict[str, Tuple[float, float, str]] = {
    "mostly_solved": (8.0, 2.0, "usually succeeds (mean 0.80)"),
    "contested": (3.0, 2.0, "usually succeeds, many middling items (mean 0.60)"),
    "symmetric": (2.0, 2.0, "easy and hard items balanced (mean 0.50)"),
    "u_shaped": (0.5, 0.5, "items reliably solved or reliably missed (mean 0.50)"),
    "mostly_missed": (2.0, 4.0, "hard pool, usually fails (mean 0.33)"),
}


def maj3(p: float) -> float:
    """P(>= 2 of 3 iid successes): plain majority of three."""
    return 3 * p * p - 2 * p ** 3


def cov3(p: float) -> float:
    """P(>= 1 of 3 iid successes): pass@3, the coverage ceiling."""
    return 1 - (1 - p) ** 3


def triple_estimates(k: int, n: int) -> Tuple[float, float, float]:
    """(pass@1, maj@3, pass@3) estimated from k successes in n >= 3 runs.

    Averages over all C(n, 3) triples, which is unbiased for iid runs.  The
    triples are not independent samples; they only reduce estimator noise.
    """
    if n < 3 or not 0 <= k <= n:
        raise ValueError(f"need n >= 3 and 0 <= k <= n, got k={k} n={n}")
    triples = comb(n, 3)
    majority = (comb(k, 3) + comb(k, 2) * (n - k)) / triples
    coverage = 1 - comb(n - k, 3) / triples
    return k / n, majority, coverage


def _lbeta(a: float, b: float) -> float:
    return lgamma(a) + lgamma(b) - lgamma(a + b)


def beta_binomial_pmf(n: int, a: float, b: float) -> List[float]:
    """P(k successes in n runs) when p ~ Beta(a, b)."""
    return [
        exp(math.log(comb(n, k)) + _lbeta(k + a, n - k + b) - _lbeta(a, b))
        for k in range(n + 1)
    ]


def _moment(a: float, b: float, r: int) -> float:
    """E[p^r] for p ~ Beta(a, b)."""
    out = 1.0
    for j in range(r):
        out *= (a + j) / (a + b + j)
    return out


def population_rates(a: float, b: float) -> Dict[str, float]:
    """True single / maj@3 / pass@3 averaged over items, p ~ Beta(a, b)."""
    m1, m2, m3 = (_moment(a, b, r) for r in (1, 2, 3))
    return {
        "single": m1,
        "maj3": 3 * m2 - 2 * m3,
        "cov3": 1 - _moment(b, a, 3),  # E[(1-p)^3] is the Beta(b, a) moment
    }


def population_gains(a: float, b: float) -> Dict[str, float]:
    rates = population_rates(a, b)
    return {
        "maj3": rates["maj3"] - rates["single"],
        "cov3": rates["cov3"] - rates["single"],
    }


def estimator_moments(a: float, b: float, n: int) -> Dict[str, Tuple[float, float]]:
    """Exact (mean, sd) of the per-item gain estimates from n runs per item."""
    pmf = beta_binomial_pmf(n, a, b)
    out: Dict[str, Tuple[float, float]] = {}
    for name, index in (("maj3", 1), ("cov3", 2)):
        diffs = []
        for k in range(n + 1):
            est = triple_estimates(k, n)
            diffs.append(est[index] - est[0])
        mean = sum(w * d for w, d in zip(pmf, diffs))
        var = sum(w * d * d for w, d in zip(pmf, diffs)) - mean ** 2
        out[name] = (mean, math.sqrt(max(var, 0.0)))
    return out


def items_needed(
    delta: float, sd: float, alpha: float = 0.05, power: float = 0.80
) -> Optional[int]:
    """Items for a one-sided z test of mean per-item gain; None if delta <= 0."""
    if delta <= 0:
        return None
    z = NormalDist().inv_cdf
    return math.ceil(((z(1 - alpha) + z(power)) * sd / delta) ** 2)


def ci_halfwidth(sd: float, items: int, level: float = 0.90) -> float:
    return NormalDist().inv_cdf(0.5 + level / 2) * sd / math.sqrt(items)


def selection_on_same_runs(
    a: float, b: float, n: int, keep: Iterable[int]
) -> Dict[str, float]:
    """Keep items by their observed success count k, then score the same runs.

    `measured_gain` is what the analysis would report on the kept items;
    `true_gain` is the expected real maj@3 - single gain on those items
    (posterior of p given k is Beta(a + k, b + n - k)).
    """
    keep = list(keep)
    pmf = beta_binomial_pmf(n, a, b)
    mass = sum(pmf[k] for k in keep)
    measured = 0.0
    true = 0.0
    for k in keep:
        est = triple_estimates(k, n)
        measured += pmf[k] * (est[1] - est[0])
        true += pmf[k] * population_gains(a + k, b + n - k)["maj3"]
    return {
        "kept_fraction": mass,
        "measured_gain": measured / mass,
        "true_gain": true / mass,
    }


def _fmt_items(value: Optional[int]) -> str:
    return "never" if value is None else str(value)


def main() -> int:
    n = 5
    print(f"Planning table: N={n} runs/item, one-sided alpha=0.05, power=0.80")
    print("maj = plain majority of 3 vs single run; cov = pass@3 vs single run\n")
    header = (
        f"{'scenario':<14}{'single':>7}{'d_maj':>8}{'d_cov':>8}"
        f"{'items_maj':>11}{'items_cov':>11}{'ci90@12 maj':>13}{'ci90@12 cov':>13}"
    )
    print(header)
    for name, (a, b, _reading) in SCENARIOS.items():
        gains = population_gains(a, b)
        rates = population_rates(a, b)
        moments = estimator_moments(a, b, n)
        print(
            f"{name:<14}{rates['single']:>7.2f}{gains['maj3']:>+8.3f}{gains['cov3']:>+8.3f}"
            f"{_fmt_items(items_needed(gains['maj3'], moments['maj3'][1])):>11}"
            f"{_fmt_items(items_needed(gains['cov3'], moments['cov3'][1])):>11}"
            f"{ci_halfwidth(moments['maj3'][1], 12):>13.3f}"
            f"{ci_halfwidth(moments['cov3'][1], 12):>13.3f}"
        )

    print("\nRuns per item vs items needed (scenario 'contested', maj):")
    a, b, _ = SCENARIOS["contested"]
    for runs in (3, 5, 10, 20):
        delta = population_gains(a, b)["maj3"]
        sd = estimator_moments(a, b, runs)["maj3"][1]
        print(f"  N={runs:<3} sd={sd:.3f}  items={_fmt_items(items_needed(delta, sd))}")

    print("\nScreening items on the same N=3 runs, then scoring those runs:")
    for scenario, keep in (
        ("symmetric", {2}),
        ("symmetric", {1, 2}),
        ("contested", {1, 2}),
        ("contested", {0, 1, 2, 3}),
    ):
        a, b, _ = SCENARIOS[scenario]
        row = selection_on_same_runs(a, b, 3, keep)
        print(
            f"  {scenario:<10} Beta({a:g},{b:g}) keep k in {sorted(keep)}: "
            f"kept {row['kept_fraction']:.0%} of items, "
            f"measured {row['measured_gain']:+.3f} vs true {row['true_gain']:+.3f}"
        )

    print(f"\nPer-item maj@3 gain is bounded by sqrt(3)/18 = {math.sqrt(3) / 18:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
