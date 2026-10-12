#!/usr/bin/env python3
"""Focused tests for skill-lifecycle/scripts/validate_evidence.py.

Synthetic and self-contained: every fixture below is hand-authored in a
temporary directory. Nothing is read from the analysis pilots, from TSD, or
from any real candidate. Each case is built, run through the CLI as a
subprocess, and asserted on exit code plus rendered output.

Covers the boundaries the three lifecycle experiments established, in
particular the ones a stronger-sounding implementation would erase:
structural reference existence is not scope legitimacy; artifact identity is
not evaluation quality; a declared negative set is not falsification
adequacy; and no input reaches PROMOTION_AUTHORIZED: true.

Run: python3 skill-lifecycle/test/test_validate_evidence.py
"""

import json
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "validate_evidence.py"

RESULTS = []
ALL_OUTPUT = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print("%-62s %s%s" % (name, "PASS" if ok else "FAIL",
                          "  " + detail if detail and not ok else ""))


# ---------------------------------------------------------------------------
# Synthetic source specimen: writes its own `# Ln ` identifiers, which is what
# makes it mechanically addressable. Real sources often do not.
# ---------------------------------------------------------------------------

SOURCE_ADDRESSABLE = "\n".join([
    "# synthetic rule source (test fixture)",
    "# L1 task identity must be present and must equal the authorization scope",
    "# L2 risk tier must be one of LOW MEDIUM HIGH",
    "# L8 pending authorization must not be rendered as granted",
    "# L10 allowed_files must be non-empty when policies.merge is set",
    "# L12 scratch files must be removed before exit",
    "",
])

SOURCE_UNSTRUCTURED = "\n".join([
    "This source is prose. It states its rules in running sentences and",
    "exposes no per-rule identifier of any kind, so no clause identity can",
    "be derived from it mechanically.",
    "",
])

PASS_ARTIFACT_EXECUTED = "\n".join([
    "$ python3 impl.py fixtures/",
    "6 cases, 6 correct, 0 failed",
    "RESULT: PASS",
    "",
])

# Same claim, typed by hand, larger and more detailed than the real one. The
# closure experiment built this pair deliberately: both hash exactly as cited
# and a SHA-256 cannot distinguish them.
PASS_ARTIFACT_SELF_AUTHORED = "\n".join([
    "Evaluation summary (authored by the candidate author).",
    "All six selected rules were reviewed and behave as intended.",
    "Positive cases: 3. Negative cases: 2. Near-miss cases: 1.",
    "Determinism: stable across reruns.",
    "RESULT: PASS",
    "",
])

FALSIFICATION_ARTIFACT = "\n".join([
    "$ python3 impl.py --mutate L1:drop-id-nonemptiness fixtures/",
    "mutant killed by N3_empty_id_and_empty_scope",
    "RESULT: MUTANT_KILLED",
    "",
])


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return sha256_text(text)


CANDIDATE_ID = "cand-l1-l12-checker-a1b2c3"


def base_manifest(work, source_text=SOURCE_ADDRESSABLE, addressability="MACHINE_ADDRESSABLE"):
    """A manifest that is integrity-complete: every check A-G satisfied."""
    source_sha = write(work / "source" / "rules.txt", source_text)
    exec_sha = write(work / "evidence" / "run.txt", PASS_ARTIFACT_EXECUTED)
    neg_sha = write(work / "evidence" / "mutation.txt", FALSIFICATION_ARTIFACT)
    return {
        "manifest_version": "1",
        "candidate": {
            "candidate_id": CANDIDATE_ID,
            "target_skill": "synthetic-rule-checker",
            "candidate_kind": "CODE_BACKED_DETERMINISTIC",
            "frozen_revision": "candidate_v1",
            "source_identity": {
                "path": "source/rules.txt",
                "sha256": source_sha,
                "addressability": addressability,
                "identifier_prefix": "L",
            },
        },
        "source_references": [
            {"reference_id": "REF1", "cited_identifier": "L1", "purpose": "IN_SCOPE"},
            {"reference_id": "REF2", "cited_identifier": "L10", "purpose": "IN_SCOPE"},
        ],
        "rule_trigger_domains": {
            "L1": ["task.id.is_empty", "authorization.scope.is_empty"],
            "L10": ["allowed_files.is_empty", "policies.merge.is_set"],
        },
        "evidence_plan": {
            "outcome_vocabulary": ["PASS", "FAIL", "NOT_APPLICABLE"],
            "fixtures": [
                {"fixture_id": "P1", "rule_id": "L1", "kind": "POSITIVE",
                 "condition": {"task.id.is_empty": False, "authorization.scope.is_empty": False},
                 "expected_outcome": "PASS"},
                {"fixture_id": "N1", "rule_id": "L1", "kind": "NEGATIVE",
                 "condition": {"task.id.is_empty": True, "authorization.scope.is_empty": True},
                 "expected_outcome": "FAIL"},
                {"fixture_id": "P2", "rule_id": "L10", "kind": "POSITIVE",
                 "condition": {"allowed_files.is_empty": False, "policies.merge.is_set": True},
                 "expected_outcome": "PASS"},
                {"fixture_id": "NA1", "rule_id": "L10", "kind": "NEAR_MISS",
                 "condition": {"allowed_files.is_empty": False, "policies.merge.is_set": False},
                 "expected_outcome": "NOT_APPLICABLE"},
            ],
        },
        "claims": {"negative_falsification_evidence": True},
        "evaluations": [
            {"evaluation_id": "EVAL_POSITIVE", "candidate_id": CANDIDATE_ID,
             "kind": "POSITIVE",
             "artifact": {"path": "evidence/run.txt", "sha256": exec_sha,
                          "execution": "EXECUTED"}},
            {"evaluation_id": "EVAL_FALSIFICATION", "candidate_id": CANDIDATE_ID,
             "kind": "NEGATIVE_FALSIFICATION",
             "artifact": {"path": "evidence/mutation.txt", "sha256": neg_sha,
                          "execution": "EXECUTED"}},
        ],
        "unresolved_semantics": [
            {"item_id": "SEM1", "question": "does L8 cover the WAITING_OWNER clause",
             "status": "OPEN"},
        ],
    }


def run_cli(work, manifest, extra_args=()):
    path = work / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(path)] + list(extra_args),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )
    out = proc.stdout.decode("utf-8")
    ALL_OUTPUT.append(out)
    return proc.returncode, out


def run_cli_preserving_key_order(work, manifest):
    """
    Like run_cli but serialises without sort_keys, so authored key order
    survives to the validator. Needed to test that condition identity is
    independent of key order rather than of the harness.
    """
    work.mkdir(parents=True, exist_ok=True)
    path = work / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )
    out = proc.stdout.decode("utf-8")
    ALL_OUTPUT.append(out)
    return proc.returncode, out


def run_raw(work, raw_text):
    path = work / "manifest.json"
    path.write_text(raw_text, encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )
    out = proc.stdout.decode("utf-8")
    ALL_OUTPUT.append(out)
    return proc.returncode, out


def failed_checks(out):
    return [l.strip()[len("FAIL "):] for l in out.split("\n") if l.strip().startswith("FAIL ")]


def review_items(out):
    return [l.strip()[len("REVIEW "):] for l in out.split("\n") if l.strip().startswith("REVIEW ")]


def ok_items(out):
    return [l.strip()[len("OK "):] for l in out.split("\n") if l.strip().startswith("OK ")]


def has_prefix(items, token):
    return any(i == token or i.startswith(token + ":") for i in items)


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------

def case_01_valid(tmp):
    work = tmp / "c01"
    code, out = run_cli(work, base_manifest(work))
    check("01 valid integrity-complete manifest -> exit 0",
          code == 0 and "STATUS: INTEGRITY_COMPLETE" in out, "exit=%d" % code)
    check("01 evidence integrity VERIFIED",
          "EVIDENCE_INTEGRITY: VERIFIED" in out)
    # Every cited identifier resolving says nothing about whether the cited
    # set covers the source extent. A fully valid manifest must still carry
    # that review requirement rather than read as proven complete.
    check("01 addressable source still requires coverage review",
          "SOURCE_COVERAGE_REQUIRES_REVIEW:CITED_SET_VS_SOURCE_EXTENT"
          in review_items(out), str(review_items(out)))


def case_02_candidate_identity_changed(tmp):
    work = tmp / "c02"
    m = base_manifest(work)
    # The frozen candidate identity is rewritten after the evaluations were
    # recorded; the evaluations still bind to the original identity.
    m["candidate"]["candidate_id"] = CANDIDATE_ID + "-v2-rewritten"
    code, out = run_cli(work, m)
    fails = failed_checks(out)
    check("02 candidate identity changed after evaluation -> exit 1",
          code == 1, "exit=%d" % code)
    check("02 names the mismatch check",
          any(f.startswith("A4_EVALUATION_CANDIDATE_IDENTITY_MISMATCH") for f in fails),
          str(fails))


def case_03_source_hash_mismatch(tmp):
    work = tmp / "c03"
    m = base_manifest(work)
    m["candidate"]["source_identity"]["sha256"] = "0" * 64
    code, out = run_cli(work, m)
    fails = failed_checks(out)
    check("03 source hash mismatch -> exit 1", code == 1, "exit=%d" % code)
    check("03 names B2_SOURCE_SHA_MISMATCH",
          any(f.startswith("B2_SOURCE_SHA_MISMATCH") for f in fails), str(fails))
    check("03 integrity NOT_ESTABLISHED",
          "EVIDENCE_INTEGRITY: NOT_ESTABLISHED" in out)


def case_04_invented_reference(tmp):
    work = tmp / "c04"
    m = base_manifest(work)
    m["source_references"].append(
        {"reference_id": "REF_INVENTED", "cited_identifier": "L99",
         "purpose": "SCOPE_EXCLUSION"})
    code, out = run_cli(work, m)
    fails = failed_checks(out)
    check("04 invented machine-addressable reference -> exit 1", code == 1, "exit=%d" % code)
    check("04 names E2_CITED_IDENTIFIER_NOT_FOUND_IN_SOURCE",
          any(f.startswith("E2_CITED_IDENTIFIER_NOT_FOUND_IN_SOURCE") for f in fails),
          str(fails))


def case_05_real_but_irrelevant(tmp):
    """L12 (a real cleanup rule) cited to exclude a decision about L8."""
    work = tmp / "c05"
    m = base_manifest(work)
    m["source_references"].append(
        {"reference_id": "REF_DODGE", "cited_identifier": "L12",
         "purpose": "SCOPE_EXCLUSION",
         "claim": "L8 pending-authorization semantics are out of scope"})
    code, out = run_cli(work, m)
    oks, reviews = ok_items(out), review_items(out)
    check("05 real-but-irrelevant reference: structure verifies",
          any(o.startswith("REFERENCE_EXISTS:REF_DODGE:L12") for o in oks), str(oks))
    check("05 manual semantic-review warning emitted",
          any(r.startswith("SEMANTIC_SCOPE_JUSTIFICATION_REQUIRES_REVIEW") for r in reviews),
          str(reviews))
    check("05 never asserts EXCLUSION_LEGITIMATE",
          "EXCLUSION_LEGITIMATE" not in out)
    check("05 structural pass does not become a promotion claim",
          "PROMOTION_AUTHORIZED: false" in out and code == 0, "exit=%d" % code)


def case_06_missing_artifact(tmp):
    work = tmp / "c06"
    m = base_manifest(work)
    (work / "evidence" / "run.txt").unlink()
    code, out = run_cli(work, m)
    fails = failed_checks(out)
    check("06 missing evaluation artifact -> exit 1", code == 1, "exit=%d" % code)
    check("06 names C2_EVALUATION_ARTIFACT_MISSING",
          any(f.startswith("C2_EVALUATION_ARTIFACT_MISSING") for f in fails), str(fails))


def case_07_artifact_hash_mismatch(tmp):
    work = tmp / "c07"
    m = base_manifest(work)
    m["evaluations"][0]["artifact"]["sha256"] = "1" * 64
    code, out = run_cli(work, m)
    fails = failed_checks(out)
    check("07 evaluation hash mismatch -> exit 1", code == 1, "exit=%d" % code)
    check("07 names C3_EVALUATION_ARTIFACT_SHA_MISMATCH",
          any(f.startswith("C3_EVALUATION_ARTIFACT_SHA_MISMATCH") for f in fails), str(fails))


def case_08_evaluation_identity_mismatch(tmp):
    work = tmp / "c08"
    m = base_manifest(work)
    m["evaluations"][0]["candidate_id"] = "cand-some-other-candidate-ffffff"
    code, out = run_cli(work, m)
    fails = failed_checks(out)
    check("08 evaluation candidate-identity mismatch -> exit 1", code == 1, "exit=%d" % code)
    check("08 names A4 with both identities",
          any(f.startswith("A4_EVALUATION_CANDIDATE_IDENTITY_MISMATCH") for f in fails),
          str(fails))


def case_09_self_authored_pass(tmp):
    """An artifact that exists and hashes exactly as cited, but was typed."""
    work = tmp / "c09"
    m = base_manifest(work)
    sha = write(work / "evidence" / "authored.txt", PASS_ARTIFACT_SELF_AUTHORED)
    m["evaluations"][0]["artifact"] = {
        "path": "evidence/authored.txt", "sha256": sha, "execution": "EXECUTED"}
    code, out = run_cli(work, m)
    oks, reviews = ok_items(out), review_items(out)
    check("09 self-authored PASS: artifact identity may pass",
          code == 0 and any(o.startswith("ARTIFACT_IDENTITY_VERIFIED") for o in oks),
          "exit=%d %s" % (code, oks))
    check("09 evaluation-quality review warning remains",
          any(r.startswith("EVALUATION_QUALITY_REQUIRES_REVIEW") for r in reviews), str(reviews))
    check("09 never asserts EVALUATION_QUALITY_VERIFIED",
          "EVALUATION_QUALITY_VERIFIED" not in out)
    check("09 PROMOTION_AUTHORIZED=false", "PROMOTION_AUTHORIZED: false" in out)


def case_10_contradictory_plan(tmp):
    work = tmp / "c10"
    m = base_manifest(work)
    # Same rule, identical condition, incompatible expected outcomes.
    m["evidence_plan"]["fixtures"].append(
        {"fixture_id": "P2_CONTRA", "rule_id": "L10", "kind": "NEAR_MISS",
         "condition": {"allowed_files.is_empty": False, "policies.merge.is_set": True},
         "expected_outcome": "NOT_APPLICABLE"})
    code, out = run_cli(work, m)
    fails = failed_checks(out)
    check("10 contradictory evidence plan -> exit 1", code == 1, "exit=%d" % code)
    check("10 names D3_INCOMPATIBLE_OUTCOMES_AT_IDENTICAL_CONDITION",
          any(f.startswith("D3_INCOMPATIBLE_OUTCOMES_AT_IDENTICAL_CONDITION") for f in fails),
          str(fails))


def case_10b_rekey_evasion(tmp):
    """The same real state described under a second variable name."""
    work = tmp / "c10b"
    m = base_manifest(work)
    m["evidence_plan"]["fixtures"].append(
        {"fixture_id": "P2_REKEY", "rule_id": "L10", "kind": "NEAR_MISS",
         "condition": {"allowed_files.count": 1, "policies.merge.is_set": True},
         "expected_outcome": "NOT_APPLICABLE"})
    code, out = run_cli(work, m)
    fails = failed_checks(out)
    check("10b re-keyed condition rejected as out-of-domain -> exit 1",
          code == 1 and any(f.startswith("D2_CONDITION_VARIABLE_OUTSIDE_DECLARED_DOMAIN")
                            for f in fails), "exit=%d %s" % (code, fails))


def case_10c_fixture_id_conflict(tmp):
    work = tmp / "c10c"
    m = base_manifest(work)
    m["evidence_plan"]["fixtures"].append(
        {"fixture_id": "P1", "rule_id": "L1", "kind": "NEGATIVE",
         "condition": {"task.id.is_empty": True, "authorization.scope.is_empty": False},
         "expected_outcome": "FAIL"})
    code, out = run_cli(work, m)
    fails = failed_checks(out)
    check("10c same fixture ID, conflicting definitions -> exit 1",
          code == 1 and any(f.startswith("D4_FIXTURE_ID_CONFLICTING_DEFINITIONS")
                            for f in fails), "exit=%d %s" % (code, fails))


def case_10d_distinct_conditions_allowed(tmp):
    """Guard against overmatching: distinct conditions may differ in outcome."""
    work = tmp / "c10d"
    m = base_manifest(work)
    m["evidence_plan"]["fixtures"].append(
        {"fixture_id": "N2", "rule_id": "L10", "kind": "NEGATIVE",
         "condition": {"allowed_files.is_empty": True, "policies.merge.is_set": True},
         "expected_outcome": "FAIL"})
    code, out = run_cli(work, m)
    check("10d distinct conditions with different outcomes allowed -> exit 0",
          code == 0, "exit=%d %s" % (code, failed_checks(out)))


def case_11_unstructured_source(tmp):
    work = tmp / "c11"
    m = base_manifest(work, source_text=SOURCE_UNSTRUCTURED,
                      addressability="NOT_MACHINE_ADDRESSABLE")
    code, out = run_cli(work, m)
    reviews = review_items(out)
    check("11 source not mechanically addressable -> SOURCE_COVERAGE_REQUIRES_REVIEW",
          has_prefix(reviews, "SOURCE_COVERAGE_REQUIRES_REVIEW"), str(reviews))
    check("11 no fabricated coverage completeness",
          code == 0 and not any(o.startswith("REFERENCE_EXISTS") for o in ok_items(out)),
          "exit=%d" % code)


def case_12_claimed_but_no_executed_artifact(tmp):
    work = tmp / "c12"
    m = base_manifest(work)
    # Claim stands; the only falsification evaluation is declared, not executed.
    m["evaluations"][1]["artifact"]["execution"] = "DECLARED_ONLY"
    code, out = run_cli(work, m)
    fails = failed_checks(out)
    check("12 claimed falsification evidence, no executed artifact -> exit 1",
          code == 1, "exit=%d" % code)
    check("12 names F1_CLAIMED_FALSIFICATION_EVIDENCE_HAS_NO_EXECUTED_ARTIFACT",
          any(f.startswith("F1_CLAIMED_FALSIFICATION_EVIDENCE_HAS_NO_EXECUTED_ARTIFACT")
              for f in fails), str(fails))


def case_12b_claimed_with_no_negative_evaluation(tmp):
    work = tmp / "c12b"
    m = base_manifest(work)
    m["evaluations"] = [m["evaluations"][0]]
    code, out = run_cli(work, m)
    fails = failed_checks(out)
    check("12b claim with no falsification evaluation at all -> exit 1",
          code == 1 and any(f.startswith("F1_CLAIMED_FALSIFICATION") for f in fails),
          "exit=%d %s" % (code, fails))


def case_13_valid_falsification(tmp):
    work = tmp / "c13"
    code, out = run_cli(work, base_manifest(work))
    oks, reviews = ok_items(out), review_items(out)
    check("13 falsification artifact identity verified",
          any(o.startswith("FALSIFICATION_ARTIFACT_VERIFIED") for o in oks), str(oks))
    # Two distinct adequacy markers with different scopes, asserted
    # separately: a single "any adequacy marker" assertion let either one be
    # deleted silently. Mutation testing found that hole.
    check("13 per-evaluation adequacy review present",
          "FALSIFICATION_ADEQUACY_REQUIRES_REVIEW:EVAL_FALSIFICATION" in reviews,
          str(reviews))
    check("13 set-level adequacy review present",
          "FALSIFICATION_ADEQUACY_REQUIRES_REVIEW:"
          "SET_ADEQUACY_NOT_MECHANICALLY_DEMONSTRATED" in reviews, str(reviews))
    check("13 adequacy never asserted as verified",
          "FALSIFICATION_ADEQUACY_VERIFIED" not in out
          and "FALSIFICATION_ADEQUATE" not in out)


def case_13b_prose_candidate_falsification_artifact(tmp):
    """
    A PROSE_ONLY candidate citing an executed falsification artifact. Check F
    is silent here by design (nothing to execute for a non-code candidate), so
    the per-evaluation adequacy marker from check C is the only one, and this
    case covers it independently of check F.
    """
    work = tmp / "c13b"
    m = base_manifest(work)
    m["candidate"]["candidate_kind"] = "PROSE_ONLY"
    m["claims"]["negative_falsification_evidence"] = False
    code, out = run_cli(work, m)
    reviews = review_items(out)
    check("13b prose candidate: artifact identity verified, adequacy still manual",
          code == 0
          and any(o.startswith("FALSIFICATION_ARTIFACT_VERIFIED") for o in ok_items(out))
          and "FALSIFICATION_ADEQUACY_REQUIRES_REVIEW:EVAL_FALSIFICATION" in reviews,
          "exit=%d %s" % (code, reviews))
    check("13b set-level adequacy marker absent when check F does not apply",
          "SET_ADEQUACY_NOT_MECHANICALLY_DEMONSTRATED" not in out)


def case_14_unresolved_semantics(tmp):
    work = tmp / "c14"
    m = base_manifest(work)
    m["unresolved_semantics"].append(
        {"item_id": "SEM2", "question": "which delimiter policy defines a clause",
         "status": "OPEN"})
    code, out = run_cli(work, m)
    reviews = review_items(out)
    check("14 open semantics remain explicit and named",
          has_prefix(reviews, "UNRESOLVED_SEMANTICS_REQUIRES_REVIEW")
          and "OPEN:SEM1" in out and "OPEN:SEM2" in out, str(reviews))
    check("14 internally-consistent metadata does not resolve them",
          code == 0 and "RESOLVED:SEM1" not in out, "exit=%d" % code)


def case_14b_resolved_without_evidence(tmp):
    work = tmp / "c14b"
    m = base_manifest(work)
    m["unresolved_semantics"][0]["status"] = "RESOLVED"
    code, out = run_cli(work, m)
    fails = failed_checks(out)
    check("14b RESOLVED without evidence ref -> exit 1",
          code == 1 and any(f.startswith("G1_SEMANTIC_ITEM_MARKED_RESOLVED_WITHOUT_EVIDENCE")
                            for f in fails), "exit=%d %s" % (code, fails))


def case_18_required_fields(tmp):
    """
    One case per required-field predicate. Mutation testing showed each of
    these branches could be deleted with the rest of the suite still green -
    the same all-positive-coverage hole the pilot found in its own gates.
    """
    work = tmp / "c18a"
    m = base_manifest(work)
    m["candidate"].pop("candidate_id")
    code, out = run_cli(work, m)
    check("18a frozen candidate identity missing -> exit 1",
          code == 1 and any(f.startswith("A1_FROZEN_CANDIDATE_IDENTITY_MISSING")
                            for f in failed_checks(out)), "exit=%d" % code)
    check("18a candidate id renders UNRESOLVED", "CANDIDATE_ID: UNRESOLVED" in out)

    work = tmp / "c18b"
    m = base_manifest(work)
    m["candidate"].pop("frozen_revision")
    code, out = run_cli(work, m)
    check("18b frozen revision missing -> exit 1",
          code == 1 and any(f.startswith("A2_FROZEN_REVISION_MISSING")
                            for f in failed_checks(out)), "exit=%d" % code)

    work = tmp / "c18c"
    m = base_manifest(work)
    m["candidate"].pop("source_identity")
    code, out = run_cli(work, m)
    check("18c source references cited with no pinned source -> exit 1",
          code == 1 and any(f.startswith("B1_PINNED_SOURCE_IDENTITY_MISSING")
                            for f in failed_checks(out)), "exit=%d" % code)

    work = tmp / "c18d"
    m = base_manifest(work)
    m["evaluations"][0].pop("artifact")
    code, out = run_cli(work, m)
    check("18d evaluation cites no artifact -> exit 1",
          code == 1 and any(f.startswith("C1_EVALUATION_ARTIFACT_NOT_CITED")
                            for f in failed_checks(out)), "exit=%d" % code)

    work = tmp / "c18e"
    m = base_manifest(work)
    m["evaluations"][0]["artifact"].pop("sha256")
    code, out = run_cli(work, m)
    check("18e evaluation artifact cited without a hash -> exit 1",
          code == 1 and any(f.startswith("C1_EVALUATION_ARTIFACT_NOT_CITED")
                            for f in failed_checks(out)), "exit=%d" % code)

    work = tmp / "c18f"
    m = base_manifest(work)
    m["evidence_plan"]["fixtures"][0]["expected_outcome"] = "PROBABLY_PASSES"
    code, out = run_cli(work, m)
    check("18f outcome outside declared vocabulary -> exit 1",
          code == 1 and any(f.startswith("D1_OUTCOME_OUTSIDE_DECLARED_VOCABULARY")
                            for f in failed_checks(out)), "exit=%d" % code)

    work = tmp / "c18g"
    m = base_manifest(work)
    m["source_references"].append(
        {"reference_id": "REF_BLANK", "cited_identifier": "", "purpose": "IN_SCOPE"})
    code, out = run_cli(work, m)
    check("18g source reference with no identifier -> exit 1",
          code == 1 and any(f.startswith("E1_SOURCE_REFERENCE_IDENTIFIER_ABSENT")
                            for f in failed_checks(out)), "exit=%d" % code)

    work = tmp / "c18i"
    m = base_manifest(work)
    m["evaluations"][0].pop("candidate_id")
    code, out = run_cli(work, m)
    check("18i evaluation with no candidate identity at all -> exit 1",
          code == 1 and any(f.startswith("A3_EVALUATION_CANDIDATE_IDENTITY_ABSENT")
                            for f in failed_checks(out)), "exit=%d" % code)
    check("18i failing run reports STATUS: INTEGRITY_INCOMPLETE",
          "STATUS: INTEGRITY_INCOMPLETE" in out)

    work = tmp / "c18j"
    m = base_manifest(work)
    m["candidate"]["source_identity"].pop("sha256")
    code, out = run_cli(work, m)
    check("18j pinned source with no hash -> exit 1",
          code == 1 and any(f.startswith("B1_PINNED_SOURCE_IDENTITY_MISSING")
                            for f in failed_checks(out)), "exit=%d" % code)

    work = tmp / "c18k"
    m = base_manifest(work)
    m["candidate"]["source_identity"].pop("path")
    code, out = run_cli(work, m)
    check("18k pinned source with no path -> exit 1",
          code == 1 and any(f.startswith("B1_PINNED_SOURCE_IDENTITY_MISSING")
                            for f in failed_checks(out)), "exit=%d" % code)

    work = tmp / "c18h"
    m = base_manifest(work)
    # A candidate that cites no source at all is not forced to pin one.
    m["source_references"] = []
    m["candidate"].pop("source_identity")
    code, out = run_cli(work, m)
    check("18h no source cited and none pinned -> exit 0",
          code == 0, "exit=%d %s" % (code, failed_checks(out)))


def case_19_unverifiable_environment(tmp):
    """A pinned source absent here is unverifiable, not a mismatch."""
    work = tmp / "c19"
    m = base_manifest(work)
    (work / "source" / "rules.txt").unlink()
    code, out = run_cli(work, m)
    warns = [l.strip()[len("WARN "):] for l in out.split("\n") if l.strip().startswith("WARN ")]
    check("19 unreadable pinned source warns, does not fail",
          code == 0 and any(w.startswith("B_SOURCE_UNVERIFIABLE_IN_THIS_ENVIRONMENT")
                            for w in warns), "exit=%d %s" % (code, warns))
    check("19 integrity reported as partial, not verified",
          "EVIDENCE_INTEGRITY: PARTIAL_UNVERIFIABLE_IN_THIS_ENVIRONMENT" in out)
    check("19 coverage review raised when source unreadable",
          "SOURCE_COVERAGE_REQUIRES_REVIEW:SOURCE_UNREADABLE_HERE" in out)


def case_20_falsification_scope_is_code_backed_only(tmp):
    """
    Check F exists because only an implementation can be executed against a
    falsification fixture. A PROSE_ONLY candidate must not be failed by it -
    and must not be silently credited either.
    """
    work = tmp / "c20"
    m = base_manifest(work)
    m["candidate"]["candidate_kind"] = "PROSE_ONLY"
    m["evaluations"][1]["artifact"]["execution"] = "DECLARED_ONLY"
    code, out = run_cli(work, m)
    fails = failed_checks(out)
    check("20 prose candidate not failed by the code-backed falsification check",
          code == 0 and not any(f.startswith("F1_") for f in fails),
          "exit=%d %s" % (code, fails))
    check("20 no falsification artifact credited for a declared-only artifact",
          "FALSIFICATION_ARTIFACT_VERIFIED" not in out)

    # Same manifest, code-backed: the check does apply.
    work = tmp / "c20b"
    m2 = base_manifest(work)
    m2["evaluations"][1]["artifact"]["execution"] = "DECLARED_ONLY"
    code2, out2 = run_cli(work, m2)
    check("20b same evidence, code-backed candidate -> exit 1",
          code2 == 1 and any(f.startswith("F1_") for f in failed_checks(out2)),
          "exit=%d" % code2)


def case_21_output_order_independence(tmp):
    """
    Reruns of one process cannot show order-independence. Two manifests that
    differ only in input array order must render byte-identically.
    """
    work = tmp / "c21"
    m = base_manifest(work)
    m["source_references"] = [
        {"reference_id": "REF_X", "cited_identifier": "L98", "purpose": "IN_SCOPE"},
        {"reference_id": "REF_Y", "cited_identifier": "L99", "purpose": "SCOPE_EXCLUSION"},
        {"reference_id": "REF1", "cited_identifier": "L1", "purpose": "IN_SCOPE"},
    ]
    code_a, out_a = run_cli(work, m)

    work_b = tmp / "c21b"
    m2 = base_manifest(work_b)
    m2["source_references"] = list(reversed(m["source_references"]))
    m2["evaluations"] = list(reversed(m2["evaluations"]))
    m2["evidence_plan"]["fixtures"] = list(reversed(m2["evidence_plan"]["fixtures"]))
    code_b, out_b = run_cli(work_b, m2)

    # Paths differ between the two temp dirs, so compare the order-bearing
    # sections rather than raw bytes.
    check("21 failures sorted independently of input order",
          failed_checks(out_a) == failed_checks(out_b) and code_a == code_b == 1,
          "%s vs %s" % (failed_checks(out_a), failed_checks(out_b)))
    check("21 review items sorted independently of input order",
          review_items(out_a) == review_items(out_b),
          "%s vs %s" % (review_items(out_a), review_items(out_b)))
    check("21 two distinct failures both reported",
          len([f for f in failed_checks(out_a)
               if f.startswith("E2_CITED_IDENTIFIER_NOT_FOUND_IN_SOURCE")]) == 2,
          str(failed_checks(out_a)))


def case_22_claim_gates_the_falsification_requirement(tmp):
    """
    Check F is triggered by the manifest's own claim. A code-backed candidate
    that makes no negative-evidence claim is not failed for lacking one - the
    requirement is "back the claim you made", not "every candidate must have
    falsification evidence".
    """
    work = tmp / "c22"
    m = base_manifest(work)
    m["claims"]["negative_falsification_evidence"] = False
    m["evaluations"] = [m["evaluations"][0]]
    code, out = run_cli(work, m)
    fails = failed_checks(out)
    check("22 code-backed candidate making no falsification claim -> exit 0",
          code == 0 and not any(f.startswith("F1_") for f in fails),
          "exit=%d %s" % (code, fails))

    work = tmp / "c22b"
    m2 = base_manifest(work)
    m2["claims"]["negative_falsification_evidence"] = True
    m2["evaluations"] = [m2["evaluations"][0]]
    code2, out2 = run_cli(work, m2)
    check("22b same evidence, claim asserted -> exit 1",
          code2 == 1 and any(f.startswith("F1_") for f in failed_checks(out2)),
          "exit=%d" % code2)


def case_23_condition_identity_ignores_key_order(tmp):
    """
    Two fixtures describing the same condition with the keys written in
    opposite order are the same condition. If key order changed the condition
    key, a contradiction could be hidden by reordering two fields.
    """
    work = tmp / "c23"
    m = base_manifest(work)
    m["evidence_plan"]["fixtures"] = [
        {"fixture_id": "K1", "rule_id": "L10", "kind": "POSITIVE",
         "condition": {"allowed_files.is_empty": False, "policies.merge.is_set": True},
         "expected_outcome": "PASS"},
        {"fixture_id": "K2", "rule_id": "L10", "kind": "NEAR_MISS",
         "condition": {"policies.merge.is_set": True, "allowed_files.is_empty": False},
         "expected_outcome": "NOT_APPLICABLE"},
    ]
    code, out = run_cli_preserving_key_order(work, m)
    fails = failed_checks(out)
    check("23 reordered condition keys still collide -> exit 1",
          code == 1 and any(f.startswith("D3_INCOMPATIBLE_OUTCOMES_AT_IDENTICAL_CONDITION")
                            for f in fails), "exit=%d %s" % (code, fails))

    # Control: same two fixtures, genuinely different conditions, no clash.
    work_ok = tmp / "c23b"
    m2 = base_manifest(work_ok)
    m2["evidence_plan"]["fixtures"] = [
        {"fixture_id": "K1", "rule_id": "L10", "kind": "POSITIVE",
         "condition": {"allowed_files.is_empty": False, "policies.merge.is_set": True},
         "expected_outcome": "PASS"},
        {"fixture_id": "K2", "rule_id": "L10", "kind": "NEAR_MISS",
         "condition": {"policies.merge.is_set": False, "allowed_files.is_empty": False},
         "expected_outcome": "NOT_APPLICABLE"},
    ]
    code2, out2 = run_cli_preserving_key_order(work_ok, m2)
    check("23b genuinely different conditions do not collide -> exit 0",
          code2 == 0, "exit=%d %s" % (code2, failed_checks(out2)))


def case_15_deterministic_rerun(tmp):
    work = tmp / "c15"
    m = base_manifest(work)
    outs, codes = [], []
    for _ in range(3):
        code, out = run_cli(work, m)
        outs.append(out)
        codes.append(code)
    text_identical = outs[0] == outs[1] == outs[2] and codes[0] == codes[1] == codes[2]

    jouts = []
    for _ in range(3):
        code, out = run_cli(work, m, extra_args=("--json",))
        jouts.append(out)
    json_identical = jouts[0] == jouts[1] == jouts[2]
    check("15 three text executions byte-identical", text_identical)
    check("15 three JSON executions byte-identical", json_identical)


def case_16_malformed(tmp):
    work = tmp / "c16"
    work.mkdir(parents=True, exist_ok=True)
    code, out = run_raw(work, "{ this is not json")
    check("16 malformed JSON -> exit 2",
          code == 2 and "STATUS: INPUT_UNUSABLE" in out, "exit=%d" % code)

    work2 = tmp / "c16b"
    m = base_manifest(work2)
    m["evidence_plan"]["fixtures"][0].pop("fixture_id")
    code2, out2 = run_cli(work2, m)
    check("16b structurally unusable manifest -> exit 2",
          code2 == 2 and "STATUS: INPUT_UNUSABLE" in out2, "exit=%d" % code2)

    work3 = tmp / "c16c"
    m3 = base_manifest(work3)
    m3["unresolved_semantics"][0]["status"] = "PROBABLY_FINE"
    code3, out3 = run_cli(work3, m3)
    check("16c out-of-vocabulary semantic status -> exit 2",
          code3 == 2 and "STATUS: INPUT_UNUSABLE" in out3, "exit=%d" % code3)

    work4 = tmp / "c16d"
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run([sys.executable, str(SCRIPT), str(work4 / "nope.json")],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    ALL_OUTPUT.append(proc.stdout.decode("utf-8"))
    check("16d missing manifest file -> exit 2", proc.returncode == 2,
          "exit=%d" % proc.returncode)


def case_24_text_render_injection(tmp):
    """
    Required hostile fixtures for the text-render line-injection remediation
    (Judge caveat on 78a00d5b7c...143d22b): a manifest-controlled scalar must
    never be able to forge a new logical line in the text renderer, in
    particular a second `PROMOTION_AUTHORIZED:` line. Each payload below
    lands in a different rendered field: CANDIDATE_ID, a FAIL locator, a
    WARN locator, and a REVIEW/OK locator.
    """
    forged_line = "PROMOTION_AUTHORIZED: true"

    def assert_no_forged_line(label, out):
        # splitlines() matches the Packet's own reserved-field invariant
        # wording and Python's actual line-boundary set; split("\n") would
        # miss a survivor among \v \f \x1c \x1d \x1e \x85 U+2028 U+2029
        # (Judge finding on a "CR/LF only" partial variant: split("\n")
        # let 8 of the 10 boundary characters through undetected).
        lines = out.splitlines()
        exact_forged = [l for l in lines if l.strip() == forged_line]
        check("24 %s: no standalone forged PROMOTION_AUTHORIZED line" % label,
              exact_forged == [], "lines=%r" % exact_forged)
        check("24 %s: exactly one PROMOTION_AUTHORIZED line, value false" % label,
              sum(1 for l in lines if l.strip().startswith("PROMOTION_AUTHORIZED:")) == 1
              and "PROMOTION_AUTHORIZED: false" in lines)

    work = tmp / "c24_id_lf"
    m = base_manifest(work)
    hostile = "cand-hostile\n" + forged_line
    m["candidate"]["candidate_id"] = hostile
    m["evaluations"][0]["candidate_id"] = hostile
    m["evaluations"][1]["candidate_id"] = hostile
    _, out = run_cli(work, m)
    assert_no_forged_line("candidate_id \\n injection", out)
    check("24 escaped candidate_id still visible, on one CANDIDATE_ID line",
          ("CANDIDATE_ID: cand-hostile\\n" + forged_line) in out.split("\n"),
          "out=%r" % out)

    work = tmp / "c24_id_crlf"
    m = base_manifest(work)
    hostile = "cand-hostile\r\n" + forged_line
    m["candidate"]["candidate_id"] = hostile
    m["evaluations"][0]["candidate_id"] = hostile
    m["evaluations"][1]["candidate_id"] = hostile
    _, out = run_cli(work, m)
    assert_no_forged_line("candidate_id \\r\\n injection", out)
    check("24 escaped CRLF candidate_id still visible, on one CANDIDATE_ID line",
          ("CANDIDATE_ID: cand-hostile\\r\\n" + forged_line) in out.split("\n"),
          "out=%r" % out)

    # Non-CR/LF line-boundary character. str.splitlines() also breaks on
    # \v \f \x1c \x1d \x1e \x85 U+2028 U+2029; a fix (or a later edit)
    # that only escapes CR/LF ships green against a CR/LF-only suite. This
    # case, plus splitlines()-based detection above, is what actually
    # catches that narrowing (Judge-verified: escaping only \r\n reproduces
    # this exact forgery via \x85 while every CR/LF-only case stays green).
    work = tmp / "c24_id_nel"
    m = base_manifest(work)
    hostile = "cand-hostile\x85" + forged_line
    m["candidate"]["candidate_id"] = hostile
    m["evaluations"][0]["candidate_id"] = hostile
    m["evaluations"][1]["candidate_id"] = hostile
    _, out = run_cli(work, m)
    assert_no_forged_line("candidate_id NEL (U+0085) injection", out)
    check("24 escaped NEL candidate_id still visible, on one CANDIDATE_ID line",
          ("CANDIDATE_ID: cand-hostile\\x85" + forged_line) in out.splitlines(),
          "out=%r" % out)

    work = tmp / "c24_fail_locator"
    m = base_manifest(work)
    m["source_references"].append({
        "reference_id": "REF-HOSTILE\n" + forged_line, "cited_identifier": "",
        "purpose": "IN_SCOPE",
    })
    code, out = run_cli(work, m)
    assert_no_forged_line("FAIL locator injection", out)
    check("24 FAIL locator injection still fails the run",
          code == 1, "exit=%d" % code)

    work = tmp / "c24_warn_locator"
    m = base_manifest(work)
    m["candidate"]["source_identity"]["path"] = "does/not/exist\n" + forged_line
    _, out = run_cli(work, m)
    assert_no_forged_line("WARN locator injection", out)

    work = tmp / "c24_review_locator"
    m = base_manifest(work)
    m["source_references"].append({
        "reference_id": "REF-EXCL\n" + forged_line, "cited_identifier": "L1",
        "purpose": "SCOPE_EXCLUSION",
    })
    _, out = run_cli(work, m)
    assert_no_forged_line("REVIEW/OK locator injection", out)

    work = tmp / "c24_benign_unicode"
    m = base_manifest(work)
    benign = "cand-café-中文-\U0001f600-colon:dash-under_score.v2"
    m["candidate"]["candidate_id"] = benign
    m["evaluations"][0]["candidate_id"] = benign
    m["evaluations"][1]["candidate_id"] = benign
    code, out = run_cli(work, m)
    check("24 benign unicode/punctuation candidate_id passes through unescaped",
          ("CANDIDATE_ID: " + benign) in out.split("\n"), "out=%r" % out)
    check("24 benign unicode candidate_id does not fail the run",
          code == 0, "exit=%d" % code)


def case_25_error_path_message_injection(tmp):
    """
    Required hostile test: the exit-2 MESSAGE line is built from a
    ManifestError whose text can itself embed a manifest-controlled
    fixture_id (see check_d_evidence_plan's "requires an object condition").
    That text path must not be able to forge a logical line either.
    """
    forged_line = "PROMOTION_AUTHORIZED: true"

    work = tmp / "c25"
    m = base_manifest(work)
    m["evidence_plan"]["fixtures"][0]["fixture_id"] = "P1-HOSTILE\n" + forged_line
    m["evidence_plan"]["fixtures"][0]["condition"] = "not-an-object"
    code, out = run_cli(work, m)
    lines = out.splitlines()
    check("25 exit-2 error path -> exit 2", code == 2, "exit=%d" % code)
    check("25 exit-2 error path names INPUT_UNUSABLE", "STATUS: INPUT_UNUSABLE" in out)
    check("25 exit-2 error path: no standalone forged PROMOTION_AUTHORIZED line",
          not any(l.strip() == forged_line for l in lines),
          "lines=%r" % [l for l in lines if forged_line in l])
    check("25 exit-2 error path: exactly one PROMOTION_AUTHORIZED line, value false",
          sum(1 for l in lines if l.strip().startswith("PROMOTION_AUTHORIZED:")) == 1
          and "PROMOTION_AUTHORIZED: false" in lines)
    check("25 hostile fixture_id still visible, escaped, on the MESSAGE line",
          any(l.startswith("MESSAGE: ") and ("P1-HOSTILE\\n" + forged_line) in l
              for l in lines),
          "lines=%r" % lines)

    code_j, out_j = run_cli(work, m, extra_args=("--json",))
    check("25 exit-2 json path also exits 2", code_j == 2, "exit=%d" % code_j)
    payload = json.loads(out_j)
    check("25 json error payload PROMOTION_AUTHORIZED is boolean false",
          payload["PROMOTION_AUTHORIZED"] is False, "payload=%r" % payload)
    check("25 json error payload preserves the raw hostile fixture_id (JSON contract unchanged)",
          ("P1-HOSTILE\n" + forged_line) in payload.get("MESSAGE", ""),
          "message=%r" % payload.get("MESSAGE"))


def case_25b_error_path_message_injection_item_id(tmp):
    """
    check_g_unresolved_semantics raises ManifestError with the manifest-
    controlled item_id embedded ("unresolved_semantics[%s].status must be
    one of ..."), a second exit-2 injection site distinct from case_25's
    fixture_id site. Same requirement, same defence (_text_safe applies to
    every exit-2 MESSAGE regardless of which check raised it), a separate
    committed case so this site has a permanent regression guard too.
    """
    forged_line = "PROMOTION_AUTHORIZED: true"

    work = tmp / "c25b"
    m = base_manifest(work)
    m["unresolved_semantics"][0]["item_id"] = "SEM-HOSTILE\n" + forged_line
    m["unresolved_semantics"][0]["status"] = "NOT_A_REAL_STATUS"
    code, out = run_cli(work, m)
    lines = out.splitlines()
    check("25b exit-2 error path (item_id site) -> exit 2", code == 2, "exit=%d" % code)
    check("25b exit-2 error path: no standalone forged PROMOTION_AUTHORIZED line",
          not any(l.strip() == forged_line for l in lines),
          "lines=%r" % [l for l in lines if forged_line in l])
    check("25b exit-2 error path: exactly one PROMOTION_AUTHORIZED line, value false",
          sum(1 for l in lines if l.strip().startswith("PROMOTION_AUTHORIZED:")) == 1
          and "PROMOTION_AUTHORIZED: false" in lines)
    check("25b hostile item_id still visible, escaped, on the MESSAGE line",
          any(l.startswith("MESSAGE: ") and ("SEM-HOSTILE\\n" + forged_line) in l
              for l in lines),
          "lines=%r" % lines)

    code_j, out_j = run_cli(work, m, extra_args=("--json",))
    check("25b exit-2 json path also exits 2", code_j == 2, "exit=%d" % code_j)
    payload = json.loads(out_j)
    check("25b json error payload PROMOTION_AUTHORIZED is boolean false",
          payload["PROMOTION_AUTHORIZED"] is False, "payload=%r" % payload)
    check("25b json error payload preserves the raw hostile item_id (JSON contract unchanged)",
          ("SEM-HOSTILE\n" + forged_line) in payload.get("MESSAGE", ""),
          "message=%r" % payload.get("MESSAGE"))


def case_26_injection_deterministic_rerun(tmp):
    """Required hostile test: malicious input reruns byte-identically too."""
    work = tmp / "c26"
    m = base_manifest(work)
    hostile = "cand-hostile\r\nPROMOTION_AUTHORIZED: true\nWARN forged\n"
    m["candidate"]["candidate_id"] = hostile
    m["evaluations"][0]["candidate_id"] = hostile
    m["evaluations"][1]["candidate_id"] = hostile

    outs, codes = [], []
    for _ in range(3):
        code, out = run_cli(work, m)
        outs.append(out)
        codes.append(code)
    check("26 malicious input: three text executions byte-identical",
          outs[0] == outs[1] == outs[2] and codes[0] == codes[1] == codes[2])

    jouts = []
    for _ in range(3):
        _, out = run_cli(work, m, extra_args=("--json",))
        jouts.append(out)
    check("26 malicious input: three JSON executions byte-identical",
          jouts[0] == jouts[1] == jouts[2])


def case_17_no_promotion_authority(tmp):
    """
    Across every output produced by every case above.

    The text-render injection cases (24-26) deliberately put the literal
    text "PROMOTION_AUTHORIZED: true" inside manifest-controlled fields, so
    a raw substring search over the joined output would now false-positive
    on safely-escaped content (e.g. "CANDIDATE_ID: cand-hostile\\nPROMOTION_
    AUTHORIZED: true" contains that substring but is one inert line, not a
    forged field). The real invariant, matching the reserved-field
    contract, is that no *line* is the forged field.

    Two precision fixes, both from independent Judge review of 78a00d5's
    remediation: (1) splitlines(), not split("\n") - split("\n") only
    catches 2 of the 10 characters str.splitlines() treats as a line
    boundary, so it would still pass if a future edit narrowed the escape
    map to CR/LF only; (2) strip() before comparing/matching a candidate
    forged line, so an indented forged line cannot slip past a bare
    equality or startswith() check either. Neither weakens what the old
    substring check caught for a real forgery; both are strictly more
    exact about what counts as "a line".
    """
    joined = "\n".join(ALL_OUTPUT)
    no_forged_true_line = all(
        line.strip() not in ("PROMOTION_AUTHORIZED: true", "PROMOTION_AUTHORIZED: True")
        for o in ALL_OUTPUT for line in o.splitlines()
    )
    check("17 PROMOTION_AUTHORIZED never true in any case",
          no_forged_true_line
          and '"PROMOTION_AUTHORIZED": true' not in joined)
    check("17 PROMOTION_AUTHORIZED present in every rendered result",
          all("PROMOTION_AUTHORIZED" in o for o in ALL_OUTPUT if o.strip()))
    check("17 no output claims promotion, canonical, or deployed status",
          not any(t in joined for t in ("PROMOTION_VERIFIED", "PROMOTION_GRANTED",
                                       "CANONICAL:", "DEPLOYED:")))
    check("17 forbidden semantic claims absent everywhere",
          "EXCLUSION_LEGITIMATE" not in joined
          and "EVALUATION_QUALITY_VERIFIED" not in joined)
    check("17 at most one PROMOTION_AUTHORIZED line in any single output "
          "(reserved-field invariant)",
          all(sum(1 for line in o.splitlines()
                  if line.strip().startswith("PROMOTION_AUTHORIZED:")) <= 1
              for o in ALL_OUTPUT if o.strip()))


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        print("=== check A/B: identity binding and pinned source ===")
        case_01_valid(tmp)
        case_02_candidate_identity_changed(tmp)
        case_03_source_hash_mismatch(tmp)

        print("=== check E: source references (existence is not relevance) ===")
        case_04_invented_reference(tmp)
        case_05_real_but_irrelevant(tmp)
        case_11_unstructured_source(tmp)

        print("=== check C: artifact integrity (identity is not quality) ===")
        case_06_missing_artifact(tmp)
        case_07_artifact_hash_mismatch(tmp)
        case_08_evaluation_identity_mismatch(tmp)
        case_09_self_authored_pass(tmp)

        print("=== check D: evidence-plan consistency (load-bearing) ===")
        case_10_contradictory_plan(tmp)
        case_10b_rekey_evasion(tmp)
        case_10c_fixture_id_conflict(tmp)
        case_10d_distinct_conditions_allowed(tmp)

        print("=== check F: falsification (artifact is not adequacy) ===")
        case_12_claimed_but_no_executed_artifact(tmp)
        case_12b_claimed_with_no_negative_evaluation(tmp)
        case_13_valid_falsification(tmp)
        case_13b_prose_candidate_falsification_artifact(tmp)

        print("=== check G: unresolved semantics ===")
        case_14_unresolved_semantics(tmp)
        case_14b_resolved_without_evidence(tmp)

        print("=== required-field completeness ===")
        case_18_required_fields(tmp)
        case_19_unverifiable_environment(tmp)

        print("=== scope boundaries ===")
        case_20_falsification_scope_is_code_backed_only(tmp)

        case_22_claim_gates_the_falsification_requirement(tmp)

        print("=== determinism and input contract ===")
        case_23_condition_identity_ignores_key_order(tmp)
        case_21_output_order_independence(tmp)
        case_15_deterministic_rerun(tmp)
        case_16_malformed(tmp)

        print("=== text-render line-injection remediation ===")
        case_24_text_render_injection(tmp)
        case_25_error_path_message_injection(tmp)
        case_25b_error_path_message_injection_item_id(tmp)
        case_26_injection_deterministic_rerun(tmp)

        print("=== promotion-authority boundary ===")
        case_17_no_promotion_authority(tmp)

    failed = [n for n, ok, _ in RESULTS if not ok]
    print()
    print("%d/%d passed" % (len(RESULTS) - len(failed), len(RESULTS)))
    if failed:
        print("FAILED: %s" % ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
