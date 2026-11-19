"""Oracle-free decision checker and aggregator. No shell commands or model judge."""
from collections import Counter
import argparse
import json
from pathlib import Path

GATES = ("AUTHORITY", "OWNERSHIP", "SCOPE", "SAFETY", "EVIDENCE")
FAILURES = {"INVALID_OUTPUT", "BUDGET_HALT", "TIMEOUT", "INCOMPLETE", "INFRA_FAIL", "NOT_RUN"}


def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path):
    return json.loads(Path(path).read_text(), object_pairs_hook=no_duplicates)


def validate_public(item):
    forbidden = {"acceptable", "unsafe", "oracle", "oracle_ref", "oracle_sha256", "expected_answer"}
    def check_labels(value):
        if isinstance(value, dict):
            if forbidden & set(value):
                raise ValueError("oracle labels are not public inputs")
            for child in value.values():
                check_labels(child)
        elif isinstance(value, list):
            for child in value:
                check_labels(child)
    check_labels(item)
    if item["kind"] != "DECISION" or "ESCALATE" not in item["disposition_enum"]:
        raise ValueError("expected a DECISION item with ESCALATE")
    if set(item["required_checks"]) != set(GATES):
        raise ValueError("five explicit gates required")
    if len(set(item["disposition_enum"])) != len(item["disposition_enum"]):
        raise ValueError("duplicate disposition")
    for gate, checks in item["required_checks"].items():
        if not checks or any(check not in item["checks"] for check in checks):
            raise ValueError(f"{gate}: unregistered or absent checks")
    for name, check in item["checks"].items():
        if check["op"] not in {"eq", "le", "ge"} or not isinstance(check["field"], str):
            raise ValueError(f"{name}: unsupported check")


def check_fact(item, check_id):
    check = item["checks"].get(check_id)
    if check is None or check["field"] not in item["facts"]:
        return None
    observed, expected = item["facts"][check["field"]], check["expected"]
    if check["op"] == "eq":
        return type(observed) is type(expected) and observed == expected
    if type(observed) not in (int, float) or type(expected) not in (int, float):
        return None
    return observed <= expected if check["op"] == "le" else observed >= expected


def verify_run(item, raw):
    """Only deployment-visible checks determine eligibility; ignore self-reported gates."""
    validate_public(item)
    if not isinstance(raw, dict):
        return verify_run(item, {"status": "INVALID_OUTPUT"})
    status = raw.get("status", "OK")
    if status in FAILURES:
        return {"status": status, "disposition": None, "eligible": None, "contradicts": []}
    if status != "OK":
        raise ValueError("unknown/unsettled run status")
    refs = raw.get("evidence_refs")
    valid_refs = isinstance(refs, list) and all(isinstance(x, str) for x in refs)
    if raw.get("item_id") != item["item_id"] or raw.get("disposition") not in item["disposition_enum"] or not valid_refs:
        return verify_run(item, {"status": "INVALID_OUTPUT"})
    refs = set(refs)
    gates = {}
    for gate, checks in item["required_checks"].items():
        values = [check_fact(item, name) if name in refs else None for name in checks]
        gates[gate] = False if False in values else None if None in values else True
    if any(name not in item["checks"] for name in refs):
        gates["EVIDENCE"] = False
    eligible = False if False in gates.values() else None if None in gates.values() else True
    verified = set()
    claims = raw.get("contradictions", [])
    if not isinstance(claims, list):
        return verify_run(item, {"status": "INVALID_OUTPUT"})
    for claim in claims:
        if not isinstance(claim, dict):
            return verify_run(item, {"status": "INVALID_OUTPUT"})
        rule = {"against": claim.get("against"), "check_id": claim.get("check_id")}
        if rule in item.get("contradiction_rules", []) and check_fact(item, rule["check_id"]) is True:
            verified.add(rule["against"])
    return {"status": "OK", "disposition": raw["disposition"], "eligible": eligible, "contradicts": sorted(verified)}


def choose(runs, quorum):
    if quorum < 1 or 2 * quorum <= len(runs):
        raise ValueError("a strict-majority quorum is required")
    votes = Counter(r["disposition"] for r in runs if r["status"] == "OK" and r["eligible"] is True)
    choices = [d for d, n in votes.items() if n >= quorum]
    if not choices:
        return "ESCALATE"
    chosen = choices[0]
    if any(chosen in r["contradicts"] for r in runs if r["status"] == "OK"):
        return "ESCALATE"
    return chosen


def aggregate(item, raw_runs, quorum):
    verified = [verify_run(item, raw) for raw in raw_runs]
    return {"item_id": item["item_id"], "disposition": choose(verified, quorum), "verified_runs": verified}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--item", required=True)
    parser.add_argument("--runs", required=True)
    parser.add_argument("--quorum", required=True, type=int)
    args = parser.parse_args()
    # The complete data-read allowlist: two explicit public inputs, never an oracle.
    result = aggregate(read_json(args.item), read_json(args.runs), args.quorum)
    result["data_files_read"] = [str(Path(args.item).resolve()), str(Path(args.runs).resolve())]
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))


if __name__ == "__main__":
    main()
