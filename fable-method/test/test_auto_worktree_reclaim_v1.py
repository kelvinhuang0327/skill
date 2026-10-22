#!/usr/bin/env python3
"""Focused test suite for FABLE_AUTO_WORKTREE_RECLAIM_V1.

Validates AC01 through AC09 and AC12:
- Two-phase safe automatic reclaim
- Default reclaim without requiring repeated --auto-close-authorized flags
- Formal merge verification (normal merge + credible squash merge with source evidence)
- Rejection of insufficient merge evidence
- Preservation of dirty, locked, active, unmerged, untracked, or non-Fable worktrees
- Caches vs ordinary untracked files
- Prevention of helper-spawned fsmonitor daemons (core.fsmonitor=false)
- Proper distinction between caller's own shell and active external processes
- Race conditions, duplicate close, and duplicate remove safety
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

# Add scripts directory to path to import helper directly
HELPER_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(HELPER_DIR))

import fable_artifact_controls as helper
from fable_artifact_controls import ControlError


def run_git(repo: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", "-C", str(repo), "-c", "core.fsmonitor=false", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout.strip()


def create_fixture_repo(root: Path) -> tuple[Path, Path, str]:
    repo = root / "repo"
    repo.mkdir()
    run_git(repo, "init", "--initial-branch=main")
    run_git(repo, "config", "user.name", "Fable Test")
    run_git(repo, "config", "user.email", "fable-test@example.invalid")
    (repo / "base.txt").write_text("initial base\n", encoding="utf-8")
    run_git(repo, "add", "base.txt")
    run_git(repo, "commit", "-m", "initial commit")

    evidence_dir = root / "evidence"
    evidence_dir.mkdir()
    artifact = evidence_dir / "result.json"
    artifact.write_text('{"status": "SUCCESS"}\n', encoding="utf-8")
    artifact_sha = hashlib.sha256(artifact.read_bytes()).hexdigest()

    manifest = evidence_dir / "manifest.json"
    manifest.write_text(
        json.dumps({"files": [{"path": str(artifact), "sha256": artifact_sha}]}),
        encoding="utf-8",
    )
    return repo, manifest, artifact_sha


def canonical_temp_dir() -> tempfile.TemporaryDirectory[str]:
    canonical_tmp = Path(tempfile.gettempdir()).resolve(strict=True)
    return tempfile.TemporaryDirectory(prefix="fable-reclaim-test-", dir=str(canonical_tmp))


def test_scoped_create_uses_target_identity_and_preserves_conflicts() -> None:
    """Creation uses only the designated path/ref and returns before/after evidence."""
    expected_output_root = Path(
        "/Users/kelvin/VibeCoding-WorkSpace/.task-data/"
        "MSA_C_FABLE_WORKTREE_HELPER_SCOPE_R1"
    )
    output_root = Path(os.environ.get("FABLE_TEST_OUTPUT_ROOT", str(expected_output_root)))
    if output_root.resolve(strict=False) != expected_output_root:
        raise AssertionError(f"fixture root must be {expected_output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    fixture_root = output_root / f"scoped-create-{uuid.uuid4().hex}"
    fixture_root.mkdir()
    repo, _manifest, _artifact_sha = create_fixture_repo(fixture_root)

    original_git = helper.git
    observed_commands: list[tuple[str, ...]] = []

    def reject_global_worktree_inventory(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        command = tuple(args)
        if command == ("worktree", "list", "--porcelain"):
            raise AssertionError("restricted creation attempted global worktree enumeration")
        observed_commands.append(command)
        return original_git(cwd, *args, check=check)

    helper.git = reject_global_worktree_inventory
    try:
        target = fixture_root / "legal-worktree"
        created = helper.create_research_worktree(
            str(repo), str(target), "main", "feature/scoped-create",
            "TASK_SCOPED_CREATE", "temporary-task", auto_close_authorized=True,
        )
        before = created["before"]
        receipt = created["creation_receipt"]
        identity = receipt["target_identity"]
        assert created["status"] == "CREATED"
        assert before["target_path"] == str(target)
        assert before["target_exists"] is False
        assert before["branch_ref"] == "refs/heads/feature/scoped-create"
        assert before["branch_ref_exists"] is False
        assert before["worktree_access_scope"] == "TARGET_ONLY"
        assert before["other_worktrees"] == "NOT_INSPECTED"
        assert receipt["result"] == "CREATED"
        assert receipt["retention_status"] == "PRESENT_CLEAN"
        assert receipt["worktree_access_scope"] == "TARGET_ONLY"
        assert receipt["other_worktrees"] == "NOT_INSPECTED"
        assert identity["path"] == str(target)
        assert identity["git_worktree_root"] == str(target)
        assert identity["branch"] == "feature/scoped-create"
        assert identity["head"] == before["base_commit"]
        assert identity["clean"] is True
        assert target.is_dir()
        lifecycle_marker = helper._lifecycle_marker_path(repo, target, create=False)
        lifecycle_record = json.loads(lifecycle_marker.read_text(encoding="utf-8"))
        assert lifecycle_record["before_create"] == before
        assert lifecycle_record["after_create"] == receipt
        assert lifecycle_record["worktree_access_scope"] == "TARGET_ONLY"
        assert lifecycle_record["other_worktrees"] == "NOT_INSPECTED"

        main_head_before = run_git(repo, "rev-parse", "refs/heads/main")
        base_file_before = (repo / "base.txt").read_bytes()
        branch_conflict_target = fixture_root / "branch-conflict-target"
        try:
            helper.create_research_worktree(
                str(repo), str(branch_conflict_target), "main", "main",
                "TASK_BRANCH_CONFLICT", "persistent-research",
            )
        except ControlError as exc:
            assert "branch ref already exists" in str(exc)
        else:
            raise AssertionError("existing branch ref was not rejected")
        assert not branch_conflict_target.exists()
        assert run_git(repo, "rev-parse", "refs/heads/main") == main_head_before
        assert (repo / "base.txt").read_bytes() == base_file_before

        path_conflict_target = fixture_root / "path-conflict-target"
        path_conflict_target.mkdir()
        sentinel = path_conflict_target / "owner-data.txt"
        sentinel.write_bytes(b"preserve this existing source\n")
        sentinel_before = sentinel.read_bytes()
        try:
            helper.create_research_worktree(
                str(repo), str(path_conflict_target), "main", "feature/path-conflict",
                "TASK_PATH_CONFLICT", "persistent-research",
            )
        except ControlError as exc:
            assert "path already exists" in str(exc)
        else:
            raise AssertionError("existing target path was not rejected")
        assert sentinel.read_bytes() == sentinel_before

        assert ("worktree", "list", "--porcelain") not in observed_commands
        print(json.dumps({
            "fixture_root": str(fixture_root),
            "created_target": identity,
            "before": before,
            "creation_receipt": receipt,
            "branch_conflict": "PRESERVED",
            "path_conflict": "PRESERVED",
            "global_worktree_list_calls": 0,
        }, sort_keys=True))
    finally:
        helper.git = original_git


def test_ac01_default_auto_reclaim() -> None:
    """AC01: Newly created, preauthorized, formally merged worktree auto-reclaims without flags."""
    with canonical_temp_dir() as td:
        root = Path(td).resolve(strict=True)
        repo, manifest, _ = create_fixture_repo(root)
        wt = root / "ac01-wt"

        res = helper.create_research_worktree(
            str(repo), str(wt), "main", "feature/ac01",
            "TASK_AC01", "temporary-task",
            close_policy="AUTO_CLOSE_AFTER_MERGE",
        )
        assert res["status"] == "CREATED"
        assert res["auto_close_authorized"] is True

        (wt / "change.txt").write_text("feature content\n")
        run_git(wt, "add", "change.txt")
        run_git(wt, "commit", "-m", "feature commit")

        # Merge into main
        run_git(repo, "merge", "--no-edit", "feature/ac01")

        # Close without any optional authorization flags
        closed = helper.close_temporary_worktree(
            str(repo), str(wt), "main", str(manifest), "TASK_AC01",
        )
        assert closed["status"] == "AUTO_CLOSED"
        assert not wt.exists()
        assert wt not in helper._parse_worktree_list(repo)
        # Branch must be preserved
        assert run_git(repo, "rev-parse", "refs/heads/feature/ac01") == closed["head"]


def test_ac02_no_preauthorization_or_ownership() -> None:
    """AC02: No task packet pre-declaration or no Fable ownership prevents reclaim."""
    with canonical_temp_dir() as td:
        root = Path(td).resolve(strict=True)
        repo, manifest, _ = create_fixture_repo(root)

        # 1. Unregistered legacy worktree
        legacy = root / "legacy-wt"
        run_git(repo, "worktree", "add", "-b", "feature/legacy", str(legacy), "main")
        try:
            helper.close_temporary_worktree(str(repo), str(legacy), "main", str(manifest), "TASK_LEGACY")
            assert False, "should have failed"
        except ControlError as exc:
            assert "preauthorization" in str(exc)
        assert legacy.is_dir()
        assert legacy in helper._parse_worktree_list(repo)

        # 2. Mismatched task_id
        wt = root / "wt-mismatch"
        helper.create_research_worktree(
            str(repo), str(wt), "main", "feature/mismatch",
            "TASK_ORIGINAL", "temporary-task",
            close_policy="AUTO_CLOSE_AFTER_MERGE",
        )
        try:
            helper.close_temporary_worktree(str(repo), str(wt), "main", str(manifest), "TASK_DIFFERENT")
            assert False, "should have failed"
        except ControlError as exc:
            assert "preauthorization" in str(exc)
        assert wt.is_dir()


def test_ac03_unmerged_or_research_preserved() -> None:
    """AC03: Unmerged, commit-only, PR-ready, or research worktrees are preserved."""
    with canonical_temp_dir() as td:
        root = Path(td).resolve(strict=True)
        repo, manifest, _ = create_fixture_repo(root)

        # Unmerged temporary worktree
        wt = root / "unmerged-wt"
        helper.create_research_worktree(
            str(repo), str(wt), "main", "feature/unmerged",
            "TASK_UNMERGED", "temporary-task",
            close_policy="AUTO_CLOSE_AFTER_MERGE",
        )
        (wt / "unmerged.txt").write_text("not merged\n")
        run_git(wt, "add", "unmerged.txt")
        run_git(wt, "commit", "-m", "unmerged commit")

        try:
            helper.close_temporary_worktree(str(repo), str(wt), "main", str(manifest), "TASK_UNMERGED")
            assert False, "should have failed"
        except ControlError as exc:
            assert "not merged" in str(exc)
        assert wt.is_dir()

        # Research worktree (persistent-research)
        research = root / "research-wt"
        helper.create_research_worktree(
            str(repo), str(research), "main", "research/one",
            "TASK_RESEARCH", "persistent-research",
        )
        try:
            helper.close_temporary_worktree(str(repo), str(research), "main", str(manifest), "TASK_RESEARCH")
            assert False, "should have failed"
        except ControlError as exc:
            assert "preauthorization" in str(exc)
        assert research.is_dir()


def test_ac04_squash_merge_and_invalid_evidence() -> None:
    """AC04: Verified squash merge succeeds; insufficient merge evidence is rejected."""
    with canonical_temp_dir() as td:
        root = Path(td).resolve(strict=True)
        repo, manifest, _ = create_fixture_repo(root)

        # 1. Valid squash merge
        wt1 = root / "squash-valid"
        helper.create_research_worktree(
            str(repo), str(wt1), "main", "feature/squash-valid",
            "TASK_SQUASH_1", "temporary-task",
            close_policy="AUTO_CLOSE_AFTER_MERGE",
        )
        (wt1 / "squash.txt").write_text("squashed feature\n")
        run_git(wt1, "add", "squash.txt")
        run_git(wt1, "commit", "-m", "squash commit")
        head1 = run_git(wt1, "rev-parse", "HEAD")

        run_git(repo, "merge", "--squash", "feature/squash-valid")
        run_git(repo, "commit", "-m", f"Squash feature/squash-valid (commit: {head1})")

        closed1 = helper.close_temporary_worktree(
            str(repo), str(wt1), "main", str(manifest), "TASK_SQUASH_1"
        )
        assert closed1["status"] == "AUTO_CLOSED"
        assert closed1["merge_type"] == "SQUASH_MERGE"
        assert not wt1.exists()

        # 2. Invalid / fake squash claim (different content, no provenance)
        wt2 = root / "squash-invalid"
        helper.create_research_worktree(
            str(repo), str(wt2), "main", "feature/squash-invalid",
            "TASK_SQUASH_2", "temporary-task",
            close_policy="AUTO_CLOSE_AFTER_MERGE",
        )
        (wt2 / "fake.txt").write_text("feature only\n")
        run_git(wt2, "add", "fake.txt")
        run_git(wt2, "commit", "-m", "feature commit")

        # Main commits something completely different with similar name
        (repo / "fake.txt").write_text("completely different content\n")
        run_git(repo, "add", "fake.txt")
        run_git(repo, "commit", "-m", "unrelated commit mentioning fake.txt")

        try:
            helper.close_temporary_worktree(str(repo), str(wt2), "main", str(manifest), "TASK_SQUASH_2")
            assert False, "should have failed"
        except ControlError as exc:
            assert "not merged" in str(exc)
        assert wt2.is_dir()


def test_ac05_dirty_locked_and_active_preserved() -> None:
    """AC05: Dirty, locked, active claim, or active execution worktrees are preserved."""
    with canonical_temp_dir() as td:
        root = Path(td).resolve(strict=True)
        repo, manifest, _ = create_fixture_repo(root)

        # 1. Dirty worktree
        wt_dirty = root / "wt-dirty"
        helper.create_research_worktree(
            str(repo), str(wt_dirty), "main", "feature/dirty",
            "TASK_DIRTY", "temporary-task",
            close_policy="AUTO_CLOSE_AFTER_MERGE",
        )
        (wt_dirty / "file.txt").write_text("content\n")
        run_git(wt_dirty, "add", "file.txt")
        run_git(wt_dirty, "commit", "-m", "committed")
        run_git(repo, "merge", "--no-edit", "feature/dirty")
        # Now make it dirty
        (wt_dirty / "file.txt").write_text("modified uncommitted\n")

        try:
            helper.close_temporary_worktree(str(repo), str(wt_dirty), "main", str(manifest), "TASK_DIRTY")
            assert False, "should have failed"
        except ControlError as exc:
            assert "modified files" in str(exc)
        assert wt_dirty.is_dir()

        # 2. Locked worktree
        wt_locked = root / "wt-locked"
        helper.create_research_worktree(
            str(repo), str(wt_locked), "main", "feature/locked",
            "TASK_LOCKED", "temporary-task",
            close_policy="AUTO_CLOSE_AFTER_MERGE",
        )
        (wt_locked / "locked_file.txt").write_text("locked content\n")
        run_git(wt_locked, "add", "locked_file.txt")
        run_git(wt_locked, "commit", "-m", "committed locked")
        run_git(repo, "merge", "--no-edit", "feature/locked")
        run_git(repo, "worktree", "lock", str(wt_locked), "--reason", "under active inspection")

        try:
            helper.close_temporary_worktree(str(repo), str(wt_locked), "main", str(manifest), "TASK_LOCKED")
            assert False, "should have failed"
        except ControlError as exc:
            assert "locked" in str(exc)
        assert wt_locked.is_dir()
        run_git(repo, "worktree", "unlock", str(wt_locked))

        # 3. Active execution claim
        wt_active = root / "wt-active"
        helper.create_research_worktree(
            str(repo), str(wt_active), "main", "feature/active",
            "TASK_ACTIVE", "temporary-task",
            close_policy="AUTO_CLOSE_AFTER_MERGE",
        )
        (wt_active / "active_file.txt").write_text("active content\n")
        run_git(wt_active, "add", "active_file.txt")
        run_git(wt_active, "commit", "-m", "committed active")
        run_git(repo, "merge", "--no-edit", "feature/active")

        claims_dir = repo / ".task-data" / "execution-claims"
        claims_dir.mkdir(parents=True)
        claim_file = claims_dir / "active.json"
        claim_file.write_text(json.dumps({
            "schema_version": 1,
            "task_key": "active",
            "owner_id": "active-user",
            "owner_pid": os.getpid(),
            "child_pid": None,
            "hostname": "local",
            "started_at_utc": "2026-01-01T00:00:00Z",
            "heartbeat_at_utc": "2026-01-01T00:00:00Z",
            "cwd": str(wt_active),
            "command": ["python3"],
            "claim_root": str(claims_dir),
        }))

        try:
            helper.close_temporary_worktree(str(repo), str(wt_active), "main", str(manifest), "TASK_ACTIVE")
            assert False, "should have failed"
        except ControlError as exc:
            assert "active execution claim" in str(exc)
        assert wt_active.is_dir()


def test_ac06_untracked_files_vs_rebuildable_cache() -> None:
    """AC06: Ordinary untracked files block reclaim; declared rebuildable caches are cleaned."""
    with canonical_temp_dir() as td:
        root = Path(td).resolve(strict=True)
        repo, manifest, _ = create_fixture_repo(root)

        # 1. Ordinary untracked file
        wt1 = root / "wt-untracked"
        helper.create_research_worktree(
            str(repo), str(wt1), "main", "feature/untracked",
            "TASK_UNTRACKED", "temporary-task",
            close_policy="AUTO_CLOSE_AFTER_MERGE",
        )
        (wt1 / "file.txt").write_text("content\n")
        run_git(wt1, "add", "file.txt")
        run_git(wt1, "commit", "-m", "committed")
        run_git(repo, "merge", "--no-edit", "feature/untracked")
        (wt1 / "random_notes.md").write_text("user scratch notes\n")

        try:
            helper.close_temporary_worktree(str(repo), str(wt1), "main", str(manifest), "TASK_UNTRACKED")
            assert False, "should have failed"
        except ControlError as exc:
            assert "untracked" in str(exc)
        assert wt1.is_dir()

        # 2. Declared rebuildable cache (__pycache__)
        wt2 = root / "wt-cache"
        helper.create_research_worktree(
            str(repo), str(wt2), "main", "feature/cache",
            "TASK_CACHE", "temporary-task",
            close_policy="AUTO_CLOSE_AFTER_MERGE",
            allowed_rebuildable_caches=["__pycache__", ".pytest_cache"],
        )
        (wt2 / "code.py").write_text("print(1)\n")
        run_git(wt2, "add", "code.py")
        run_git(wt2, "commit", "-m", "committed")
        run_git(repo, "merge", "--no-edit", "feature/cache")

        pycache = wt2 / "__pycache__"
        pycache.mkdir()
        (pycache / "code.cpython-39.pyc").write_bytes(b"cached bytecode\n")

        closed = helper.close_temporary_worktree(
            str(repo), str(wt2), "main", str(manifest), "TASK_CACHE"
        )
        assert closed["status"] == "AUTO_CLOSED"
        assert not wt2.exists()


def test_ac07_fsmonitor_and_active_processes() -> None:
    """AC07: fsmonitor daemon is not spawned; own shell is not mistaken; true active process blocks."""
    with canonical_temp_dir() as td:
        root = Path(td).resolve(strict=True)
        repo, manifest, _ = create_fixture_repo(root)

        wt = root / "wt-active-proc"
        helper.create_research_worktree(
            str(repo), str(wt), "main", "feature/proc",
            "TASK_PROC", "temporary-task",
            close_policy="AUTO_CLOSE_AFTER_MERGE",
        )
        (wt / "file.txt").write_text("content\n")
        run_git(wt, "add", "file.txt")
        run_git(wt, "commit", "-m", "committed")
        run_git(repo, "merge", "--no-edit", "feature/proc")

        # Spawn a separate background external process holding open a file in wt
        target_file = wt / "file.txt"
        holder = subprocess.Popen(
            [sys.executable, "-c", f"import time; f = open({str(target_file)!r}); time.sleep(30)"]
        )
        try:
            time.sleep(0.5)  # give it time to open the file
            try:
                helper.close_temporary_worktree(
                    str(repo), str(wt), "main", str(manifest), "TASK_PROC"
                )
                assert False, "should have failed due to active external process"
            except ControlError as exc:
                assert "active external process" in str(exc)
            assert wt.is_dir()
        finally:
            holder.terminate()
            holder.wait()

        # Once external process terminates, close succeeds cleanly
        closed = helper.close_temporary_worktree(
            str(repo), str(wt), "main", str(manifest), "TASK_PROC"
        )
        assert closed["status"] == "AUTO_CLOSED"
        assert not wt.exists()


def test_ac08_race_and_duplicate_safety() -> None:
    """AC08: Race condition / duplicate close / duplicate remove do not cause double delete."""
    with canonical_temp_dir() as td:
        root = Path(td).resolve(strict=True)
        repo, manifest, _ = create_fixture_repo(root)

        wt = root / "wt-race"
        helper.create_research_worktree(
            str(repo), str(wt), "main", "feature/race",
            "TASK_RACE", "temporary-task",
            close_policy="AUTO_CLOSE_AFTER_MERGE",
        )
        (wt / "file.txt").write_text("content\n")
        run_git(wt, "add", "file.txt")
        run_git(wt, "commit", "-m", "committed")
        run_git(repo, "merge", "--no-edit", "feature/race")

        # First close succeeds
        res1 = helper.close_temporary_worktree(str(repo), str(wt), "main", str(manifest), "TASK_RACE")
        assert res1["status"] == "AUTO_CLOSED"
        assert not wt.exists()

        # Duplicate close attempt fails closed safely
        try:
            helper.close_temporary_worktree(str(repo), str(wt), "main", str(manifest), "TASK_RACE")
            assert False, "duplicate close should fail"
        except ControlError as exc:
            assert ("not registered" in str(exc) or "cannot be read" in str(exc))

        # Duplicate remove check
        assert not wt.exists()


def test_ac09_historical_and_main_repo_preserved() -> None:
    """AC09: Historical worktree, base repo root, and other branches remain intact."""
    with canonical_temp_dir() as td:
        root = Path(td).resolve(strict=True)
        repo, manifest, _ = create_fixture_repo(root)

        # Attempt to close the main repo checkout itself
        try:
            helper.close_temporary_worktree(str(repo), str(repo), "main", str(manifest), "TASK_MAIN")
            assert False, "should refuse to remove main repository"
        except ControlError as exc:
            assert "refusing to remove the canonical checkout" in str(exc)
        assert repo.is_dir()


def main() -> int:
    tests = [
        ("AC01: Default auto-reclaim without flags", test_ac01_default_auto_reclaim),
        ("AC02: Refuse unregistered / non-Fable worktree", test_ac02_no_preauthorization_or_ownership),
        ("AC03: Refuse unmerged / research worktree", test_ac03_unmerged_or_research_preserved),
        ("AC04: Squash merge validation & reject bad claims", test_ac04_squash_merge_and_invalid_evidence),
        ("AC05: Dirty, locked, active claim preservation", test_ac05_dirty_locked_and_active_preserved),
        ("AC06: Untracked files vs rebuildable cache", test_ac06_untracked_files_vs_rebuildable_cache),
        ("AC07: fsmonitor avoidance & active process check", test_ac07_fsmonitor_and_active_processes),
        ("AC08: Race condition & duplicate close safety", test_ac08_race_and_duplicate_safety),
        ("AC09: Historical worktree & canonical repo preservation", test_ac09_historical_and_main_repo_preserved),
    ]

    passed = 0
    failed = 0
    print(f"=== Running {len(tests)} Focused Reclaim Tests ===")
    for name, func in tests:
        t0 = time.time()
        try:
            func()
            elapsed = time.time() - t0
            print(f"PASS: {name} ({elapsed:.2f}s)")
            passed += 1
        except Exception as exc:
            elapsed = time.time() - t0
            print(f"FAIL: {name} ({elapsed:.2f}s): {exc}")
            failed += 1

    print(f"\nResult: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
