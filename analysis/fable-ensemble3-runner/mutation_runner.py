#!/usr/bin/env python3
"""Falsifiability check for the runner package: break each guard in a copy and require its test to fail by assertion.

    python3 -B mutation_runner.py [--check-patterns] [--only SUBSTRING] [--workers N]

Reuses the verified R2 runner (fable-ensemble3-r1/mutation_check.py): a mutant counts only if the mutated source compiles
and imports and the named test fails an assertion inside the test module; a crash, a timeout or a failure raised by the
subject itself does not count. The profile tests read sealed Git objects through a git-dir pointer in a plain source copy;
no clone, checkout or index mutation is performed. The original tree is never touched.
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
ANALYSIS = ROOT.parent
REPO = ANALYSIS.parent
SEALED = "32d5c9991c71d5b4025c015c395c9d41226a6d45"
MODULES = ["test_execution_profile", "test_exploration_runner", "test_probe_2b"]

_spec = importlib.util.spec_from_file_location("verified_mutation_runner", ANALYSIS / "fable-ensemble3-r1" / "mutation_check.py")
mc = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = mc
_spec.loader.exec_module(mc)
sys.path.insert(0, str(ROOT))
from mutation_cases import CASES  # noqa: E402


def build_base(tmp: Path) -> Path:
    base = tmp / "base"
    # A plain copy plus read-only object lookup: no clone / checkout / index writes.
    git_dir = subprocess.run(["git", "--no-optional-locks", "-C", str(REPO), "rev-parse", "--absolute-git-dir"],
                             check=True, capture_output=True, text=True).stdout.strip()
    base.mkdir()
    (base / ".git").write_text(f"gitdir: {git_dir}\n")
    for name in ("fable-ensemble3-r1", "fable-ensemble3-lab", "fable-ensemble3-bank"):
        shutil.copytree(ANALYSIS / name, base / "analysis" / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(ROOT, base / "analysis" / "fable-ensemble3-runner", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return base


def known_tests() -> set:
    """Every `module.Class.method` the test modules define, so a case that names a test that does not exist cannot pass quietly."""
    found = set()
    for module in MODULES:
        for node in ast.parse((ROOT / f"{module}.py").read_text()).body:
            if isinstance(node, ast.ClassDef):
                found.update(f"{module}.{node.name}.{f.name}" for f in node.body if isinstance(f, ast.FunctionDef) and f.name.startswith("test_"))
    return found


def patches(old, new):
    return old if isinstance(old, list) else [(old, new)]


def apply(text: str, old, new):
    for before, after in patches(old, new):
        if text.count(before) != 1:
            return None, f"pattern occurs {text.count(before)} times: {before[:60]!r}"
        text = text.replace(before, after)
    return text, ""


def run_case(base: Path, tmp: Path, index: int, case) -> dict:
    name, target, old, new, check = case
    work_root = tmp / f"m{index}"
    shutil.copytree(base, work_root, symlinks=True)
    work = work_root / "analysis" / "fable-ensemble3-runner"
    destination = work / target
    text = destination.read_text()
    mutated, problem = apply(text, old, new)
    if mutated is None:
        return {"name": name, "status": "PATCH_MISMATCH", "detail": problem}
    if mutated == text:
        return {"name": name, "status": "NOOP", "detail": ""}
    try:
        compile(mutated, str(destination), "exec")
    except SyntaxError as exc:
        return {"name": name, "status": "INVALID", "detail": f"does not compile: {exc.msg}"}
    destination.write_text(mutated)
    imports, reason = mc._imports_cleanly(work, destination)
    run = mc._run_tests(work, [check], 180) if imports else None
    expected = check.split(".")[-1]
    killed = bool(imports and not run.load_error and run.failures.get(expected) == "FAIL")
    shutil.rmtree(work_root, ignore_errors=True)
    return {"name": name, "status": "KILLED" if killed else "NOT_KILLED", "expected_test": check, "imports": imports,
            "detail": reason if not imports else "", "observed_failures": None if run is None else run.failures}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-patterns", action="store_true", help="only verify that every pattern matches the source exactly once")
    parser.add_argument("--only", default="")
    parser.add_argument("--workers", type=int, choices=(1, 2), default=2)
    parser.add_argument("--results-json", help="retain exact subprocess output and assertion classifications here")
    args = parser.parse_args()
    cases = [case for case in CASES if args.only in case[0]]
    if args.check_patterns:
        bad, tests = 0, known_tests()
        for name, target, old, new, check in cases:
            _, problem = apply((ROOT / target).read_text(), old, new)
            if check not in tests:
                problem = (problem + "; " if problem else "") + f"no such test: {check}"
            if problem:
                bad += 1
                print(f"PATCH_MISMATCH {name}: {problem}")
        print(f"{len(cases) - bad}/{len(cases)} patterns match exactly once and name an existing test")
        return 0 if not bad else 1
    captures = []
    run = subprocess.run
    def capture(*a, **kw):
        started = time.time()
        result = run(*a, **kw)
        if kw.get("capture_output"):
            captures.append({"argv": a[0], "cwd": str(kw.get("cwd", "")), "start_epoch": started, "end_epoch": time.time(),
                             "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
        return result
    if args.results_json:
        subprocess.run = capture
    with tempfile.TemporaryDirectory(prefix="ensemble3-runner-mutants-") as tmp:
        tmp = Path(tmp)
        base = build_base(tmp)
        baseline = mc._run_tests(base / "analysis" / "fable-ensemble3-runner", MODULES, 900)
        if args.results_json:
            Path(args.results_json).write_text(json.dumps({"baseline": baseline.__dict__, "captures": captures}, indent=2) + "\n")
        if baseline.returncode or baseline.failures or baseline.load_error:
            raise SystemExit(f"baseline red: {baseline.failures}")
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            results = list(pool.map(lambda pair: run_case(base, tmp, pair[0], pair[1]), enumerate(cases)))
    killed = sum(r["status"] == "KILLED" for r in results)
    if args.results_json:
        subprocess.run = run
        Path(args.results_json).write_text(json.dumps({"results": results, "captures": captures}, indent=2) + "\n")
    for r in results:
        print(f"{r['status']:<14} {r['name']}" + ("" if r["status"] == "KILLED" else f"   {r.get('detail') or r.get('observed_failures')}"))
    print(f"\n{killed}/{len(results)} killed")
    return 0 if killed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
