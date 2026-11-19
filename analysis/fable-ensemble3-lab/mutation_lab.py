"""Focused falsifiability checks on copied lab source; use the verified R2 mutation runner."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile

sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parent
R2=ROOT.parent/'fable-ensemble3-r1'
spec=importlib.util.spec_from_file_location('verified_mutation_runner',R2/'mutation_check.py')
mc=importlib.util.module_from_spec(spec); sys.modules[spec.name]=mc; spec.loader.exec_module(mc)
CASES=[
    ('agent self-confidence overrides gates','decision_engine.py',
     'eligible = False if False in gates.values() else None if None in gates.values() else True','eligible = True',
     'test_lab.BankAndEngineTests.test_checker_uses_facts_and_citations_not_agent_eligibility'),
    ('ineligible contradiction ignored','decision_engine.py',
     'if any(chosen in r["contradicts"] for r in runs if r["status"] == "OK"):',
     'if any(chosen in r["contradicts"] for r in runs if r["status"] == "OK" and r["eligible"] is True):',
     'test_lab.BankAndEngineTests.test_self_and_ineligible_verified_contradictions_veto'),
    ('one ensemble vote accepted','decision_engine.py','if n >= quorum]','if n >= 1]',
     'test_lab.BankAndEngineTests.test_public_policy_matches_pinned_reference_policy'),
    ('whole round exceeds reservation cap','budget_ledger.py',
     'if money(self.state["spent_upper"])+self.held+required > self.budget:','if False:',
     'test_lab.LedgerAndRunnerTests.test_round_reservation_is_all_or_nothing'),
    ('unknown cost treated as free','budget_ledger.py',
     'charged = money(cost) if known else self.cap','charged = money(cost) if known else Decimal(0)',
     'test_lab.LedgerAndRunnerTests.test_single_infra_retry_is_reserved_and_final_failure_counted'),
    ('provider identity ignored','responses_adapter.py',
     'if response.get("model") != MODEL:','if False:',
     'test_responses_adapter.AdapterTests.test_different_or_missing_model_is_a_capability_failure'),
    ('response history retained','responses_adapter.py',
     '"tools":[],"store":False,','"tools":[],"store":True,',
     'test_responses_adapter.AdapterTests.test_stateless_request_contains_only_public_input'),
    ('input preflight cap ignored','responses_adapter.py',
     "if token_integer(count.get('input_tokens'))>INPUT_LIMIT:",'if False:',
     'test_responses_adapter.AdapterTests.test_excessive_input_stops_before_generation'),
    ('nested oracle labels accepted','decision_engine.py',
     '    check_labels(item)','    if forbidden & set(item): raise ValueError("oracle labels are not public inputs")',
     'test_lab.BankAndEngineTests.test_nested_oracle_labels_cannot_enter_public_requests'),
    ('engineering fixtures promoted to formal bank','exploration_readiness.py',
     'bank.get("purpose")!="FORMAL_DECISION_EXPLORATION"','False',
     'test_exploration_readiness.ReadinessTests.test_engineering_fixtures_cannot_become_formal_by_repetition'),
    ('formal model identity ignored','exploration_readiness.py',
     'interface.get("model_observed")!=protocol.get("model")','False',
     'test_exploration_readiness.ReadinessTests.test_different_model_fake_transport_or_unverified_cap_is_rejected'),
    ('registered retries not in formal budget','exploration_readiness.py',
     'if budget<required: reasons.append','if False: reasons.append',
     'test_exploration_readiness.ReadinessTests.test_whole_rounds_and_infra_retry_are_in_budget'),
    ('nonobject output counted as timeout','decision_engine.py',
     'if not isinstance(raw, dict):\n        return verify_run(item, {"status": "INVALID_OUTPUT"})',
     'if not isinstance(raw, dict):\n        return verify_run(item, {"status": "TIMEOUT"})',
     'test_lab.BankAndEngineTests.test_nonobject_json_is_invalid_instead_of_throwing'),
    ('missing identity allowed on both sides','exploration_readiness.py',
     'or not identity_present or','or False or',
     'test_exploration_readiness.BoundArtifactTests.test_missing_identity_on_both_bound_artifacts_is_blocked'),
    ('public engineering split unbound','exploration_readiness.py',
     'identity_keys=("item_id","family","kind","split","source_ref")',
     'identity_keys=("item_id","family","kind","source_ref")',
     'test_exploration_readiness.BoundArtifactTests.test_public_engineering_split_cannot_be_hidden_by_manifest_rebinding'),
    ('public source identity unbound','exploration_readiness.py',
     'identity_keys=("item_id","family","kind","split","source_ref")',
     'identity_keys=("item_id","family","kind","split")',
     'test_exploration_readiness.BoundArtifactTests.test_public_source_cannot_be_missing_or_changed_after_rebinding'),
    ('registered policy artifact not read','exploration_readiness.py',
     '        artifact(protocol.get("reference_policy"))','        pass',
     'test_exploration_readiness.BoundArtifactTests.test_registered_policy_is_required_and_hash_verified'),
]


def main():
    checks=[row[-1] for row in CASES]
    baseline=mc._run_tests(ROOT,checks,30)
    if baseline.returncode or baseline.failures: raise SystemExit('focused baseline red')
    results=[]
    for name,target,old,new,check in CASES:
        with tempfile.TemporaryDirectory(prefix='ensemble3-lab-mutant-') as tmp:
            root=Path(tmp); work=root/'lab'; reference=root/'fable-ensemble3-r1'
            work.mkdir(); reference.mkdir()
            for file in ROOT.glob('*.py'): shutil.copy2(file,work/file.name)
            shutil.copy2(R2/'ensemble_scoring.py',reference/'ensemble_scoring.py')
            destination=work/target; text=destination.read_text()
            if text.count(old)!=1: raise SystemExit('patch mismatch: '+name)
            mutated=text.replace(old,new); compile(mutated,str(destination),'exec'); destination.write_text(mutated)
            imports,reason=mc._imports_cleanly(work,destination)
            run=mc._run_tests(work,[check],30) if imports else None
            expected=check.split('.')[-1]
            killed=bool(imports and not run.load_error and run.failures.get(expected)=='FAIL')
            results.append({'name':name,'target':target,'expected_test':check,'imports':imports,
                            'import_detail':reason,'observed_failures':None if run is None else run.failures,
                            'status':'KILLED' if killed else 'NOT_KILLED'})
    print(json.dumps({'focused_baseline_exit':baseline.returncode,'results':results,
                      'killed':sum(r['status']=='KILLED' for r in results),'total':len(results)},indent=2))
    return 0 if all(r['status']=='KILLED' for r in results) else 1


if __name__=='__main__': raise SystemExit(main())
