#!/usr/bin/env python3
"""Build and validate the formal exploration bank (DRAFT: independent oracle review pending).

    python3 build_formal_bank.py DESTINATION [--pack PATH]

Writes public/*.json (what a model sees), private/*.json (oracle labels), manifest.json and
ORACLE_REVIEW.md.  Every label carries a verbatim quote from the instruction pack the model
receives; the build refuses a quote that is not in the pack, so a reviewer can check each label
against the same text the model had.  Nothing here calls a model.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Dict, List

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from bank_spec import FAMILIES, GATE_FACTS, LINEAGE, archetype  # noqa: E402
import items_f01_f04  # noqa: E402
import items_f05_f08  # noqa: E402
import items_f09_f12  # noqa: E402

DEFAULT_PACK = Path(
    "/Users/kelvin/VibeCoding-WorkSpace/fable-ensemble3-r1-evidence/instruction-source/candidate_instructions.md"
)
AUTHOR_DATE = "2026-10-03"
MIN_ITEMS, MIN_FAMILIES, MIN_PER_FAMILY = 30, 6, 3
ESCALATE_SHARE = (0.35, 0.65)
CODE = re.compile(r"[A-Z][A-Z0-9_]{9,}")
PUBLIC_ID = re.compile(r"fb-f\d{2}-\d+")
# Plain words that happen to be upper-case in the pack, and a scenario descriptor: none bears an answer.
CODE_ALLOWED_IN_PUBLIC = {"FRESH_CONTEXT", "AUTHORIZED", "INDEPENDENT"}
OPTION_ORDER_SALT = "v3-1"  # bank v3; v2 hashed 'key|option' without a salt


def _load(name, path):
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


engine = _load("lab_decision_engine", HERE.parent / "fable-ensemble3-lab" / "decision_engine.py")
scoring = _load("ensemble_scoring", HERE.parent / "fable-ensemble3-r1" / "ensemble_scoring.py")


def all_specs() -> List[dict]:
    return items_f01_f04.ITEMS + items_f05_f08.ITEMS + items_f09_f12.ITEMS


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"


def norm(text: str) -> str:
    for src, dst in (("“", '"'), ("”", '"'), ("‘", "'"), ("’", "'")):
        text = text.replace(src, dst)
    return re.sub(r"\s+", " ", re.sub(r"[`*]", "", text)).strip()


def public_id(spec: dict) -> str:
    """The id a model sees: family and ordinal only.  The descriptive slug would name the answer."""
    return f"fb-{spec['family_key']}-{spec['ordinal']}"


def ordered_options(key: str, options: List[str]) -> List[str]:
    """Deterministic pseudo-random order (SHA-256 of 'salt|key|option').

    The salt was the first of v3-1, v3-2, ... whose order shows no ESCALATE-first association with
    ESCALATE-correct (Fisher p >= 0.5) and lets no fixed-position strategy beat its expectation by more
    than 3; it was chosen from label/position statistics only, never from model output (README, F5).
    """
    return sorted(options, key=lambda option: hashlib.sha256(f"{OPTION_ORDER_SALT}|{key}|{option}".encode()).hexdigest())


def public_item(spec: dict) -> dict:
    fam = spec["family_key"]
    pid = public_id(spec)
    facts = {fact: fact not in spec["gate_fail"] for fact in GATE_FACTS.values()}
    overlap = set(facts) & set(spec["facts"])
    if overlap:
        raise ValueError(f"{spec['item_id']}: facts reuse gate names {sorted(overlap)}")
    facts.update(spec["facts"])
    checks = {fact: {"field": fact, "op": "eq", "expected": True} for fact in GATE_FACTS.values()}
    rules = []
    for veto in spec["veto"]:
        checks[veto["check_id"]] = {"field": veto["field"], "op": "eq", "expected": veto["expected"]}
        rules.extend({"against": against, "check_id": veto["check_id"]} for against in veto["against"])
    return {
        "item_id": pid,
        "source_ref": f"authored/{fam}",
        "family": f"formal/{fam}",
        "task_class": spec["task_class"],
        "kind": "DECISION",
        "task": spec["task"],
        "disposition_enum": ordered_options(pid, spec["options"] + ["ESCALATE"]),
        "facts": facts,
        "checks": checks,
        "required_checks": {gate: [fact] for gate, fact in GATE_FACTS.items()},
        "contradiction_rules": rules,
        "exposure": [{"event": "AUTHOR", "date": AUTHOR_DATE}],
        "split": "EXPLORATION",
    }


def private_label(spec: dict, public_sha256: str) -> dict:
    return {
        "item_id": public_id(spec),
        "slug": spec["slug"],
        "acceptable": spec["acceptable"],
        "unsafe": spec["unsafe"],
        "label_source": "RULE_DERIVED",
        "rationale": spec["why"],
        "rule_quotes": spec["quotes"],
        "rule_support": spec["support"],
        "archetype": archetype(spec),
        "family_key": spec["family_key"],
        "family_description": FAMILIES[spec["family_key"]][1],
        "public_sha256": public_sha256,
    }


def problems(specs: List[dict], pack_text: str) -> List[str]:
    """Every defect found, not just the first."""
    found: List[str] = []
    pack_norm = norm(pack_text)
    pack_codes = set(CODE.findall(pack_text))
    ids = [s["item_id"] for s in specs]
    if len(set(ids)) != len(ids):
        found.append("duplicate item ids")
    public_ids = [public_id(s) for s in specs]
    if len(set(public_ids)) != len(public_ids):
        found.append("duplicate public ids")
    if len({s["task"] for s in specs}) != len(specs):
        found.append("duplicate task text")
    per_family: Dict[str, List[dict]] = {}
    for spec in specs:
        per_family.setdefault(spec["family_key"], []).append(spec)
        item_id = spec["item_id"]
        if not PUBLIC_ID.fullmatch(public_id(spec)):
            found.append(f"{item_id}: public id {public_id(spec)!r} is not opaque")
        options, acceptable, unsafe = spec["options"], spec["acceptable"], spec["unsafe"]
        enum = options + ["ESCALATE"]
        if len(set(options)) != len(options) or "ESCALATE" in options:
            found.append(f"{item_id}: options must be distinct and exclude ESCALATE")
        if not acceptable or not set(acceptable) <= set(enum):
            found.append(f"{item_id}: acceptable must be a non-empty subset of the enum")
        if not set(unsafe) <= set(options):
            found.append(f"{item_id}: unsafe must be a subset of the non-ESCALATE options")
        if spec["support"] not in ("EXPLICIT", "IMPLIED"):
            found.append(f"{item_id}: unknown support {spec['support']!r}")
        if not spec["quotes"]:
            found.append(f"{item_id}: no rule quote")
        for quote in spec["quotes"]:
            if norm(quote["quote"]) not in pack_norm:
                found.append(f"{item_id}: quote not in the instruction pack: {quote['quote'][:70]!r}")
        for veto in spec["veto"]:
            if veto["field"] not in spec["facts"]:
                found.append(f"{item_id}: veto field {veto['field']!r} is not a fact")
            if not set(veto["against"]) <= set(options):
                found.append(f"{item_id}: veto targets a disposition outside the options")
        for gate_fact in spec["gate_fail"]:
            if gate_fact not in GATE_FACTS.values():
                found.append(f"{item_id}: {gate_fact!r} is not a gate fact")
        try:
            public = public_item(spec)
            engine.validate_public(public)
            scoring.validate_item(
                scoring.Item(public["item_id"], public["family"], "DECISION", frozenset(enum), frozenset(acceptable), frozenset(unsafe))
            )
        except (ValueError, KeyError) as exc:
            found.append(f"{item_id}: {exc}")
            continue
        text = canonical(public)
        leaked = {c for c in pack_codes if c in text} - CODE_ALLOWED_IN_PUBLIC
        if leaked:
            found.append(f"{item_id}: public text carries pack rule codes {sorted(leaked)}")
        for quote in spec["quotes"]:
            if len(quote["quote"]) > 40 and norm(quote["quote"]) in norm(text):
                found.append(f"{item_id}: a rule quote appears in the public item")
    if len(specs) < MIN_ITEMS:
        found.append(f"{len(specs)} items, need at least {MIN_ITEMS}")
    if len(per_family) < MIN_FAMILIES:
        found.append(f"{len(per_family)} families, need at least {MIN_FAMILIES}")
    for fam, members in per_family.items():
        if len(members) < MIN_PER_FAMILY:
            found.append(f"{fam}: {len(members)} items, need at least {MIN_PER_FAMILY}")
    share = sum("ESCALATE" in s["acceptable"] for s in specs) / max(len(specs), 1)
    if not ESCALATE_SHARE[0] <= share <= ESCALATE_SHARE[1]:
        found.append(f"ESCALATE is acceptable for {share:.0%} of items, outside {ESCALATE_SHARE}")
    return found


def review_pack(specs: List[dict]) -> str:
    lines = [
        "# Oracle review pack (formal exploration bank, DRAFT)",
        "",
        "Status: authored by the Worker; **independent oracle review pending**. Nothing here is a model result.",
        "A label is only as good as its quote: every `quote` below was matched verbatim against the instruction pack",
        "the model receives. `support: EXPLICIT` means the quoted text states the outcome; `IMPLIED` means one",
        "inference step is needed and the reviewer should look at that step first.",
        "Ids are opaque on purpose: the model sees `fb-f01-1`; the name in parentheses exists only in this pack.",
        f"Option order is a fixed pseudo-random permutation per item (salt `{OPTION_ORDER_SALT}`). The association between",
        "position and answer was checked (Fisher p=0.754; always-first/middle/last = 14/18/17, expected 16.33), but no",
        "position-counterbalanced check with a real model has been run.",
        "",
        f"Items: {len(specs)}; families: {len({s['family_key'] for s in specs})}; "
        f"ESCALATE acceptable: {sum('ESCALATE' in s['acceptable'] for s in specs)}; "
        f"archetypes: " + ", ".join(f"{k}={sum(archetype(s) == k for s in specs)}" for k in "GVL"),
        "",
        "Archetypes: G = a gate fails so the checker forces ESCALATE; V = a registered contradiction rule decides it;",
        "L = the facts and the quoted rule decide it and nothing mechanical does.",
    ]
    current = None
    for spec in specs:
        if spec["family_key"] != current:
            current = spec["family_key"]
            lines += ["", f"## {current} - {FAMILIES[current][1]}"]
        lines += [
            "",
            f"### {public_id(spec)}  ({spec['slug']})  [{archetype(spec)}, {spec['support']}]",
            f"Scenario: {spec['task']}",
            f"Options (the order the model sees): {', '.join(ordered_options(public_id(spec), spec['options'] + ['ESCALATE']))}",
            f"ACCEPTABLE: {', '.join(spec['acceptable'])}   UNSAFE: {', '.join(spec['unsafe']) or '(none)'}",
            f"Why: {spec['why']}",
        ]
        lines += [f"- quote ({q['file']}): {q['quote']}" for q in spec["quotes"]]
    return "\n".join(lines) + "\n"


def build(destination: Path, pack: Path) -> dict:
    pack_bytes = pack.read_bytes()
    specs = all_specs()
    defects = problems(specs, pack_bytes.decode("utf-8"))
    if defects:
        raise ValueError("bank is invalid:\n  " + "\n  ".join(defects))
    public_dir, private_dir = destination / "public", destination / "private"
    public_dir.mkdir(parents=True, exist_ok=True)
    private_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "purpose": "FORMAL_DECISION_EXPLORATION",
        "status": "DRAFT_PENDING_INDEPENDENT_ORACLE_REVIEW",
        "oracle_review": "PENDING_INDEPENDENT",
        "statistical_claims_allowed": False,
        "label_source": "RULE_DERIVED",
        "instruction_pack_sha256": hashlib.sha256(pack_bytes).hexdigest(),
        "items": [],
    }
    for spec in specs:
        public = public_item(spec)
        public_text = canonical(public)
        label_text = canonical(private_label(spec, hashlib.sha256(public_text.encode()).hexdigest()))
        pid = public["item_id"]
        for path, contents in ((public_dir / f"{pid}.json", public_text), (private_dir / f"{pid}.json", label_text)):
            if path.exists() and path.read_text() != contents:
                raise ValueError(f"refuse to overwrite an existing fixture: {path}")
            path.write_text(contents)
        manifest["items"].append(
            {
                "item_id": pid,
                "family": public["family"],
                "kind": "DECISION",
                "split": "EXPLORATION",
                "source_ref": public["source_ref"],
                "lineage_ref": LINEAGE[spec["family_key"]],
                "used_for_outcome_selection": False,
                # absolute, because the readiness check resolves refs from its own working directory
                "public_ref": str((public_dir / f"{pid}.json").resolve()),
                "public_sha256": hashlib.sha256(public_text.encode()).hexdigest(),
                "oracle_ref": str((private_dir / f"{pid}.json").resolve()),
                "oracle_sha256": hashlib.sha256(label_text.encode()).hexdigest(),
            }
        )
    (destination / "manifest.json").write_text(canonical(manifest))
    (destination / "ORACLE_REVIEW.md").write_text(review_pack(specs))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("destination")
    parser.add_argument("--pack", default=str(DEFAULT_PACK))
    args = parser.parse_args()
    manifest = build(Path(args.destination), Path(args.pack))
    specs = all_specs()
    print(
        f"built {len(manifest['items'])} items in {len({s['family_key'] for s in specs})} families; "
        f"ESCALATE acceptable {sum('ESCALATE' in s['acceptable'] for s in specs)}; "
        f"archetypes " + " ".join(f"{k}={sum(archetype(s) == k for s in specs)}" for k in "GVL")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
