"""Engineering acceptance: oracle isolation, real subprocess replay, failure accounting and cap admission."""
import itertools
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
import budget_ledger as bl
import build_engineering_bank as bank
import decision_engine as engine
import offline_replay as replay

sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parent


def raw(item, disposition=None, refs=None, contradictions=None):
    return {"item_id":item["item_id"],"disposition":disposition or item["disposition_enum"][0],
            "evidence_refs":refs if refs is not None else sorted({c for cs in item["required_checks"].values() for c in cs}),
            "contradictions":contradictions or []}


class BankAndEngineTests(unittest.TestCase):
    def test_common_oracle_is_valid_and_balanced_for_engineering_only(self):
        es=replay.scorer(); cases=list(bank.fixtures())
        self.assertEqual(len(cases),8)
        self.assertEqual(len({i["family"] for i,l in cases}),8)
        self.assertEqual(sum("ESCALATE" in l["acceptable"] for i,l in cases),4)
        for item,label in cases:
            engine.validate_public(item)
            es.validate_item(es.Item(item["item_id"],item["family"],"DECISION",frozenset(item["disposition_enum"]),
                                     frozenset(label["acceptable"]),frozenset(label["unsafe"])))
            self.assertEqual(item["split"],"ENGINEERING_ONLY")

    def test_checker_uses_facts_and_citations_not_agent_eligibility(self):
        item,_=list(bank.fixtures())[0]
        output=raw(item); output["eligible"]=False
        self.assertIs(engine.verify_run(item,output)["eligible"],True)
        output=raw(item,refs=[]); output["eligible"]=True
        self.assertIsNone(engine.verify_run(item,output)["eligible"])
        denied,_=list(bank.fixtures())[1]
        self.assertIs(engine.verify_run(denied,raw(denied))["eligible"],False)

    def test_invalid_output_stays_a_failure(self):
        item,_=list(bank.fixtures())[0]
        self.assertEqual(engine.verify_run(item,raw(item,"MADE_UP"))["status"],"INVALID_OUTPUT")

    def test_nonobject_json_is_invalid_instead_of_throwing(self):
        item,_=list(bank.fixtures())[0]
        for value in ([],None,42,True,"answer"):
            self.assertEqual(engine.verify_run(item,value)["status"],"INVALID_OUTPUT")

    def test_self_and_ineligible_verified_contradictions_veto(self):
        item,_=list(bank.fixtures())[0]
        own=raw(item,"START_NEW",contradictions=[{"against":"START_NEW","check_id":"child_alive"}])
        self.assertEqual(engine.aggregate(item,[own],1)["disposition"],"ESCALATE")
        other=raw(item,"ATTACH_CHILD",refs=[],contradictions=own["contradictions"])
        self.assertEqual(engine.aggregate(item,[raw(item,"START_NEW")]*2+[other],2)["disposition"],"ESCALATE")
        bogus=raw(item,"ATTACH_CHILD",refs=[],contradictions=[{"against":"START_NEW","check_id":"made_up"}])
        self.assertEqual(engine.aggregate(item,[raw(item,"START_NEW")]*2+[bogus],2)["disposition"],"START_NEW")

    def test_one_and_three_identical_records_use_the_same_rule(self):
        item,_=list(bank.fixtures())[0]
        for disposition,eligible,contradicts,status in itertools.product(item["disposition_enum"],(True,False,None),
                    ([],["START_NEW"]),("OK","TIMEOUT","NOT_RUN")):
            run={"disposition":disposition,"eligible":eligible,"contradicts":contradicts,"status":status}
            self.assertEqual(engine.choose([run],1),engine.choose([run]*3,2))

    def test_public_policy_matches_pinned_reference_policy(self):
        es=replay.scorer(); item,label=list(bank.fixtures())[0]
        oracle_item=es.Item(item["item_id"],item["family"],"DECISION",frozenset(item["disposition_enum"]),
                            frozenset(label["acceptable"]),frozenset(label["unsafe"]))
        records=[{"status":"OK","disposition":d,"eligible":e,"contradicts":c}
                 for d,e,c in itertools.product(item["disposition_enum"],(True,False,None),([], ["START_NEW"]))]
        records.append({"status":"TIMEOUT","disposition":None,"eligible":None,"contradicts":[]})
        for records3 in itertools.product(records,repeat=3):
            chosen=engine.choose(records3,2)
            verified=[es.Run(r["status"],r["disposition"],r["eligible"],frozenset(r["contradicts"])) for r in records3]
            self.assertEqual(es.policy_outcome(oracle_item,verified,2),es.Outcome(es.label_disposition(oracle_item,chosen),chosen=="ESCALATE"))

    def test_hash_alone_does_not_grant_authority(self):
        item,_=list(bank.fixtures())[5]
        self.assertEqual(engine.aggregate(item,[raw(item)]*3,2)["disposition"],"ESCALATE")

    def test_duplicate_json_keys_are_rejected(self):
        with self.assertRaises(ValueError): json.loads('{"x":1,"x":2}',object_pairs_hook=engine.no_duplicates)

    def test_nested_oracle_labels_cannot_enter_public_requests(self):
        item,_=list(bank.fixtures())[0]
        item["notes"]=[{"nested":{"acceptable":["ATTACH_CHILD"]}}]
        with self.assertRaises(ValueError): engine.validate_public(item)


class PhysicalOracleIsolationTests(unittest.TestCase):
    def test_fresh_process_is_byte_identical_with_changed_deleted_and_denied_oracles(self):
        if not Path('/usr/bin/sandbox-exec').exists(): self.skipTest('macOS sandbox capability unavailable')
        with tempfile.TemporaryDirectory(prefix="ensemble3-isolation-") as tmp:
            root=Path(tmp); manifest=bank.build(root); row=manifest["items"][0]
            item=engine.read_json(row["public_ref"])
            public=root/'public'; private=(root/'private').resolve()
            candidates=public/'runs.json'; candidates.write_text(json.dumps([raw(item)]*3))
            args=[sys.executable,'-B',str(ROOT/'decision_engine.py'),'--item',row["public_ref"],'--runs',str(candidates),'--quorum','2']
            clean=subprocess.run(args,cwd=public,capture_output=True,check=True).stdout
            trace=json.loads(clean)["data_files_read"]
            self.assertEqual(set(trace),{str(Path(row["public_ref"]).resolve()),str(candidates.resolve())})
            Path(row["oracle_ref"]).write_text('{"acceptable":["START_NEW"]}')
            changed=subprocess.run(args,cwd=public,capture_output=True,check=True).stdout
            self.assertEqual(clean,changed)
            profile=root/'deny.sb'; profile.write_text(f'(version 1)\n(allow default)\n(deny file-read* (subpath "{private}"))\n')
            blocked=subprocess.run(['/usr/bin/sandbox-exec','-f',str(profile),'/bin/cat',row["oracle_ref"]],capture_output=True)
            self.assertNotEqual(blocked.returncode,0)
            allowed=subprocess.run(['/usr/bin/sandbox-exec','-f',str(profile),'/bin/cat',row["public_ref"]],capture_output=True)
            self.assertEqual(allowed.returncode,0)  # distinguish a real denial from a broken sandbox launcher
            denied=subprocess.run(['/usr/bin/sandbox-exec','-f',str(profile),*args],cwd=public,capture_output=True,check=True).stdout
            self.assertEqual(clean,denied)
            for oracle in private.glob('*.json'): oracle.unlink()
            deleted=subprocess.run(args,cwd=public,capture_output=True,check=True).stdout
            self.assertEqual(clean,deleted)


class LedgerAndRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="ensemble3-ledger-")
        self.root=Path(self.temp.name)
        item,_=list(bank.fixtures())[0]
        self.public={item["item_id"]:item}

    def tearDown(self): self.temp.cleanup()

    def ledger(self,budget=10,cap=1): return bl.Ledger(self.root/'ledger.json',budget,cap)

    def test_whole_round_preflight_below_three_runs_launches_nothing(self):
        calls=[]
        runs=replay.run_rounds(self.public,lambda *args:calls.append(args),self.ledger(budget=5),rounds=5)
        self.assertEqual(calls,[])
        self.assertEqual([r["status"] for r in next(iter(runs.values()))],["NOT_RUN"]*5)

    def test_round_reservation_is_all_or_nothing(self):
        ledger=self.ledger(budget=3)
        before=json.loads((self.root/'ledger.json').read_text())
        with self.assertRaises(bl.BudgetStop): ledger.reserve_round(0,['a','b'])
        self.assertEqual(ledger.state,before)
        self.assertEqual(json.loads((self.root/'ledger.json').read_text()),before)

    def test_planned_round_count_is_fixed_before_observing_costs(self):
        calls=[]
        def cheap(item,index,attempt):
            calls.append(index)
            return {"status":"OK","cost":"0.01","output":raw(item)}
        runs=replay.run_rounds(self.public,cheap,self.ledger(budget=6),rounds=5)
        self.assertEqual(calls,[0,1,2])
        self.assertEqual([r["status"] for r in next(iter(runs.values()))],["OK"]*3+["NOT_RUN"]*2)

    def test_single_infra_retry_is_reserved_and_final_failure_counted(self):
        calls=[]; ledger=self.ledger()
        def failing(item,index,attempt): calls.append((index,attempt)); return {"status":"INFRA_ERROR","cost":None}
        runs=replay.run_rounds(self.public,failing,ledger,rounds=3)
        self.assertEqual(calls,[(r,a) for r in range(3) for a in (1,2)])
        self.assertEqual([r["status"] for r in next(iter(runs.values()))],["INFRA_FAIL"]*3)
        self.assertEqual(Decimal(ledger.state["spent_upper"]),6)

    def test_infra_retry_success_charges_both_attempts(self):
        ledger=self.ledger()
        def flaky(item,index,attempt):
            return {"status":"INFRA_ERROR","cost":"0.2"} if attempt==1 else {"status":"OK","cost":"0.3","output":raw(item)}
        runs=replay.run_rounds(self.public,flaky,ledger,rounds=3)
        self.assertEqual([r["status"] for r in next(iter(runs.values()))],["OK"]*3)
        self.assertEqual(Decimal(ledger.state["spent_upper"]),Decimal('1.5'))

    def test_timeouts_are_failed_runs_and_are_not_retried(self):
        calls=[]; ledger=self.ledger()
        def slow(item,index,attempt): calls.append((index,attempt)); raise TimeoutError()
        runs=replay.run_rounds(self.public,slow,ledger,rounds=3)
        self.assertEqual(calls,[(r,1) for r in range(3)])
        self.assertEqual([r["status"] for r in next(iter(runs.values()))],["TIMEOUT"]*3)
        self.assertEqual(Decimal(ledger.state["spent_upper"]),3)

    def test_bad_model_output_is_not_omitted(self):
        ledger=self.ledger()
        runs=replay.run_rounds(self.public,lambda *args:{"status":"OK","cost":"0.1","output":{"disposition":"BAD"}},ledger,rounds=3)
        self.assertEqual([r["status"] for r in next(iter(runs.values()))],["INVALID_OUTPUT"]*3)
        _,label=list(bank.fixtures())[0]
        report=replay.score(self.public,{next(iter(self.public)):label},runs)
        self.assertEqual(report['unresolved'],[])
        self.assertEqual(report['diagnostic_intervals']['single']['estimate'],0.0)

    def test_started_but_unsettled_attempt_is_not_relaunched_on_recovery(self):
        ledger=self.ledger(); key=ledger.reserve_round(0,list(self.public))[0]; ledger.begin(key)
        recovered=bl.Ledger(self.root/'ledger.json',10,1)
        with self.assertRaises(bl.StateConflict): recovered.begin(key)
        recovered.recover_uncertain()
        self.assertEqual(recovered.state['slots'][key]['status'],'INCOMPLETE')
        self.assertEqual(Decimal(recovered.state['spent_upper']),1)
        with self.assertRaises(bl.StateConflict): recovered.begin(key)
        runs=replay.replay_saved(self.public,recovered,rounds=3)
        self.assertEqual([r['status'] for r in next(iter(runs.values()))],['INCOMPLETE','NOT_RUN','NOT_RUN'])

    def test_cap_violation_is_persisted_and_stops_all_future_starts(self):
        ledger=self.ledger(); keys=ledger.reserve_round(0,['a','b']); ledger.begin(keys[0])
        with self.assertRaises(bl.BudgetStop): ledger.finish(keys[0],'OK','1.01')
        self.assertTrue(bl.Ledger(self.root/'ledger.json',10,1).state['overrun'])
        with self.assertRaises(bl.StateConflict): ledger.begin(keys[1])

    def test_atomic_reload_and_budget_identity(self):
        ledger=self.ledger(); ledger.reserve_round(0,['a'])
        self.assertEqual(ledger.state,bl.Ledger(self.root/'ledger.json',10,1).state)
        self.assertEqual(list(self.root.glob('*.tmp')),[])
        with self.assertRaises(bl.StateConflict): bl.Ledger(self.root/'ledger.json',11,1)
        for invalid in (True,-1,'NaN','Infinity'):
            with self.assertRaises(ValueError): bl.money(invalid)

    def test_saved_results_replay_without_provider_calls(self):
        ledger=self.ledger()
        runs=replay.run_rounds(self.public,lambda item,*args:{"status":"OK","cost":"0.1","output":raw(item)},ledger,rounds=3)
        saved=replay.replay_saved(self.public,bl.Ledger(self.root/'ledger.json',10,1),rounds=3)
        self.assertEqual(runs,saved)
        with self.assertRaises(ValueError): replay.run_rounds(self.public,lambda *args:self.fail('relaunch'),ledger,rounds=3)


if __name__ == '__main__': unittest.main()
