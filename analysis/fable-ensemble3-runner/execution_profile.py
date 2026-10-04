#!/usr/bin/env python3
"""Execution profile and startup identity check for the ENSEMBLE3 R1 exploration (stdlib only; never calls a model).

    python3 execution_profile.py build --protocol P --receipt R --bank-manifest M --pack K --pack-manifest KM \\
        --source-commit C --sealed-commit S --repo REPO --out PROFILE.json [--status FROZEN]
    python3 execution_profile.py check PROFILE.json [--mode SIMULATION|LIVE] [--deep]

The profile pins every identity a run depends on: model and effort, the instruction pack, the bank and its review
receipt, the scoring policy, the cost basis, the adapter constants and the sealed source files.  `check` re-derives each
one from the files on disk and blocks, before any generation, on a missing field, a hash that no longer matches, a stale
or conflict-marked instruction pack, or two artifacts that disagree about which pack or bank they bind.  The bank is only
ever rebuilt with an explicit --pack, never through the builder's default path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Mapping, Optional

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ANALYSIS = HERE.parent
LAB, R1, BANK = ANALYSIS / "fable-ensemble3-lab", ANALYSIS / "fable-ensemble3-r1", ANALYSIS / "fable-ensemble3-bank"
if str(LAB) not in sys.path:
    sys.path.insert(0, str(LAB))

import decision_engine as engine  # noqa: E402
import exploration_readiness as readiness  # noqa: E402
import responses_adapter as api  # noqa: E402

for _module in (engine, readiness, api):  # a worktree must never silently import a sibling checkout's copy
    assert Path(_module.__file__).resolve().parent == LAB, _module.__file__

SCHEMA_VERSION = 1
ARTIFACT = "FABLE_ENSEMBLE3_R1_EXECUTION_PROFILE"
MODES = ("SIMULATION", "LIVE")
CONFLICT_MARKER = re.compile(r"^(<{7}( |$)|>{7}( |$)|={7}$)", re.M)
SEALED_DIRS = ("analysis/fable-ensemble3-r1", "analysis/fable-ensemble3-lab", "analysis/fable-ensemble3-bank")
# Packs that were superseded.  A run bound to one of them must never start (F7: v1 carried committed conflict markers).
KNOWN_STALE_PACKS = {
    "0d1a4b07d31d56753f897eb6e9d7d0a8ddb94287e48d23ec63deeaf8f3903d38": "v1 pack with committed merge-conflict markers (F7)",
}
# The runner package's own code.  A profile or probe registration names these hashes, so the Owner's approval, which is bound to
# that artifact, covers the code that will run and not only the data it will read.
RUNNER_FILES = ("_lab.py", "execution_profile.py", "exploration_runner.py", "probe_2b.py", "simulated_transport.py")
REQUIRED = (
    "artifact", "schema_version", "status", "model", "effort", "provider", "instruction", "bank", "policy", "cost",
    "protocol", "lab", "sealed_source", "runner_sources",
)


def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_pinned(record) -> bytes:
    """Hash and consume exactly one read. Never validate one version and use a second."""
    content = Path(record["ref"]).read_bytes()
    if hashlib.sha256(content).hexdigest() != record["sha256"]:
        raise ValueError("pinned content changed")
    return content


def pin(path) -> Dict[str, str]:
    path = Path(path).resolve()
    return {"ref": str(path), "sha256": sha256_file(path)}


def runner_sources() -> Dict[str, str]:
    """sha256 of each file of this package that can touch a request, as it is on disk now."""
    return {name: sha256_file(HERE / name) for name in RUNNER_FILES}


def runner_drift(recorded) -> List[str]:
    """Why the code on disk is not the code a profile or registration was made for; empty when it is."""
    if not isinstance(recorded, dict) or set(recorded) != set(RUNNER_FILES):
        return ["RUNNER_SOURCE_PIN_MALFORMED"]
    reasons = []
    for name in RUNNER_FILES:
        path = HERE / name
        if not path.is_file():
            reasons.append(f"RUNNER_SOURCE_MISSING:{name}")
        elif sha256_file(path) != recorded[name]:
            reasons.append(f"RUNNER_SOURCE_DRIFT:{name}")
    return reasons


def _git(repo, *args) -> str:
    result = subprocess.run(["git", "--no-optional-locks", "-C", str(repo), *args], capture_output=True, text=True)
    if result.returncode:
        raise ValueError(f"git {' '.join(args)} failed: {result.stderr.strip()[:200]}")
    return result.stdout.strip()


def sealed_files(repo, sealed_commit) -> Dict[str, str]:
    """sha256 of every file the sealed commit holds under the three analysis directories, checked against the worktree."""
    blobs = {}
    for line in _git(repo, "ls-tree", "-r", sealed_commit, "--", *SEALED_DIRS).splitlines():
        meta, name = line.split("\t", 1)
        blobs[name] = meta.split()[2]
    if not blobs:
        raise ValueError("the sealed commit holds no analysis/fable-ensemble3-* files")
    names = sorted(blobs)
    hashed = subprocess.run(["git", "--no-optional-locks", "-C", str(repo), "hash-object", "--stdin-paths"], capture_output=True, text=True,
                            input="".join(f"{Path(repo) / name}\n" for name in names))
    if hashed.returncode or len(hashed.stdout.split()) != len(names):
        raise ValueError(f"git hash-object failed: {hashed.stderr.strip()[:200]}")
    files = {}
    for name, worktree_blob in zip(names, hashed.stdout.split()):
        if worktree_blob != blobs[name]:
            raise ValueError(f"{name} differs from the sealed commit")
        files[name] = sha256_file(Path(repo) / name)
    return files


def build_profile(*, protocol, receipt, bank_manifest, pack, pack_manifest, source_commit, sealed_commit, repo,
                  status="DRAFT_NOT_FROZEN") -> dict:
    """Assemble a profile from the artifacts on disk; refuses to build one that `check` would not accept in SIMULATION."""
    protocol_path, receipt_path, manifest_path = Path(protocol), Path(receipt), Path(bank_manifest)
    proto = engine.read_json(protocol_path)
    bank = engine.read_json(manifest_path)
    profile = {
        "artifact": ARTIFACT,
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "model": proto["model"],
        "effort": proto["effort"],
        "provider": {
            "adapter": pin(LAB / "responses_adapter.py"),
            "endpoint": "https://api.openai.com/v1/",
            "input_token_limit": api.INPUT_LIMIT,
            "output_token_limit": api.OUTPUT_LIMIT,
            "service_tier": "default",
            "store": False,
        },
        "instruction": {
            **pin(pack),
            "manifest": pin(pack_manifest),
            "source_commit": source_commit,
            "superseded_sha256": sorted(KNOWN_STALE_PACKS),
        },
        "bank": {"manifest": pin(manifest_path), "receipt": pin(receipt_path), "purpose": bank["purpose"], "items": len(bank["items"])},
        "policy": {
            **pin(R1 / "ensemble_scoring.py"),
            "name": "POLICY_K3V1",
            "quorum": 2,
            "target_runs": proto["target_runs"],
            "schedule_seed": proto["schedule_seed"],
            "min_rounds": 3,
        },
        "cost": {
            "basis": proto["cost_basis"],
            "attempt_reservation_usd": str(proto["attempt_reservation_usd"]),
            "max_attempts_per_slot": 2,
            "total_reservation_usd": proto.get("total_reservation_usd"),
        },
        "protocol": pin(protocol_path),
        "lab": {name: pin(LAB / name) for name in ("decision_engine.py", "budget_ledger.py", "offline_replay.py", "exploration_readiness.py")},
        "sealed_source": {"commit": sealed_commit, "files": sealed_files(repo, sealed_commit)},
        "runner_sources": runner_sources(),
    }
    interface = proto.get("interface")
    if interface:
        profile["interface"] = {"ref": interface["ref"], "sha256": interface["sha256"]}
    return profile


def _decimal(value) -> Optional[Decimal]:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and number > 0 else None


def check_profile(profile_path, *, mode: str = "SIMULATION", env: Optional[Mapping[str, str]] = None, deep: bool = False) -> dict:
    """Every reason a run bound to this profile must not start; an empty list means PASS.  Never calls a model."""
    reasons: List[str] = []
    env = os.environ if env is None else env

    def done() -> dict:
        return {"status": "BLOCKED" if reasons else "PASS", "mode": mode, "reasons": sorted(set(reasons)), "generation_calls": 0}

    if mode not in MODES:
        reasons.append("MODE_INVALID")
        return done()
    try:
        profile = engine.read_json(profile_path)
    except (OSError, ValueError):
        reasons.append("PROFILE_MISSING_OR_INVALID")
        return done()
    if not isinstance(profile, dict):
        reasons.append("PROFILE_MISSING_OR_INVALID")
        return done()
    reasons.extend(f"PROFILE_FIELD_MISSING:{key}" for key in REQUIRED if key not in profile)
    if profile.get("artifact") != ARTIFACT or profile.get("schema_version") != SCHEMA_VERSION:
        reasons.append("PROFILE_SCHEMA_INVALID")
    if reasons:
        return done()  # the rest reads these fields
    try:
        _check_body(profile, mode, env, deep, reasons)
    except (KeyError, TypeError, AttributeError, ValueError):
        reasons.append("PROFILE_SHAPE_INVALID")
    return done()


def _check_body(profile: dict, mode: str, env: Mapping[str, str], deep: bool, reasons: List[str]) -> None:
    pinned_bytes = {}
    def pinned(name: str, record) -> Optional[Path]:
        """The pinned artifact's path if it still exists with its recorded sha256."""
        try:
            path, digest = Path(record["ref"]), record["sha256"]
        except (KeyError, TypeError):
            reasons.append(f"PIN_MALFORMED:{name}")
            return None
        if not path.is_file():
            reasons.append(f"ARTIFACT_MISSING:{name}")
            return None
        try:
            content = path.read_bytes()
        except OSError:
            reasons.append(f"ARTIFACT_UNREADABLE:{name}")
            return None
        if hashlib.sha256(content).hexdigest() != digest:
            reasons.append(f"ARTIFACT_HASH_MISMATCH:{name}")
            return None
        pinned_bytes[path] = content
        return path

    def load(path: Optional[Path]) -> dict:
        if path is None:
            return {}
        try:
            value = json.loads(pinned_bytes[path], object_pairs_hook=engine.no_duplicates)
        except (OSError, ValueError):
            reasons.append(f"ARTIFACT_UNREADABLE:{path.name}")
            return {}
        return value if isinstance(value, dict) else {}

    instruction, bank_pin, policy, cost, provider = profile["instruction"], profile["bank"], profile["policy"], profile["cost"], profile["provider"]
    protocol_path = pinned("protocol", profile["protocol"])
    protocol = load(protocol_path)
    pack_path = pinned("instruction", instruction)
    pack_manifest = load(pinned("instruction_manifest", instruction["manifest"]))
    manifest_path = pinned("bank_manifest", bank_pin["manifest"])
    bank = load(manifest_path)
    receipt_path = pinned("bank_receipt", bank_pin["receipt"])
    receipt = load(receipt_path)
    pinned("policy", policy)
    pinned("adapter", provider["adapter"])
    for name, record in profile["lab"].items():
        pinned(f"lab:{name}", record)

    # --- one instruction pack, everywhere ------------------------------------------------------------------------------
    pack_sha = instruction["sha256"]
    stale = dict(KNOWN_STALE_PACKS)
    stale.update({digest: "listed in the profile as superseded" for digest in instruction.get("superseded_sha256", [])})
    bound = {
        "bank manifest": bank.get("instruction_pack_sha256"),
        "receipt": receipt.get("instruction_sha256"),
        "protocol": (protocol.get("instruction_surface") or {}).get("sha256"),
        "pack manifest": pack_manifest.get("instruction_sha256"),
    }
    if pack_sha in stale or any(value in stale for value in bound.values()):
        reasons.append("STALE_INSTRUCTION_PACK")
    for label, reason in (("bank manifest", "PACK_BANK_MISMATCH"), ("receipt", "PACK_RECEIPT_MISMATCH"),
                          ("protocol", "PACK_PROTOCOL_MISMATCH"), ("pack manifest", "PACK_MANIFEST_MISMATCH")):
        if bound[label] != pack_sha:
            reasons.append(reason)
    if pack_manifest.get("source_commit") != instruction["source_commit"]:
        reasons.append("PACK_SOURCE_COMMIT_MISMATCH")
    if pack_path is not None and CONFLICT_MARKER.search(pinned_bytes[pack_path].decode("utf-8", errors="replace")):
        reasons.append("INSTRUCTION_HAS_CONFLICT_MARKERS")

    # --- one bank, one receipt -----------------------------------------------------------------------------------------
    bank_sha = bank_pin["manifest"]["sha256"]
    if receipt.get("verdict") != "VERIFIED" or receipt.get("scope") != "FORMAL_BANK_AND_ORACLE":
        reasons.append("RECEIPT_NOT_VERIFIED")
    if receipt.get("bank_manifest_sha256") != bank_sha:
        reasons.append("BANK_RECEIPT_MISMATCH")
    if (protocol.get("bank") or {}).get("sha256") != bank_sha:
        reasons.append("BANK_PROTOCOL_MISMATCH")
    if (protocol.get("bank_review") or {}).get("sha256") != bank_pin["receipt"]["sha256"]:
        reasons.append("RECEIPT_PROTOCOL_MISMATCH")
    if bank.get("purpose") != bank_pin["purpose"] or len(bank.get("items", [])) != bank_pin["items"]:
        reasons.append("BANK_SHAPE_MISMATCH")

    # --- policy, model, provider constants -----------------------------------------------------------------------------
    if policy["sha256"] != readiness.FROZEN_POLICY_SHA256:
        reasons.append("POLICY_NOT_THE_REVIEWED_R2")
    if policy["quorum"] != 2 or policy["target_runs"] != protocol.get("target_runs") or policy["schedule_seed"] != protocol.get("schedule_seed"):
        reasons.append("POLICY_PARAMETER_MISMATCH")
    if protocol.get("target_runs") != 5:
        reasons.append("REGISTERED_R1_RUN_COUNT_MISMATCH")
    if profile["model"] != api.MODEL or profile["model"] != protocol.get("model"):
        reasons.append("MODEL_MISMATCH")
    if profile["effort"] != api.EFFORT or profile["effort"] != protocol.get("effort"):
        reasons.append("EFFORT_MISMATCH")
    if (provider["input_token_limit"], provider["output_token_limit"], provider["service_tier"], provider["store"]) != (
            api.INPUT_LIMIT, api.OUTPUT_LIMIT, "default", False):
        reasons.append("PROVIDER_CONSTANT_MISMATCH")

    # --- cost basis ----------------------------------------------------------------------------------------------------
    attempt = _decimal(cost["attempt_reservation_usd"])
    if attempt is None or attempt != api.ATTEMPT_PRICE_UPPER or str(protocol.get("attempt_reservation_usd")) != cost["attempt_reservation_usd"]:
        reasons.append("COST_RESERVATION_MISMATCH")
    if cost["basis"] != protocol.get("cost_basis") or cost["max_attempts_per_slot"] != 2:
        reasons.append("COST_BASIS_MISMATCH")
    total, required = _decimal(cost.get("total_reservation_usd")), None
    if attempt is not None:
        required = attempt * cost["max_attempts_per_slot"] * bank_pin["items"] * policy["target_runs"]
    if mode == "LIVE":
        if total is None:
            reasons.append("TOTAL_COST_CAP_UNFROZEN")
        elif required is not None and total < required:
            reasons.append("REGISTERED_WHOLE_ROUNDS_NOT_RESERVED")
        if protocol.get("total_reservation_usd") != cost.get("total_reservation_usd"):
            reasons.append("COST_TOTAL_PROTOCOL_MISMATCH")

    # --- the sealed source this run relies on --------------------------------------------------------------------------
    sealed = profile["sealed_source"]["files"]
    repo = ANALYSIS.parent
    for relative, digest in sealed.items():
        path = repo / relative
        if not path.is_file():
            reasons.append(f"SEALED_SOURCE_MISSING:{relative}")
        elif sha256_file(path) != digest:
            reasons.append(f"SEALED_SOURCE_DRIFT:{relative}")
    present = {p.relative_to(repo).as_posix() for d in SEALED_DIRS for p in (repo / d).rglob("*")
               if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}
    reasons.extend(f"SEALED_SOURCE_EXTRA:{extra}" for extra in sorted(present - set(sealed)))
    for name, record in profile["lab"].items():
        if sealed.get(f"analysis/fable-ensemble3-lab/{name}") != record["sha256"]:
            reasons.append(f"LAB_NOT_SEALED:{name}")
    reasons.extend(runner_drift(profile["runner_sources"]))  # the code that will run is the code this profile was made for

    # --- a live run needs more ---------------------------------------------------------------------------------------
    if mode == "LIVE":
        if profile["status"] != "FROZEN":
            reasons.append("LIVE_PROFILE_NOT_FROZEN")
        if not env.get("OPENAI_API_KEY"):  # presence only; the value is never read into any result
            reasons.append("LIVE_KEY_MISSING")
        interface = load(pinned("interface", profile["interface"])) if profile.get("interface") else {}
        if (interface.get("status"), interface.get("mode"), interface.get("instruction_sha256")) != ("BENCHMARK_READY", "LIVE", pack_sha):
            reasons.append("LIVE_INTERFACE_NOT_READY")
        if protocol_path is not None:
            try:
                reasons.extend(f"PREFLIGHT:{reason}" for reason in readiness.check_file(protocol_path)["reasons"])
            except (OSError, ValueError, KeyError, TypeError):
                reasons.append("PREFLIGHT_UNREADABLE")

    # --- the bank can be rebuilt from the sealed builder and exactly this pack -----------------------------------------
    if deep and manifest_path is not None and pack_path is not None:
        with tempfile.TemporaryDirectory(prefix="ensemble3-profile-deep-") as tmp:
            destination = Path(tmp) / "bank"
            built = subprocess.run([sys.executable, "-B", str(BANK / "build_formal_bank.py"), str(destination), "--pack", str(pack_path)],
                                   capture_output=True, text=True, cwd=str(BANK), timeout=300)
            if built.returncode:
                reasons.append("BANK_REBUILD_FAILED")
            else:
                rebuilt = (destination / "manifest.json").read_text().replace(str(destination.resolve()), "<DEST>")
                expected = pinned_bytes[manifest_path].decode("utf-8").replace(str(manifest_path.parent.resolve()), "<DEST>")
                if rebuilt != expected:
                    reasons.append("BANK_REBUILD_MISMATCH")


def _write_exclusive(profile: dict, out: Path) -> dict:
    """Publish the profile only if it passes `check` in SIMULATION mode; never overwrite."""
    out = Path(out)
    handle, temporary = tempfile.mkstemp(prefix=out.name + ".", suffix=".tmp", dir=str(out.parent))
    try:
        with os.fdopen(handle, "w") as stream:
            stream.write(json.dumps(profile, indent=2, sort_keys=True) + "\n")
        result = check_profile(temporary, mode="SIMULATION")
        if not result["reasons"]:
            os.link(temporary, out)  # fails if `out` exists
        return result
    finally:
        Path(temporary).unlink()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    for name in ("protocol", "receipt", "bank-manifest", "pack", "pack-manifest", "source-commit", "sealed-commit", "repo", "out"):
        build.add_argument(f"--{name}", required=True)
    build.add_argument("--status", default="DRAFT_NOT_FROZEN", choices=("DRAFT_NOT_FROZEN", "FROZEN"))
    check = commands.add_parser("check")
    check.add_argument("profile")
    check.add_argument("--mode", default="SIMULATION", choices=MODES)
    check.add_argument("--deep", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "build":
        profile = build_profile(protocol=args.protocol, receipt=args.receipt, bank_manifest=args.bank_manifest, pack=args.pack,
                                pack_manifest=args.pack_manifest, source_commit=args.source_commit, sealed_commit=args.sealed_commit,
                                repo=args.repo, status=args.status)
        result = _write_exclusive(profile, Path(args.out))
    else:
        result = check_profile(args.profile, mode=args.mode, deep=args.deep)
    print(json.dumps(result, indent=2))
    return 0 if not result["reasons"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
