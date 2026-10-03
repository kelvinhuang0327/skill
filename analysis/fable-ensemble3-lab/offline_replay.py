"""SIMULATED integration exercise, never a model benchmark or provider adapter."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import sys
from budget_ledger import Ledger, BudgetStop
import decision_engine as engine

SCORER = Path(__file__).resolve().parent.parent/"fable-ensemble3-r1"/"ensemble_scoring.py"


def scorer():
    spec = importlib.util.spec_from_file_location("ensemble3_reference_scoring", SCORER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module; spec.loader.exec_module(module)
    return module


def load_bank(manifest_path):
    manifest = engine.read_json(manifest_path)
    if manifest["purpose"] != "ENGINEERING_ONLY":
        raise ValueError("offline engineering runner cannot start a formal experiment")
    public, labels = {}, {}
    for row in manifest["items"]:
        for ref, digest in [("public_ref", "public_sha256"), ("oracle_ref", "oracle_sha256")]:
            if hashlib.sha256(Path(row[ref]).read_bytes()).hexdigest() != row[digest]:
                raise ValueError("fixture or oracle changed after freeze")
        item = engine.read_json(row["public_ref"]); engine.validate_public(item)
        label = engine.read_json(row["oracle_ref"])
        if label["item_id"] != row["item_id"] or label["public_sha256"] != row["public_sha256"]:
            raise ValueError("oracle does not bind this fixture")
        public[row["item_id"]] = item; labels[row["item_id"]] = label
    return public, labels


def run_rounds(public, provider, ledger, rounds=5, seed=6302):
    if rounds < 3:
        raise ValueError("at least three rounds required")
    if ledger.state["slots"]:
        raise ValueError("existing execution must be replayed; never relaunch it implicitly")
    planned = min(rounds, int(ledger.budget // (len(public)*2*ledger.cap)))
    if planned < 3:
        return {item_id:[{"status":"NOT_RUN"}]*rounds for item_id in public}
    # No cherry-picking: registered list remains fixed, including NOT_RUN slots.
    runs = {item_id: [] for item_id in public}
    order = sorted(public); random.Random(seed).shuffle(order)
    for round_index in range(planned):
        try: ledger.reserve_round(round_index, order)
        except BudgetStop:
            for item_id in order:
                runs[item_id].extend([{"status": "NOT_RUN"}]*(rounds-round_index))
            break
        for item_id in order:
            slot = f"{item_id}:{round_index}"
            while True:
                ledger.begin(slot)
                try:
                    receipt = provider(public[item_id], round_index, ledger.state["slots"][slot]["attempts"])
                except TimeoutError:
                    receipt = {"status": "TIMEOUT", "cost": None}
                except (OSError, ConnectionError):
                    receipt = {"status": "INFRA_ERROR", "cost": None}
                except Exception:
                    receipt = {"status": "INCOMPLETE", "cost": None}
                status = receipt.get("status", "INVALID_OUTPUT")
                if status == "OK":
                    try: verified = engine.verify_run(public[item_id], receipt.get("output", {}))
                    except (ValueError, TypeError, KeyError): verified = engine.verify_run(public[item_id], {"status": "INVALID_OUTPUT"})
                    status = verified["status"]
                elif status == "INFRA_ERROR":
                    verified = None
                else:
                    if status not in engine.FAILURES: status = "INVALID_OUTPUT"
                    verified = engine.verify_run(public[item_id], {"status": status})
                state = ledger.finish(slot, status, receipt.get("cost"), verified)
                if state == "RETRY_READY": continue
                if verified is None: verified = engine.verify_run(public[item_id], {"status": "INFRA_FAIL"})
                runs[item_id].append(verified)
                break
    for item_id in order:
        runs[item_id].extend([{"status":"NOT_RUN"}]*(rounds-len(runs[item_id])))
    return runs


def replay_saved(public, ledger, rounds=5):
    result = {}
    for item_id in public:
        rows=[]
        for index in range(rounds):
            slot=ledger.state["slots"].get(f"{item_id}:{index}")
            if slot is None or slot["state"] == "RESERVED": rows.append({"status":"NOT_RUN"})
            elif slot["state"] != "FINAL": raise ValueError("unsettled slot: explicitly recover uncertain starts before replay")
            else: rows.append(slot["verified_run"])
        result[item_id]=rows
    return result


def score(public, labels, runs):
    es = scorer()
    items = [es.Item(i, p["family"], "DECISION", frozenset(p["disposition_enum"]),
                     frozenset(labels[i]["acceptable"]), frozenset(labels[i]["unsafe"])) for i,p in public.items()]
    verified = {i:[es.Run(status=r["status"], disposition=r.get("disposition"), eligible=r.get("eligible"),
                         contradicts=frozenset(r.get("contradicts", []))) for r in rows] for i,rows in runs.items()}
    pool = es.evaluate_pool(items, verified)
    names = ["single", "single_gated", "gated", "cov3", "disagreement", "checker_effect", "aggregation_effect", "crit_reduction"]
    intervals = es.pool_intervals(pool, names)
    return {"kind": "SIMULATED_ENGINEERING_ONLY", "statistical_claims_allowed": False,
            "item_count": pool.n_items, "family_count": pool.n_families, "unresolved": pool.unresolved,
            "always_escalate_success": sum("ESCALATE" in labels[i]["acceptable"] for i in public)/len(public),
            "diagnostic_intervals": {name:None if iv is None else {"estimate":iv.est,"lo":iv.lo,"hi":iv.hi,"families":iv.families}
                                     for name,iv in intervals.items()}, "classifier_diagnostic_only": es.classify(intervals)}


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--manifest",required=True); parser.add_argument("--output",required=True)
    args=parser.parse_args(); public,labels=load_bank(args.manifest)
    destination=Path(args.output); destination.mkdir(parents=True,exist_ok=True)
    if (destination/"ledger.json").exists(): raise ValueError("do not relaunch an existing run; replay saved records instead")
    ledger=Ledger(destination/"ledger.json",budget=80,attempt_cap=1)
    def fixture_provider(item, index, attempt):
        # This simulated provider is intentionally informed by the fixture oracle; it is
        # a pipeline positive control, not an independent agent, model score or evidence of uplift.
        return {"status":"OK","cost":"0.1","output":{"item_id":item["item_id"],
                "disposition":labels[item["item_id"]]["acceptable"][0],
                "evidence_refs": sorted({c for cs in item["required_checks"].values() for c in cs}), "contradictions":[]}}
    runs=run_rounds(public,fixture_provider,ledger)
    (destination/"verified_runs.json").write_text(json.dumps(runs,sort_keys=True,indent=2)+"\n")
    report=score(public,labels,runs)
    report["ledger_cost_is_simulated"] = True
    (destination/"report.json").write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
    print(json.dumps({"status":"PASS","purpose":"SIMULATED_ENGINEERING_ONLY","items":report["item_count"],"families":report["family_count"]}))


if __name__ == "__main__": main()
