#!/usr/bin/env python3
"""Offline tests for the FABLE_ENSEMBLE3_R1 planning numbers."""

from __future__ import annotations

import importlib.util
import itertools
import math
import sys
import unittest
from pathlib import Path

# Importing the sibling module must not leave __pycache__ in the repository.
sys.dont_write_bytecode = True

MODULE_PATH = Path(__file__).with_name("design_check.py")
_spec = importlib.util.spec_from_file_location("ensemble3_design_check", MODULE_PATH)
assert _spec is not None and _spec.loader is not None
dc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dc)

BETAS = [(8.0, 2.0), (3.0, 2.0), (2.0, 2.0), (0.5, 0.5), (2.0, 4.0), (1.0, 1.0)]


class ClosedFormTests(unittest.TestCase):
    def test_closed_forms_match_enumerating_three_runs(self):
        for p in (0.0, 0.1, 0.37, 0.5, 0.8, 1.0):
            maj = cov = 0.0
            for outcome in itertools.product((0, 1), repeat=3):
                weight = math.prod(p if x else 1 - p for x in outcome)
                maj += weight * (sum(outcome) >= 2)
                cov += weight * (sum(outcome) >= 1)
            self.assertAlmostEqual(dc.maj3(p), maj, places=12)
            self.assertAlmostEqual(dc.cov3(p), cov, places=12)

    def test_per_item_majority_gain_is_bounded_and_antisymmetric(self):
        grid = [i / 10000 for i in range(10001)]
        gains = [dc.maj3(p) - p for p in grid]
        self.assertAlmostEqual(max(gains), math.sqrt(3) / 18, places=5)
        self.assertAlmostEqual(min(gains), -math.sqrt(3) / 18, places=5)
        for p in (0.1, 0.3, 0.7):
            self.assertAlmostEqual(dc.maj3(p) - p, -(dc.maj3(1 - p) - (1 - p)), places=12)

    def test_symmetric_difficulty_has_zero_average_majority_gain(self):
        for a in (0.5, 1.0, 2.0, 5.0):
            self.assertAlmostEqual(dc.population_gains(a, a)["maj3"], 0.0, places=12)

    def test_pmf_sums_to_one(self):
        for a, b in BETAS:
            self.assertAlmostEqual(sum(dc.beta_binomial_pmf(7, a, b)), 1.0, places=12)


class EstimatorTests(unittest.TestCase):
    def test_estimates_match_brute_force_over_triples(self):
        n = 5
        for k in range(n + 1):
            runs = [1] * k + [0] * (n - k)
            triples = list(itertools.combinations(runs, 3))
            majority = sum(sum(t) >= 2 for t in triples) / len(triples)
            coverage = sum(sum(t) >= 1 for t in triples) / len(triples)
            single, maj_hat, cov_hat = dc.triple_estimates(k, n)
            self.assertAlmostEqual(single, k / n, places=12)
            self.assertAlmostEqual(maj_hat, majority, places=12)
            self.assertAlmostEqual(cov_hat, coverage, places=12)

    def test_per_item_estimates_are_unbiased_for_the_population_gain(self):
        for a, b in BETAS:
            truth = dc.population_gains(a, b)
            for n in (3, 4, 5, 10):
                moments = dc.estimator_moments(a, b, n)
                self.assertAlmostEqual(moments["maj3"][0], truth["maj3"], places=10)
                self.assertAlmostEqual(moments["cov3"][0], truth["cov3"], places=10)

    def test_invalid_counts_are_rejected(self):
        for k, n in ((0, 2), (4, 3), (-1, 5)):
            with self.assertRaises(ValueError):
                dc.triple_estimates(k, n)


class AnalysisRuleTests(unittest.TestCase):
    def test_screening_on_the_same_runs_inflates_the_gain(self):
        row = dc.selection_on_same_runs(2.0, 2.0, 3, {2})
        # k=2 of 3: single is 2/3 and the majority of the only triple is right.
        self.assertAlmostEqual(row["measured_gain"], 1 / 3, places=12)
        self.assertLess(row["true_gain"], 0.05)

    def test_middle_band_screening_is_biased_when_difficulty_is_skewed(self):
        # Symmetric difficulty hides the bias for k in {1, 2}; a skewed pool does not.
        symmetric = dc.selection_on_same_runs(2.0, 2.0, 3, {1, 2})
        skewed = dc.selection_on_same_runs(3.0, 2.0, 3, {1, 2})
        self.assertAlmostEqual(symmetric["measured_gain"], symmetric["true_gain"], places=10)
        # Computed: measured +0.048 vs true +0.024, i.e. the reported gain is doubled.
        self.assertAlmostEqual(skewed["measured_gain"], 0.048, places=3)
        self.assertAlmostEqual(skewed["true_gain"], 0.024, places=3)

    def test_scoring_the_full_pool_is_unbiased(self):
        for a, b in BETAS:
            row = dc.selection_on_same_runs(a, b, 3, {0, 1, 2, 3})
            self.assertAlmostEqual(row["kept_fraction"], 1.0, places=12)
            self.assertAlmostEqual(row["measured_gain"], row["true_gain"], places=10)

    def test_items_needed(self):
        self.assertIsNone(dc.items_needed(0.0, 0.1))
        self.assertIsNone(dc.items_needed(-0.02, 0.1))
        self.assertEqual(dc.items_needed(0.05, 0.10), 25)
        self.assertGreater(dc.items_needed(0.02, 0.10), dc.items_needed(0.05, 0.10))
        self.assertGreater(dc.items_needed(0.05, 0.20), dc.items_needed(0.05, 0.10))

    def test_ci_halfwidth_shrinks_with_items(self):
        self.assertGreater(dc.ci_halfwidth(0.1, 12), dc.ci_halfwidth(0.1, 48))
        self.assertAlmostEqual(
            dc.ci_halfwidth(0.1, 100, 0.90), 1.644854 * 0.1 / 10, places=6
        )


if __name__ == "__main__":
    unittest.main()
