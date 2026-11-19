"""Read-only preflight for a future frozen exploration. Never invokes a model.

The check verifies a registration and supplied evidence, not the truth of an
oracle or independence of its source events. Those require the bank review.
"""
import argparse
import hashlib
import json
from pathlib import Path
from decimal import Decimal
import decision_engine as engine

MIN_ITEMS = 30
MIN_FAMILIES = 6
# Stage-0's independently reviewed policy. A different policy starts a new epoch.
FROZEN_POLICY_SHA256 = "e13a10b4cf1fa02043e08104ee772ec15a716da09c7fabfde47dc9c5069b20ee"


def artifact(record):
    if not isinstance(record, dict) or not record.get("ref") or not record.get("sha256"):
        raise ValueError("missing pinned artifact")
    path=Path(record["ref"])
    if hashlib.sha256(path.read_bytes()).hexdigest()!=record["sha256"]:
        raise ValueError("artifact changed after registration")
    return path


def assess(protocol, bank=None, interface=None, offline=None, bank_review=None):
    reasons=[]
    if protocol.get("status")!="FROZEN": reasons.append("PROTOCOL_NOT_FROZEN")
    if protocol.get("selection_basis")!="DEPLOYMENT_OBSERVABLE_FEATURES": reasons.append("SELECTION_RULE_UNFROZEN")
    if not protocol.get("instruction_surface"): reasons.append("FABLE_INSTRUCTION_CLOSURE_UNFROZEN")
    if not protocol.get("reference_policy"): reasons.append("REFERENCE_POLICY_UNFROZEN")
    if protocol.get("target_runs")!=5: reasons.append("REGISTERED_R1_RUN_COUNT_MISMATCH")
    if bank is None or bank.get("purpose")!="FORMAL_DECISION_EXPLORATION":
        reasons.append("FORMAL_BANK_UNAVAILABLE")
    else:
        rows=bank.get("items",[])
        ids=[r.get("item_id") for r in rows]
        if not all(isinstance(i,str) and i for i in ids) or len(set(ids))!=len(ids):
            reasons.append("BANK_ITEM_IDENTITY_INVALID")
        if len(rows)<MIN_ITEMS: reasons.append("EXPLORATION_ITEM_FLOOR_NOT_MET")
        families={r.get("family") for r in rows if r.get("family")}
        if len(families)<MIN_FAMILIES: reasons.append("EXPLORATION_FAMILY_FLOOR_NOT_MET")
        lineage={}
        for row in rows:
            if row.get("kind")!="DECISION" or row.get("split")!="EXPLORATION":
                reasons.append("BANK_KIND_OR_SPLIT_INVALID");break
            source=row.get("lineage_ref")
            if not source or not row.get("source_ref") or not row.get("family"):
                reasons.append("BANK_LINEAGE_UNVERIFIED");break
            if source in lineage and lineage[source]!=row["family"]:
                reasons.append("SAME_LINEAGE_SPLIT_INTO_MULTIPLE_FAMILIES");break
            lineage[source]=row["family"]
            if row.get("used_for_outcome_selection") is not False:
                reasons.append("OUTCOME_BASED_SELECTION_NOT_EXCLUDED");break
    if bank_review is None or bank_review.get("verdict")!="VERIFIED" or bank_review.get("scope")!="FORMAL_BANK_AND_ORACLE":
        reasons.append("FORMAL_BANK_REVIEW_MISSING")
    receipt=interface if isinstance(interface,dict) else {}
    identity_present=all(isinstance(value,str) and value.strip() for value in (
        protocol.get("model"),protocol.get("effort"),protocol.get("cost_basis"),
        receipt.get("model_observed"),receipt.get("effort_requested"),receipt.get("cost_basis")))
    if (interface is None or not identity_present or interface.get("status")!="BENCHMARK_READY" or
        interface.get("mode")!="LIVE" or interface.get("model_observed")!=protocol.get("model") or
        interface.get("effort_requested")!=protocol.get("effort") or
        interface.get("generation_cap_verified") is not True or
        interface.get("stateless_isolation_verified") is not True or
        interface.get("usage_accounting_verified") is not True or
        interface.get("cost_basis")!=protocol.get("cost_basis")):
        reasons.append("LIVE_INTERFACE_EXIT_NOT_MET")
    if (offline is None or offline.get("status")!="PASS" or
        offline.get("scope")!="OFFLINE_ENGINEERING" or
        offline.get("source_identity_verified") is not True):
        reasons.append("OFFLINE_INTEGRATION_IDENTITY_UNVERIFIED")
    try:
        cap=Decimal(str(protocol["attempt_reservation_usd"]))
        budget=Decimal(str(protocol["total_reservation_usd"]))
        if not cap.is_finite() or not budget.is_finite() or cap<=0 or budget<=0:
            raise ValueError("bad cost cap")
        count=len(bank.get("items",[])) if bank else MIN_ITEMS
        required=cap*2*count*5
        if budget<required: reasons.append("REGISTERED_WHOLE_ROUNDS_NOT_RESERVED")
    except (KeyError,ValueError,ArithmeticError):
        required=None;reasons.append("TOTAL_COST_CAP_UNFROZEN")
    return {"status":"BLOCKED" if reasons else "ELIGIBLE_FOR_REVIEWED_LAUNCH",
            "generation_calls":0,"reasons":reasons,"minimum_items":MIN_ITEMS,"minimum_families":MIN_FAMILIES,
            "required_reservation_usd":None if required is None else str(required),
            "is_power_guarantee":False,"is_model_benchmark_result":False}


def check_file(path):
    protocol=engine.read_json(path)
    if not isinstance(protocol,dict):
        return {"status":"BLOCKED","generation_calls":0,"reasons":["PROTOCOL_SHAPE_INVALID"]}
    loaded={};errors=[]
    try:
        artifact(protocol.get("reference_policy"))
        actual=Path(__file__).resolve().parent.parent/"fable-ensemble3-r1"/"ensemble_scoring.py"
        if (protocol["reference_policy"]["sha256"]!=FROZEN_POLICY_SHA256 or
            hashlib.sha256(actual.read_bytes()).hexdigest()!=FROZEN_POLICY_SHA256):
            raise ValueError("registered policy is not the reviewed R2 policy")
    except (ValueError,TypeError,KeyError,OSError): errors.append("REFERENCE_POLICY_IDENTITY_INVALID")
    for name in ("bank","interface","offline","bank_review"):
        record=protocol.get(name)
        if record is None: loaded[name]=None;continue
        try:
            loaded[name]=engine.read_json(artifact(record))
            if not isinstance(loaded[name],dict): raise ValueError("artifact must be an object")
        except (ValueError,TypeError,KeyError,OSError):
            loaded[name]=None;errors.append(name.upper()+"_ARTIFACT_INVALID")
    if loaded.get("offline"):
        expected=loaded["offline"].get("source_hashes",{})
        here=Path(__file__).resolve().parent
        observed={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in here.glob("*.py")}
        loaded["offline"]["source_identity_verified"]=expected==observed
    if loaded.get("bank_review"):
        bank_identity=(protocol.get("bank") or {}).get("sha256")
        if not bank_identity or loaded["bank_review"].get("bank_manifest_sha256")!=bank_identity:
            errors.append("BANK_REVIEW_IDENTITY_INVALID")
    if protocol.get("instruction_surface"):
        try: artifact(protocol["instruction_surface"])
        except (ValueError,TypeError,KeyError,OSError): errors.append("INSTRUCTION_SURFACE_IDENTITY_INVALID")
        if (loaded.get("interface") or {}).get("instruction_sha256")!=protocol["instruction_surface"].get("sha256"):
            errors.append("INTERFACE_INSTRUCTION_IDENTITY_INVALID")
    if loaded.get("bank"):
        try:
            # Checker can read private labels for validation; model and aggregator cannot.
            for row in loaded["bank"]["items"]:
                public=engine.read_json(artifact({"ref":row["public_ref"],"sha256":row["public_sha256"]}))
                private=engine.read_json(artifact({"ref":row["oracle_ref"],"sha256":row["oracle_sha256"]}))
                engine.validate_public(public)
                identity_keys=("item_id","family","kind","split","source_ref")
                if (any(public.get(key)!=row.get(key) for key in identity_keys) or
                    private["item_id"]!=row["item_id"] or private["public_sha256"]!=row["public_sha256"]):
                    raise ValueError("mismatched public/private binding")
        except (ValueError,TypeError,KeyError,OSError): errors.append("BANK_BINDING_INVALID")
    try: result=assess(protocol,**loaded)
    except (AttributeError,TypeError,KeyError,ValueError):
        result={"status":"BLOCKED","generation_calls":0,"reasons":["REGISTRATION_SHAPE_INVALID"]}
    result["reasons"].extend(errors)
    if errors: result["status"]="BLOCKED"
    return result


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("protocol")
    result=check_file(parser.parse_args().protocol)
    print(json.dumps(result,indent=2))
    raise SystemExit(0 if not result["reasons"] else 2)
