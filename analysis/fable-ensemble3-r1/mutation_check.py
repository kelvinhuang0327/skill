#!/usr/bin/env python3
"""Mutation check for FABLE_ENSEMBLE3_R1: break each rule on purpose and require the tests to notice.

    python3 -B mutation_check.py      # about half a minute; exit 0 only if every mutant is KILLED

The unmutated copy must pass first, otherwise nothing is learned.  A mutant is KILLED only when
  * the mutated source still compiles and imports (a file that no longer loads proves nothing), and
  * at least one test named in its `expect` list FAILS an assertion.
A test that merely raises is CRASHED, an unexpected test failing is UNEXPECTED, a source or test
module that no longer loads is INVALID, and none of these counts as catching the defect.  Each
patch must match the source exactly once and change it, so a mutant cannot silently become a
no-op when the code moves.  The checks that guard the checker are in test_mutation_check.py.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

sys.dont_write_bytecode = True

KILLED, SURVIVED, CRASHED, UNEXPECTED, INVALID = "KILLED", "SURVIVED", "CRASHED", "UNEXPECTED", "INVALID"
PATCH_MISMATCH, NOOP = "PATCH_MISMATCH", "NOOP"

FILES = (
    "design_check.py",
    "ensemble_scoring.py",
    "interval_check.py",
    "test_design_check.py",
    "test_ensemble_scoring.py",
)
TEST_MODULES = ("test_design_check", "test_ensemble_scoring")


class BaselineRed(Exception):
    """The unmutated tests do not pass, so no mutant result can be trusted."""


@dataclass(frozen=True)
class Mutant:
    name: str
    target: str  # source file the patch applies to
    old: str  # must occur exactly once in the target
    new: str
    expect: Tuple[str, ...]  # test methods; at least one must FAIL an assertion


@dataclass(frozen=True)
class Result:
    name: str
    status: str
    detail: str = ""


@dataclass(frozen=True)
class TestRun:
    timed_out: bool
    load_error: bool  # a test module could not be imported
    failures: Dict[str, str]  # test method -> "FAIL" (assertion) or "ERROR" (exception)
    returncode: int


def _run_tests(work: Path, modules: Sequence[str], timeout: int) -> TestRun:
    # unittest labels a subject's runtime AssertionError as FAIL too. Inspect the
    # traceback before formatting strips unittest frames; require a test-owned
    # assertion call, not an exception raised inside the mutated implementation.
    probe = '''
import json, pathlib, sys, traceback, unittest
modules = sys.argv[1:]
test_files = {str(pathlib.Path(m.split('.')[0] + '.py').resolve()) for m in modules}
class BehavioralResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.behavior = {}
        self.load_error = False
    def addFailure(self, test, err):
        frames = [f for f, _ in traceback.walk_tb(err[2])]
        assertion = any(f.f_globals.get('__unittest') and
                        (f.f_code.co_name.startswith('assert') or f.f_code.co_name in ('fail', '_raiseFailure'))
                        for f in frames)
        outside = [f for f in frames if not f.f_globals.get('__unittest')]
        test_owned = bool(outside) and str(pathlib.Path(outside[-1].f_code.co_filename).resolve()) in test_files
        self.behavior[test.id().split('.')[-1]] = 'FAIL' if assertion and test_owned else 'ERROR'
        super().addFailure(test, err)
    def addError(self, test, err):
        self.behavior[test.id().split('.')[-1]] = 'ERROR'
        self.load_error |= isinstance(test, unittest.loader._FailedTest)
        super().addError(test, err)
suite = unittest.defaultTestLoader.loadTestsFromNames(modules)
result = unittest.TextTestRunner(verbosity=1, resultclass=BehavioralResult).run(suite)
print('MUTATION_RESULT:' + json.dumps({'failures':result.behavior, 'load_error':result.load_error}))
sys.exit(0 if result.wasSuccessful() else 1)
'''
    cmd = [sys.executable, "-B", "-c", probe, *modules]
    try:
        proc = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return TestRun(True, False, {}, -1)
    records = [line[len("MUTATION_RESULT:"):] for line in proc.stdout.splitlines() if line.startswith("MUTATION_RESULT:")]
    if len(records) != 1:
        return TestRun(False, True, {}, proc.returncode or 1)
    data = json.loads(records[0])
    return TestRun(False, data["load_error"], data["failures"], proc.returncode)


def _imports_cleanly(work: Path, target: Path) -> Tuple[bool, str]:
    snippet = (
        "import importlib.util, sys\n"
        "spec = importlib.util.spec_from_file_location('mutated_target', sys.argv[1])\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules['mutated_target'] = module\n"
        "spec.loader.exec_module(module)\n"
    )
    proc = subprocess.run([sys.executable, "-B", "-c", snippet, str(target)], cwd=work, capture_output=True, text=True)
    if proc.returncode == 0:
        return True, ""
    return False, (proc.stderr.strip().splitlines() or ["import failed"])[-1]


def _copy(src_dir: Path, files: Sequence[str], dest: Path) -> None:
    for name in files:
        shutil.copy2(src_dir / name, dest / name)


def check_baseline(src_dir: Path, files: Sequence[str], modules: Sequence[str], timeout: int = 120) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        _copy(src_dir, files, work)
        run = _run_tests(work, modules, timeout)
        if run.timed_out or run.returncode != 0 or run.failures:
            raise BaselineRed(f"unmutated tests are not green: {sorted(run.failures) or 'timeout or load error'}")


def evaluate_mutant(
    src_dir: Path, files: Sequence[str], modules: Sequence[str], mutant: Mutant, timeout: int = 120
) -> Result:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        _copy(src_dir, files, work)
        target = work / mutant.target
        text = target.read_text(encoding="utf-8")
        found = text.count(mutant.old)
        if found != 1:
            return Result(mutant.name, PATCH_MISMATCH, f"pattern occurs {found} times, needs exactly 1")
        if mutant.new == mutant.old:
            return Result(mutant.name, NOOP, "the patch does not change the source")
        mutated = text.replace(mutant.old, mutant.new)
        try:
            compile(mutated, str(target), "exec")
        except SyntaxError as exc:
            return Result(mutant.name, INVALID, f"does not compile: {exc.msg}")
        target.write_text(mutated, encoding="utf-8")
        loads, why = _imports_cleanly(work, target)
        if not loads:
            return Result(mutant.name, INVALID, f"does not import: {why}")

        run = _run_tests(work, modules, timeout)
        if run.timed_out:
            return Result(mutant.name, CRASHED, "test run timed out")
        if run.load_error:
            return Result(mutant.name, INVALID, "a test module no longer imports")
        caught = [t for t in mutant.expect if run.failures.get(t) == "FAIL"]
        if caught:
            return Result(mutant.name, KILLED, caught[0])
        raised = [t for t in mutant.expect if run.failures.get(t) == "ERROR"]
        if raised:
            return Result(mutant.name, CRASHED, f"{raised[0]} raised instead of failing an assertion")
        if run.failures:
            return Result(mutant.name, UNEXPECTED, f"only unexpected tests failed: {sorted(run.failures)[:3]}")
        return Result(mutant.name, SURVIVED, "no test failed")


def run_all(
    src_dir: Path, files: Sequence[str], modules: Sequence[str], mutants: Sequence[Mutant]
) -> List[Result]:
    check_baseline(src_dir, files, modules)
    return [evaluate_mutant(src_dir, files, modules, m) for m in mutants]


# --- the mutants ----------------------------------------------------------------
# Each one reintroduces a defect the Judge found, or removes a rule the spec relies on.

S = "ensemble_scoring.py"
D = "design_check.py"
MUTANTS: Tuple[Mutant, ...] = (
    # one policy, one quorum difference (R2 acceptance 1; Judge finding F4)
    Mutant(
        "single_gated bypasses the shared policy (the v0.2 defect)", S,
        "    return policy_outcome(item, [run], 1)\n",
        "    return run_outcome(item, run) if _is_voter(run) else _escalation(item)\n",
        ("test_the_wrappers_are_the_one_policy_at_quorum_one_and_two", "test_identical_copies_have_no_aggregation_effect",
         "test_judge_counterexample_three_identical_answers_with_verified_contradictions"),
    ),
    Mutant(
        "single run uses quorum 2", S,
        "    return policy_outcome(item, [run], 1)\n", "    return policy_outcome(item, [run], 2)\n",
        ("test_the_wrappers_are_the_one_policy_at_quorum_one_and_two", "test_identical_copies_have_no_aggregation_effect"),
    ),
    Mutant(
        "a run's own contradiction is ignored", S,
        "if any(disposition in r.contradicts for r in runs if r.status == OK):",
        "if any(disposition in r.contradicts for r in runs if r.status == OK and r.disposition != disposition):",
        ("test_judge_counterexample_three_identical_answers_with_verified_contradictions",),
    ),
    Mutant(
        "contradictions ignored entirely", S,
        "if any(disposition in r.contradicts for r in runs if r.status == OK):", "if False:",
        ("test_verified_contradiction_from_an_ineligible_run_blocks_acceptance",
         "test_judge_counterexample_three_identical_answers_with_verified_contradictions"),
    ),
    Mutant(
        "only eligible runs may contradict", S,
        "if any(disposition in r.contradicts for r in runs if r.status == OK):",
        "if any(disposition in r.contradicts for r in runs if _is_voter(r)):",
        ("test_verified_contradiction_from_an_ineligible_run_blocks_acceptance",),
    ),
    Mutant(
        "failed runs may contradict", S,
        "if any(disposition in r.contradicts for r in runs if r.status == OK):",
        "if any(disposition in r.contradicts for r in runs):",
        ("test_failed_runs_cannot_vote_or_contradict",),
    ),
    Mutant(
        "quorum guard removed", S,
        "if quorum < 1 or 2 * quorum <= len(runs):", "if False:",
        ("test_quorum_must_be_a_strict_majority",),
    ),
    Mutant(
        "one agreeing vote is enough", S,
        "if c >= quorum]", "if c >= 1]",
        ("test_one_eligible_voter_is_not_enough", "test_no_two_votes_agreeing_escalates"),
    ),
    Mutant(
        "unknown eligibility passes", S,
        "return run.status == OK and run.eligible is True  # unknown eligibility fails closed",
        "return run.status == OK and run.eligible is not False",
        ("test_unknown_gate_fails_closed",),
    ),
    Mutant(
        "unknown gate treated as pass", S,
        "    if any(value is None for value in values):\n        return None\n    return True", "    return True",
        ("test_unknown_gate_fails_closed",),
    ),
    # scoring table and validation
    Mutant(
        "ESCALATE always success", S,
        "return SUCCESS if ESCALATE in item.acceptable else AVOIDABLE", "return SUCCESS",
        ("test_escalate_is_success_only_when_the_oracle_lists_it",),
    ),
    Mutant(
        "ESCALATE allowed as unsafe", S,
        "        if ESCALATE in item.unsafe:\n            raise ValueError(f\"{item.item_id}: ESCALATE can never be unsafe\")\n", "",
        ("test_item_validation",),
    ),
    Mutant(
        "plain-majority ties not escalated", S,
        "ranked[0][1] == ranked[1][1]", "False",
        ("test_plain_majority_escalates_ties_and_accepts_a_lone_valid_vote",),
    ),
    Mutant(
        "unknown or unfinished statuses accepted", S,
        "if run.status not in AGENT_FAILURES | {INFRA_FAIL} | MISSING:", "if False:",
        ("test_out_of_enum_disposition_fails_loudly",),
    ),
    Mutant(
        "a contradiction outside the enum accepted", S,
        "    if item.kind == \"DECISION\" and not run.contradicts <= item.disposition_enum:\n"
        "        raise ValueError(f\"{item.item_id}: a contradiction names a disposition outside the enum\")\n", "",
        ("test_out_of_enum_disposition_fails_loudly",),
    ),
    # effects and the way they decompose (F4)
    Mutant(
        "crit_reduction measured against the ungated single run", S,
        'crit_reduction=m["crit_single_gated"] - m["crit_gated"],', 'crit_reduction=m["crit_single"] - m["crit_gated"],',
        ("test_gate_removes_critical_failures_the_ensemble_does_not_add_to", "test_effects_decompose_exactly_on_random_pools"),
    ),
    Mutant(
        "aggregation_effect measured against the ungated single run", S,
        'aggregation_effect=m["gated"] - m["single_gated"],', 'aggregation_effect=m["gated"] - m["single"],',
        ("test_the_gate_alone_can_explain_the_whole_gain", "test_effects_decompose_exactly_on_random_pools"),
    ),
    Mutant(
        "escalate_created never counted", S,
        'loss["escalate_created"] += 1', "pass",
        ("test_effects_decompose_exactly_on_random_pools",),
    ),
    Mutant(
        "disagreement always zero", S,
        '"disagreement": 1 - shapes[1] / t,', '"disagreement": 0.0,',
        ("test_disagreement_ignores_artifact_rows", "test_variance_without_a_harvestable_gain"),
    ),
    # stopping rule (F1)
    Mutant(
        "variance label taken from coverage_gap", S,
        'dis = intervals.get("disagreement")', 'dis = intervals.get("coverage_gap") or intervals.get("disagreement")',
        ("test_disagreement_not_coverage_decides_which_stop_label",),
    ),
    Mutant(
        "stop on quiet dispositions before looking for a gain", S,
        "    improved = agg.lo > 0 or crit.lo > 0\n    harmed",
        "    if dis.hi < delta_min:\n        return NO_VARIANCE, \"\"\n    improved = agg.lo > 0 or crit.lo > 0\n    harmed",
        ("test_gate_variance_without_disposition_disagreement_is_not_a_stop",),
    ),
    Mutant(
        "stop without ruling the harvest out", S,
        "ruled_out = agg.hi <= delta_min and crit.hi <= delta_min", "ruled_out = True",
        ("test_quiet_dispositions_do_not_stop_while_a_gain_is_not_ruled_out",),
    ),
    Mutant(
        "harm guard removed", S,
        "if improved and harmed:", "if False:",
        ("test_an_improvement_that_costs_safety_is_not_a_go",),
    ),
    Mutant(
        "no minimum number of families", S,
        "if dis.families < min_families:", "if False:",
        ("test_too_few_families_blocks_a_verdict_even_with_a_large_effect",
         "test_judge_counterexample_two_decision_families_and_four_artifact_families"),
    ),
    # one DECISION scope (R2 acceptance 2; Judge finding F3)
    Mutant(
        "evaluate_pool ignores the kind", S,
        "scoped = [item for item in items if item.kind == kind]", "scoped = list(items)",
        ("test_adding_or_removing_artifact_items_never_changes_a_decision_result",
         "test_judge_counterexample_two_decision_families_and_four_artifact_families",
         "test_missing_share_counts_decision_items_only", "test_disagreement_ignores_artifact_rows"),
    ),
    Mutant(
        "classify skips the one-pool check", S,
        "if len({(iv.scope, iv.families, iv.cohort) for iv in (dis, agg, crit)}) != 1:", "if False:",
        ("test_classify_requires_all_three_metrics_from_one_pool",),
    ),
    Mutant(
        "interval scope dropped", S,
        "            interval.families,\n            pool.item_ids,\n            cohort,\n        )", "            interval.families,\n            frozenset(),\n            cohort,\n        )",
        ("test_classify_requires_all_three_metrics_from_one_pool",),
    ),
    Mutant(
        "one-family interval reported as missing", S,
        "out[name] = Interval(sum(only) / len(only), -math.inf, math.inf, 1, pool.item_ids, cohort)", "out[name] = None",
        ("test_one_family_is_too_few_families_not_a_missing_metric",),
    ),
    Mutant(
        "duplicate item ids allowed", S,
        "    if len(set(ids)) != len(ids):\n        raise ValueError(\"item ids must be unique across kinds\")\n", "",
        ("test_ids_must_be_unique_and_kinds_known",),
    ),
    Mutant(
        "unresolved items leave the denominator", S,
        "return len(self.item_ids)", "return len(self.item_ids) - len(self.unresolved)",
        ("test_not_run_slots_are_excluded_and_thin_items_stay_counted_as_unresolved",
         "test_missing_share_counts_decision_items_only"),
    ),
    Mutant(
        "no widening for unresolved items", S,
        "pi = len(pool.unresolved) / pool.n_items if pool.n_items else 0.0", "pi = 0.0",
        ("test_unresolved_items_widen_the_interval_and_can_block_a_verdict",
         "test_missing_share_counts_decision_items_only"),
    ),
    # missing data (F2)
    Mutant(
        "items with any failed run are dropped (the v0.1 rule)", S,
        "if sum(1 for r in runs if r.status not in MISSING) < 3:",
        "if sum(1 for r in runs if r.status not in MISSING) < 3 or any(r.status != OK for r in runs):",
        ("test_agent_attributable_failures_stay_in_the_denominator",
         "test_infra_fail_after_the_single_retry_stays_in_the_denominator_as_a_failed_run"),
    ),
    Mutant(
        "INFRA_FAIL treated as missing instead of a failed run", S,
        "MISSING = frozenset({NOT_RUN})", "MISSING = frozenset({NOT_RUN, INFRA_FAIL})",
        ("test_infra_fail_after_the_single_retry_stays_in_the_denominator_as_a_failed_run",),
    ),
    Mutant(
        "plan_rounds uses float arithmetic", S,
        "affordable = int(budget // (per_run_cap * n_items))", "affordable = int(float(budget) / float(per_run_cap * n_items))",
        ("test_budget_preflight_reserves_whole_rounds",),
    ),
    # intervals (F3)
    Mutant(
        "item-level interval instead of the cluster-robust one", S,
        "    se = math.sqrt(g / (g - 1) * residual) / n\n    half = t_quantile(0.5 + level / 2, g - 1) * se",
        "    se = math.sqrt(sum((x - est) ** 2 for v in families.values() for x in v) / (n - 1)) / math.sqrt(n)\n"
        "    half = NormalDist().inv_cdf(0.5 + level / 2) * se",
        ("test_cluster_interval_is_the_t_interval_of_family_means_when_sizes_match",
         "test_item_level_bootstrap_undercovers_where_the_cluster_interval_does_not",
         "test_item_level_interval_understates_clustered_uncertainty"),
    ),
    Mutant(
        "normal quantile instead of t(G-1)", S,
        "half = t_quantile(0.5 + level / 2, g - 1) * se", "half = NormalDist().inv_cdf(0.5 + level / 2) * se",
        ("test_cluster_interval_is_the_t_interval_of_family_means_when_sizes_match",),
    ),
    # planning math (design_check.py)
    Mutant(
        "cohort loses item-to-result association", S,
        '"rows": {f: dict(zip(pool.row_ids[f], rows)) for f, rows in sorted(pool.rows.items())},',
        '"rows": {f: sorted(rows, key=lambda row: json.dumps(row, sort_keys=True)) for f, rows in sorted(pool.rows.items())},',
        ("test_item_result_permutations_are_different_cohorts",),
    ),
    Mutant(
        "classify ignores the result cohort", S,
        "(iv.scope, iv.families, iv.cohort)", "(iv.scope, iv.families)",
        ("test_same_item_ids_from_different_results_cannot_be_mixed",),
    ),
    Mutant(
        "a metric silently drops a resolved row", S,
        "if any(name not in row for rows in pool.rows.values() for row in rows):", "if False:",
        ("test_a_metric_cannot_silently_drop_resolved_items",),
    ),
    Mutant(
        "majority-of-three formula wrong", D,
        "return 3 * p * p - 2 * p ** 3", "return 3 * p * p - p ** 3",
        ("test_closed_forms_match_enumerating_three_runs",),
    ),
    Mutant(
        "pass@3 formula wrong", D,
        "return 1 - (1 - p) ** 3", "return 1 - (1 - p) ** 2",
        ("test_closed_forms_match_enumerating_three_runs",),
    ),
    Mutant(
        "triple estimator forgets the all-correct triples", D,
        "majority = (comb(k, 3) + comb(k, 2) * (n - k)) / triples", "majority = (comb(k, 2) * (n - k)) / triples",
        ("test_estimates_match_brute_force_over_triples", "test_per_item_estimates_are_unbiased_for_the_population_gain"),
    ),
    Mutant(
        "sample size ignores power", D,
        "return math.ceil(((z(1 - alpha) + z(power)) * sd / delta) ** 2)", "return math.ceil(((z(1 - alpha)) * sd / delta) ** 2)",
        ("test_items_needed",),
    ),
)


def main() -> int:
    src_dir = Path(__file__).resolve().parent
    try:
        results = run_all(src_dir, FILES, TEST_MODULES, MUTANTS)
    except BaselineRed as exc:
        print(f"BASELINE RED: {exc}")
        return 2
    width = max(len(r.name) for r in results)
    for r in results:
        print(f"{r.status:<14} {r.name:<{width}}  {r.detail}")
    killed = sum(r.status == KILLED for r in results)
    print(f"\n{killed}/{len(results)} killed")
    return 0 if killed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
