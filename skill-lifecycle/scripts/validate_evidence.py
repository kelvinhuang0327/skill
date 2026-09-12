#!/usr/bin/env python3
"""
Deterministic evidence-integrity / completeness checker for a skill candidate
evidence manifest.

ROLE (read this before reading any output): this script checks EVIDENCE
INTEGRITY and EVIDENCE COMPLETENESS. It does not check semantic legitimacy
and it holds no promotion authority. `PROMOTION_AUTHORIZED` is a constant
`false` in every code path.

What it can establish:
  - a frozen candidate identity exists and later evidence binds to it;
  - a pinned source identity is supplied and, where readable, hashes as cited;
  - cited artifacts exist and hash as cited (artifact IDENTITY, nothing more);
  - an evidence plan is internally satisfiable (no contradictory expectations);
  - cited machine-addressable source identifiers actually exist in the source;
  - a claimed executed falsification artifact is actually cited and verifiable;
  - recorded unresolved semantics stay recorded.

What it cannot establish, by construction, and therefore reports as
MANUAL_REVIEW_REQUIRED rather than as a pass:
  - that a scope exclusion is legitimate (a cited identifier existing proves
    REFERENCE_EXISTS, never EXCLUSION_LEGITIMATE);
  - that an evaluation actually happened or was any good (a matching SHA-256
    proves ARTIFACT_IDENTITY_VERIFIED, never EVALUATION_QUALITY_VERIFIED);
  - that cited source coverage is complete when the source exposes no
    mechanically addressable identifiers;
  - that a negative/falsification set is adequate (FALSIFICATION_ARTIFACT_
    VERIFIED is an identity claim; FALSIFICATION_ADEQUACY is review territory).

Evidence basis for those boundaries (experiments, not opinion):
  analysis/gated-skill-lifecycle-pilot-r1/report.md
    -> retrospective N=1; gate set co-designed with its own case.
  analysis/gated-lifecycle-prospective-candidate-r1/report.md
    -> assumption DISCOVERY came from freeze-then-fresh-review, not from any
       gate; probe P5 satisfied the whole gate set by self-attestation.
  analysis/gated-lifecycle-self-attestation-closure-r1/report.md
    -> MECHANICAL_CLOSURE_VERDICT: PARTIAL. Of five candidate closures only
       C4 (evidence-plan consistency) reached SUFFICIENT, because its
       load-bearing claim IS an internal property. C1/C2/C3/C5 each proved
       identity, existence or a proxy while the required lie relocated one
       self-attested field deeper (A2B, A6C).
  Therefore: check D below is allowed to be load-bearing. Checks A, B, C, E,
  F are integrity checks whose semantic counterpart is explicitly deferred to
  a human reviewer. Do not restate this as a stronger claim.

Checks:
  A  candidate identity binding
  B  pinned source identity
  C  referenced artifact integrity
  D  evidence-plan internal consistency          (load-bearing)
  E  machine-addressable source references
  F  negative / falsification evidence completeness
  G  unresolved semantics preservation

Exit codes (mechanical only - these carry no fable-method lifecycle meaning):
  0 -> input valid and all mechanical integrity/completeness checks pass;
       warnings and manual semantic review may still remain
  1 -> one or more mechanical integrity/completeness requirements fail
  2 -> invalid input / unusable manifest / execution error

Python 3.9 compatible. Standard library only. Deterministic: no clock, no
randomness, no network, every emitted list sorted by a stable key.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# Stable vocabularies
# --------------------------------------------------------------------------

STATUS_INTEGRITY_COMPLETE = "INTEGRITY_COMPLETE"
STATUS_INTEGRITY_INCOMPLETE = "INTEGRITY_INCOMPLETE"
STATUS_INPUT_UNUSABLE = "INPUT_UNUSABLE"

EVIDENCE_INTEGRITY_VERIFIED = "VERIFIED"
EVIDENCE_INTEGRITY_PARTIAL = "PARTIAL_UNVERIFIABLE_IN_THIS_ENVIRONMENT"
EVIDENCE_INTEGRITY_NOT_ESTABLISHED = "NOT_ESTABLISHED"

# Candidate kinds. Only CODE_BACKED_DETERMINISTIC triggers check F, because
# only an implementation can be executed against a falsification fixture.
KIND_CODE_BACKED = "CODE_BACKED_DETERMINISTIC"
KIND_PROSE_ONLY = "PROSE_ONLY"
KIND_OTHER = "OTHER"
CANDIDATE_KINDS = (KIND_CODE_BACKED, KIND_PROSE_ONLY, KIND_OTHER)

# Manual-review tokens. Each one marks a claim this script deliberately does
# not make. None of them is a failure and none of them is a pass.
MR_SCOPE_JUSTIFICATION = "SEMANTIC_SCOPE_JUSTIFICATION_REQUIRES_REVIEW"
MR_EVALUATION_QUALITY = "EVALUATION_QUALITY_REQUIRES_REVIEW"
MR_SOURCE_COVERAGE = "SOURCE_COVERAGE_REQUIRES_REVIEW"
MR_FALSIFICATION_ADEQUACY = "FALSIFICATION_ADEQUACY_REQUIRES_REVIEW"
MR_UNRESOLVED_SEMANTICS = "UNRESOLVED_SEMANTICS_REQUIRES_REVIEW"

# Structural facts this script IS allowed to assert.
OK_REFERENCE_EXISTS = "REFERENCE_EXISTS"
OK_ARTIFACT_IDENTITY = "ARTIFACT_IDENTITY_VERIFIED"
OK_FALSIFICATION_ARTIFACT = "FALSIFICATION_ARTIFACT_VERIFIED"

EXECUTION_EXECUTED = "EXECUTED"
EXECUTION_DECLARED_ONLY = "DECLARED_ONLY"

ADDRESSABLE = "MACHINE_ADDRESSABLE"
NOT_ADDRESSABLE = "NOT_MACHINE_ADDRESSABLE"

PURPOSE_SCOPE_EXCLUSION = "SCOPE_EXCLUSION"

KIND_NEGATIVE_FALSIFICATION = "NEGATIVE_FALSIFICATION"

SEMANTIC_STATUS_OPEN = "OPEN"
SEMANTIC_STATUS_DEFERRED = "DEFERRED_OUT_OF_SCOPE"
SEMANTIC_STATUS_RESOLVED = "RESOLVED"
SEMANTIC_STATUSES = (
    SEMANTIC_STATUS_OPEN,
    SEMANTIC_STATUS_DEFERRED,
    SEMANTIC_STATUS_RESOLVED,
)


class ManifestError(Exception):
    """Input is unusable. Maps to exit 2, never to a check failure."""


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _require_mapping(value, where):
    if not isinstance(value, dict):
        raise ManifestError("%s must be an object" % where)
    return value


def _require_list(value, where):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ManifestError("%s must be an array" % where)
    return value


def _text(value):
    return value if isinstance(value, str) else ""


# Every character str.splitlines() treats as a line boundary. A manifest-
# controlled scalar (a candidate id, a locator, an error message built from
# a manifest-supplied fixture_id/item_id) must not be able to inject one of
# these into the line-oriented text renderer and forge a second logical
# line - in particular a second `PROMOTION_AUTHORIZED:` line. Escaped, never
# dropped, so content is not silently lost. A literal backslash is left
# alone, so an already-escaped-looking input and a real line break render
# the same way; that ambiguity is accepted because the invariant this
# defends is "no extra line", not "the text round-trips". JSON output is
# untouched by construction - json.dumps already escapes every one of these
# inside a string literal - so this is applied only to text-rendering
# copies, never to the `result` mapping itself.
_TEXT_LINE_BREAKS = {
    "\r": "\\r", "\n": "\\n", "\v": "\\v", "\f": "\\f",
    "\x1c": "\\x1c", "\x1d": "\\x1d", "\x1e": "\\x1e",
    "\x85": "\\x85", "\u2028": "\\u2028", "\u2029": "\\u2029",
}


def _text_safe(value):
    return "".join(_TEXT_LINE_BREAKS.get(ch, ch) for ch in value)


def _resolve(base_dir, raw_path):
    p = Path(raw_path)
    return p if p.is_absolute() else (base_dir / p)


def _condition_key(condition):
    """
    Canonical key for a fixture condition.

    Sorted by variable name so key equality is independent of authoring order.
    This is exact comparison over declared values only; it interprets nothing.
    """
    items = sorted((str(k), condition[k]) for k in condition)
    return json.dumps(items, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))


def _definition_key(fixture):
    """Canonical identity of a fixture definition, excluding its own id."""
    return json.dumps(
        {
            "rule_id": _text(fixture.get("rule_id")),
            "kind": _text(fixture.get("kind")),
            "condition": _condition_key(
                fixture.get("condition") if isinstance(fixture.get("condition"), dict) else {}
            ),
            "expected_outcome": _text(fixture.get("expected_outcome")),
        },
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )


class Report(object):
    """Accumulates findings. Every list is sorted before rendering."""

    def __init__(self):
        self.failed = []
        self.warnings = []
        self.manual_review = []
        self.verified = []
        self.integrity_failed = False
        self.integrity_unverifiable = False

    def fail(self, check_id, locator=""):
        self.failed.append(check_id + (":" + locator if locator else ""))

    def warn(self, token, locator=""):
        self.warnings.append(token + (":" + locator if locator else ""))

    def review(self, token, locator=""):
        self.manual_review.append(token + (":" + locator if locator else ""))

    def ok(self, token, locator=""):
        self.verified.append(token + (":" + locator if locator else ""))


# --------------------------------------------------------------------------
# Check A - candidate identity binding
# --------------------------------------------------------------------------

def check_a_candidate_identity(rep, candidate, evaluations):
    candidate_id = _text(candidate.get("candidate_id")).strip()
    if not candidate_id:
        rep.fail("A1_FROZEN_CANDIDATE_IDENTITY_MISSING")
        return ""

    if not _text(candidate.get("frozen_revision")).strip():
        rep.fail("A2_FROZEN_REVISION_MISSING", candidate_id)

    for ev in evaluations:
        ev_id = _text(ev.get("evaluation_id")) or "<unnamed>"
        bound = _text(ev.get("candidate_id")).strip()
        if not bound:
            rep.fail("A3_EVALUATION_CANDIDATE_IDENTITY_ABSENT", ev_id)
        elif bound != candidate_id:
            rep.fail("A4_EVALUATION_CANDIDATE_IDENTITY_MISMATCH",
                     "%s:%s!=%s" % (ev_id, bound, candidate_id))
    return candidate_id


# --------------------------------------------------------------------------
# Check B - pinned source identity
# --------------------------------------------------------------------------

def check_b_source_identity(rep, candidate, base_dir, source_refs_present):
    """
    Returns (source_text, addressability) where source_text is None when the
    source could not be read in this environment.

    A source identity is REQUIRED when the manifest cites source references;
    a candidate that cites no source is not forced to pin one.
    """
    source = candidate.get("source_identity")
    if source is None:
        if source_refs_present:
            rep.fail("B1_PINNED_SOURCE_IDENTITY_MISSING")
        return None, NOT_ADDRESSABLE
    source = _require_mapping(source, "candidate.source_identity")

    raw_path = _text(source.get("path")).strip()
    expected_sha = _text(source.get("sha256")).strip().lower()
    addressability = _text(source.get("addressability")).strip() or NOT_ADDRESSABLE
    if addressability not in (ADDRESSABLE, NOT_ADDRESSABLE):
        raise ManifestError(
            "candidate.source_identity.addressability must be one of %s"
            % ", ".join((ADDRESSABLE, NOT_ADDRESSABLE))
        )

    if not raw_path or not expected_sha:
        rep.fail("B1_PINNED_SOURCE_IDENTITY_MISSING", raw_path or "<no-path>")
        return None, addressability

    path = _resolve(base_dir, raw_path)
    if not path.is_file():
        # Validation environments legitimately differ. Unreadable is not a
        # mismatch, and it is also not a verification.
        rep.warn("B_SOURCE_UNVERIFIABLE_IN_THIS_ENVIRONMENT", raw_path)
        rep.integrity_unverifiable = True
        return None, addressability

    actual = _sha256_file(path)
    if actual != expected_sha:
        rep.fail("B2_SOURCE_SHA_MISMATCH", "%s:%s!=%s" % (raw_path, actual, expected_sha))
        rep.integrity_failed = True
        return None, addressability

    rep.ok("SOURCE_IDENTITY_VERIFIED", raw_path)
    try:
        return path.read_text(encoding="utf-8"), addressability
    except (UnicodeDecodeError, OSError):
        rep.warn("B_SOURCE_NOT_TEXT_ADDRESSABLE", raw_path)
        return None, addressability


# --------------------------------------------------------------------------
# Check C - referenced artifact integrity
# --------------------------------------------------------------------------

def check_c_artifact_integrity(rep, evaluations, base_dir, candidate_id):
    """
    Verifies artifact IDENTITY only. Every verified artifact also raises
    EVALUATION_QUALITY_REQUIRES_REVIEW, because the closure experiment built
    two artifacts that hashed exactly as cited - one a real execution
    transcript, one typed by hand asserting PASS - and the integrity verdicts
    were identical. A SHA-256 cannot tell those apart.
    """
    verified_ids = set()
    for ev in evaluations:
        ev_id = _text(ev.get("evaluation_id")) or "<unnamed>"
        artifact = ev.get("artifact")
        if not isinstance(artifact, dict):
            rep.fail("C1_EVALUATION_ARTIFACT_NOT_CITED", ev_id)
            rep.integrity_failed = True
            continue

        raw_path = _text(artifact.get("path")).strip()
        expected_sha = _text(artifact.get("sha256")).strip().lower()
        if not raw_path or not expected_sha:
            rep.fail("C1_EVALUATION_ARTIFACT_NOT_CITED", ev_id)
            rep.integrity_failed = True
            continue

        path = _resolve(base_dir, raw_path)
        if not path.is_file():
            rep.fail("C2_EVALUATION_ARTIFACT_MISSING", "%s:%s" % (ev_id, raw_path))
            rep.integrity_failed = True
            continue

        actual = _sha256_file(path)
        if actual != expected_sha:
            rep.fail("C3_EVALUATION_ARTIFACT_SHA_MISMATCH",
                     "%s:%s:%s!=%s" % (ev_id, raw_path, actual, expected_sha))
            rep.integrity_failed = True
            continue

        rep.ok(OK_ARTIFACT_IDENTITY, "%s:%s" % (ev_id, raw_path))
        rep.review(MR_EVALUATION_QUALITY, ev_id)
        verified_ids.add(ev_id)

        if _text(ev.get("kind")) == KIND_NEGATIVE_FALSIFICATION \
                and _text(artifact.get("execution")) == EXECUTION_EXECUTED:
            rep.ok(OK_FALSIFICATION_ARTIFACT, ev_id)
            rep.review(MR_FALSIFICATION_ADEQUACY, ev_id)

    if candidate_id and not evaluations:
        rep.warn("C_NO_EVALUATION_CITED", candidate_id)
    return verified_ids


# --------------------------------------------------------------------------
# Check D - evidence-plan internal consistency (load-bearing)
# --------------------------------------------------------------------------

def check_d_evidence_plan(rep, plan, rules):
    """
    The one check whose load-bearing claim is an internal property, so it is
    allowed to be load-bearing (closure experiment: C4 SUFFICIENT, surviving a
    deliberate prose re-key evasion once the trigger-variable domain was
    enforced). Residual, stated rather than hidden: an author who misdeclares
    the trigger domain itself moves the defect into fixture misdeclaration,
    which this check does not address.
    """
    vocabulary = _require_list(plan.get("outcome_vocabulary"), "evidence_plan.outcome_vocabulary")
    vocab = set(v for v in vocabulary if isinstance(v, str))
    fixtures = _require_list(plan.get("fixtures"), "evidence_plan.fixtures")

    by_condition = {}
    by_fixture_id = {}

    for fixture in fixtures:
        fixture = _require_mapping(fixture, "evidence_plan.fixtures[]")
        fid = _text(fixture.get("fixture_id")).strip()
        rule_id = _text(fixture.get("rule_id")).strip()
        outcome = _text(fixture.get("expected_outcome")).strip()
        condition = fixture.get("condition")
        if not fid:
            raise ManifestError("evidence_plan.fixtures[] requires fixture_id")
        if not isinstance(condition, dict):
            raise ManifestError("fixture %s requires an object condition" % fid)

        if vocab and outcome not in vocab:
            rep.fail("D1_OUTCOME_OUTSIDE_DECLARED_VOCABULARY", "%s:%s" % (fid, outcome or "<empty>"))

        # Re-key evasion defence: conditions may only speak the rule's own
        # declared trigger variables, so the same real state cannot be
        # described under a second name to hide a contradiction.
        declared = rules.get(rule_id)
        if isinstance(declared, list):
            domain = set(v for v in declared if isinstance(v, str))
            for var in sorted(str(k) for k in condition):
                if var not in domain:
                    rep.fail("D2_CONDITION_VARIABLE_OUTSIDE_DECLARED_DOMAIN",
                             "%s:%s:%s" % (rule_id, fid, var))

        ckey = (rule_id, _condition_key(condition))
        by_condition.setdefault(ckey, {}).setdefault(outcome, []).append(fid)
        by_fixture_id.setdefault(fid, {}).setdefault(_definition_key(fixture), []).append(fid)

    for (rule_id, ckey) in sorted(by_condition):
        outcomes = by_condition[(rule_id, ckey)]
        if len(outcomes) > 1:
            rep.fail(
                "D3_INCOMPATIBLE_OUTCOMES_AT_IDENTICAL_CONDITION",
                "%s:%s:%s" % (rule_id, ckey, ",".join(sorted(outcomes))),
            )

    for fid in sorted(by_fixture_id):
        if len(by_fixture_id[fid]) > 1:
            rep.fail("D4_FIXTURE_ID_CONFLICTING_DEFINITIONS", fid)


# --------------------------------------------------------------------------
# Check E - machine-addressable source references
# --------------------------------------------------------------------------

def check_e_source_references(rep, refs, source_text, addressability, identifier_prefix):
    """
    Existence of a cited identifier is REFERENCE_EXISTS and nothing else. The
    closure experiment's A2 cited a real rule about cleanup to exclude a
    decision that was really about a different clause; every strengthening
    relocated the lie into another self-attested field (A2B). So relevance is
    always deferred to review.
    """
    if not refs:
        return

    if addressability != ADDRESSABLE or source_text is None:
        # No fabricated completeness: say what is unproven and why.
        rep.review(MR_SOURCE_COVERAGE,
                   "SOURCE_NOT_MECHANICALLY_ADDRESSABLE"
                   if addressability != ADDRESSABLE else "SOURCE_UNREADABLE_HERE")
        for ref in refs:
            ref = _require_mapping(ref, "source_references[]")
            if _text(ref.get("purpose")) == PURPOSE_SCOPE_EXCLUSION:
                rep.review(MR_SCOPE_JUSTIFICATION, _text(ref.get("reference_id")) or "<unnamed>")
        return

    present = _extract_identifiers(source_text, identifier_prefix)
    for ref in refs:
        ref = _require_mapping(ref, "source_references[]")
        ref_id = _text(ref.get("reference_id")) or "<unnamed>"
        cited = _text(ref.get("cited_identifier")).strip()
        if not cited:
            rep.fail("E1_SOURCE_REFERENCE_IDENTIFIER_ABSENT", ref_id)
            continue
        if cited not in present:
            rep.fail("E2_CITED_IDENTIFIER_NOT_FOUND_IN_SOURCE", "%s:%s" % (ref_id, cited))
            continue
        rep.ok(OK_REFERENCE_EXISTS, "%s:%s" % (ref_id, cited))
        if _text(ref.get("purpose")) == PURPOSE_SCOPE_EXCLUSION:
            rep.review(MR_SCOPE_JUSTIFICATION, "%s:%s" % (ref_id, cited))

    # Existence of every cited identifier still says nothing about whether the
    # cited set covers the source. That remains a review question.
    rep.review(MR_SOURCE_COVERAGE, "CITED_SET_VS_SOURCE_EXTENT")


def _extract_identifiers(source_text, identifier_prefix):
    """
    Collect identifiers the source itself exposes, as leading tokens of the
    form `<prefix><token>` at the start of a line (optionally after a comment
    marker). Deliberately narrow: this only works on a source that writes its
    own identifiers, which is a property of a given source file and never a
    general guarantee.
    """
    found = set()
    for line in source_text.split("\n"):
        stripped = line.strip()
        for marker in ("#", "//", "--", "*"):
            if stripped.startswith(marker):
                stripped = stripped[len(marker):].strip()
                break
        if not stripped.startswith(identifier_prefix):
            continue
        token = stripped[len(identifier_prefix):].split()
        if not token:
            continue
        found.add(identifier_prefix + token[0].rstrip(":.,;)"))
    return found


# --------------------------------------------------------------------------
# Check F - negative / falsification evidence completeness
# --------------------------------------------------------------------------

def check_f_falsification(rep, candidate, claims, evaluations, verified_ids):
    """
    For a code-backed deterministic candidate that CLAIMS negative/
    falsification evidence, require a cited EXECUTED artifact. Declared
    negative fixtures alone are not that evidence: the closure experiment's
    C5 reported an inadequate set ADEQUATE_BY_SHAPE, and only executing the
    mutation showed the mutant survived every declared negative case.
    """
    if _text(candidate.get("candidate_kind")) != KIND_CODE_BACKED:
        return
    if claims.get("negative_falsification_evidence") is not True:
        return

    executed = []
    for ev in evaluations:
        ev_id = _text(ev.get("evaluation_id")) or "<unnamed>"
        artifact = ev.get("artifact") if isinstance(ev.get("artifact"), dict) else {}
        if _text(ev.get("kind")) != KIND_NEGATIVE_FALSIFICATION:
            continue
        if _text(artifact.get("execution")) != EXECUTION_EXECUTED:
            rep.warn("F_FALSIFICATION_EVALUATION_NOT_EXECUTED", ev_id)
            continue
        if ev_id in verified_ids:
            executed.append(ev_id)

    if not executed:
        rep.fail("F1_CLAIMED_FALSIFICATION_EVIDENCE_HAS_NO_EXECUTED_ARTIFACT",
                 _text(candidate.get("candidate_id")))
    else:
        # Identity is settled; adequacy is not, and must not be folded in.
        rep.review(MR_FALSIFICATION_ADEQUACY, "SET_ADEQUACY_NOT_MECHANICALLY_DEMONSTRATED")


# --------------------------------------------------------------------------
# Check G - unresolved semantics preservation
# --------------------------------------------------------------------------

def check_g_unresolved_semantics(rep, items):
    """
    Recorded unresolved items stay recorded. Internal metadata consistency is
    never grounds to reinterpret an item as resolved.
    """
    for item in items:
        item = _require_mapping(item, "unresolved_semantics[]")
        item_id = _text(item.get("item_id")) or "<unnamed>"
        status = _text(item.get("status")).strip()
        if status not in SEMANTIC_STATUSES:
            raise ManifestError(
                "unresolved_semantics[%s].status must be one of %s"
                % (item_id, ", ".join(SEMANTIC_STATUSES))
            )
        if status == SEMANTIC_STATUS_RESOLVED:
            if not _text(item.get("resolution_evidence_ref")).strip():
                rep.fail("G1_SEMANTIC_ITEM_MARKED_RESOLVED_WITHOUT_EVIDENCE", item_id)
            else:
                rep.review(MR_UNRESOLVED_SEMANTICS, "RESOLUTION_CLAIM:" + item_id)
        elif status == SEMANTIC_STATUS_OPEN:
            rep.review(MR_UNRESOLVED_SEMANTICS, "OPEN:" + item_id)
        else:
            rep.review(MR_UNRESOLVED_SEMANTICS, "DEFERRED:" + item_id)


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def evaluate(manifest, base_dir):
    manifest = _require_mapping(manifest, "manifest")
    rep = Report()

    candidate = _require_mapping(manifest.get("candidate") or {}, "candidate")
    kind = _text(candidate.get("candidate_kind")).strip()
    if kind and kind not in CANDIDATE_KINDS:
        raise ManifestError(
            "candidate.candidate_kind must be one of %s" % ", ".join(CANDIDATE_KINDS)
        )

    evaluations = _require_list(manifest.get("evaluations"), "evaluations")
    for ev in evaluations:
        _require_mapping(ev, "evaluations[]")
    refs = _require_list(manifest.get("source_references"), "source_references")
    plan = _require_mapping(manifest.get("evidence_plan") or {}, "evidence_plan")
    claims = _require_mapping(manifest.get("claims") or {}, "claims")
    semantics = _require_list(manifest.get("unresolved_semantics"), "unresolved_semantics")
    rules = _require_mapping(manifest.get("rule_trigger_domains") or {}, "rule_trigger_domains")
    identifier_prefix = _text(
        (candidate.get("source_identity") or {}).get("identifier_prefix")
        if isinstance(candidate.get("source_identity"), dict) else ""
    ) or "L"

    candidate_id = check_a_candidate_identity(rep, candidate, evaluations)
    source_text, addressability = check_b_source_identity(
        rep, candidate, base_dir, bool(refs))
    verified_ids = check_c_artifact_integrity(rep, evaluations, base_dir, candidate_id)
    check_d_evidence_plan(rep, plan, rules)
    check_e_source_references(rep, refs, source_text, addressability, identifier_prefix)
    check_f_falsification(rep, candidate, claims, evaluations, verified_ids)
    check_g_unresolved_semantics(rep, semantics)

    failed = sorted(set(rep.failed))
    if rep.integrity_failed:
        integrity = EVIDENCE_INTEGRITY_NOT_ESTABLISHED
    elif rep.integrity_unverifiable:
        integrity = EVIDENCE_INTEGRITY_PARTIAL
    else:
        integrity = EVIDENCE_INTEGRITY_VERIFIED

    return {
        "STATUS": STATUS_INTEGRITY_INCOMPLETE if failed else STATUS_INTEGRITY_COMPLETE,
        "CANDIDATE_ID": candidate_id or "UNRESOLVED",
        "FAILED_CHECKS": failed,
        "WARNINGS": sorted(set(rep.warnings)),
        "MANUAL_REVIEW_REQUIRED": sorted(set(rep.manual_review)),
        "EVIDENCE_INTEGRITY": integrity,
        "STRUCTURAL_FACTS_VERIFIED": sorted(set(rep.verified)),
        # Constant. This script is not a promotion authority, and the
        # self-attestation closure experiment concluded the bypass cannot be
        # fully closed mechanically.
        "PROMOTION_AUTHORIZED": False,
    }


def render(result, as_json):
    if as_json:
        return json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False)
    lines = [
        "STATUS: " + result["STATUS"],
        "CANDIDATE_ID: " + _text_safe(result["CANDIDATE_ID"]),
        "EVIDENCE_INTEGRITY: " + result["EVIDENCE_INTEGRITY"],
        "PROMOTION_AUTHORIZED: false",
        "FAILED_CHECKS: " + (str(len(result["FAILED_CHECKS"])) if result["FAILED_CHECKS"] else "NONE"),
    ]
    for item in result["FAILED_CHECKS"]:
        lines.append("  FAIL " + _text_safe(item))
    lines.append("WARNINGS: " + (str(len(result["WARNINGS"])) if result["WARNINGS"] else "NONE"))
    for item in result["WARNINGS"]:
        lines.append("  WARN " + _text_safe(item))
    lines.append("MANUAL_REVIEW_REQUIRED: " + (
        str(len(result["MANUAL_REVIEW_REQUIRED"])) if result["MANUAL_REVIEW_REQUIRED"] else "NONE"))
    for item in result["MANUAL_REVIEW_REQUIRED"]:
        lines.append("  REVIEW " + _text_safe(item))
    lines.append("STRUCTURAL_FACTS_VERIFIED: " + (
        str(len(result["STRUCTURAL_FACTS_VERIFIED"])) if result["STRUCTURAL_FACTS_VERIFIED"] else "NONE"))
    for item in result["STRUCTURAL_FACTS_VERIFIED"]:
        lines.append("  OK " + _text_safe(item))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Mechanical evidence integrity/completeness checker. "
                    "Never a promotion authority."
    )
    parser.add_argument("manifest", help="path to a candidate evidence manifest JSON file")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument(
        "--base-dir", default=None,
        help="root for relative artifact/source paths (default: the manifest's directory)",
    )
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest)
    base_dir = Path(args.base_dir) if args.base_dir else manifest_path.parent

    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        result = evaluate(manifest, base_dir)
    except (OSError, ValueError, ManifestError, TypeError, KeyError) as exc:
        payload = {
            "STATUS": STATUS_INPUT_UNUSABLE,
            "CANDIDATE_ID": "UNRESOLVED",
            "FAILED_CHECKS": [],
            "WARNINGS": [],
            "MANUAL_REVIEW_REQUIRED": [],
            "EVIDENCE_INTEGRITY": EVIDENCE_INTEGRITY_NOT_ESTABLISHED,
            "MESSAGE": "%s: %s" % (type(exc).__name__, exc),
            "PROMOTION_AUTHORIZED": False,
        }
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
        else:
            print("STATUS: " + STATUS_INPUT_UNUSABLE)
            print("CANDIDATE_ID: UNRESOLVED")
            print("EVIDENCE_INTEGRITY: " + EVIDENCE_INTEGRITY_NOT_ESTABLISHED)
            print("PROMOTION_AUTHORIZED: false")
            print("MESSAGE: " + _text_safe(payload["MESSAGE"]))
        return 2

    print(render(result, args.json))
    return 1 if result["FAILED_CHECKS"] else 0


if __name__ == "__main__":
    sys.exit(main())
