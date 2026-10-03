"""Admission tests with artificial metadata; never a real frozen bank or live audit."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import exploration_readiness as gate
import build_engineering_bank as engineering_bank

ROOT=Path(__file__).resolve().parent


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.protocol={"status":"FROZEN","selection_basis":"DEPLOYMENT_OBSERVABLE_FEATURES",
            "instruction_surface":{"ref":"not_a_live_instruction_file"},"target_runs":5,
            "reference_policy":{"ref":"metadata_control_only","sha256":gate.FROZEN_POLICY_SHA256},
            "model":"fixture-model","effort":"medium","cost_basis":"fixture-price-basis",
            "attempt_reservation_usd":"0.55","total_reservation_usd":"165"}
        self.bank={"purpose":"FORMAL_DECISION_EXPLORATION","items":[
            {"item_id":"fixture-"+str(n),"family":"family-"+str(n%6),"kind":"DECISION","split":"EXPLORATION",
             "source_ref":"source-"+str(n%6),"lineage_ref":"lineage-"+str(n%6),"used_for_outcome_selection":False}
            for n in range(30)]}
        self.interface={"status":"BENCHMARK_READY","mode":"LIVE","model_observed":"fixture-model",
            "effort_requested":"medium","generation_cap_verified":True,"stateless_isolation_verified":True,
            "usage_accounting_verified":True,"cost_basis":"fixture-price-basis"}
        self.offline={"status":"PASS","scope":"OFFLINE_ENGINEERING","source_identity_verified":True}
        self.review={"verdict":"VERIFIED","scope":"FORMAL_BANK_AND_ORACLE"}

    def assess(self,**overrides):
        args={"protocol":self.protocol,"bank":self.bank,"interface":self.interface,"offline":self.offline,"bank_review":self.review}
        args.update(overrides)
        return gate.assess(**args)

    def test_complete_artificial_metadata_is_eligible_but_not_a_benchmark_result(self):
        result=self.assess()
        self.assertEqual(result["status"],"ELIGIBLE_FOR_REVIEWED_LAUNCH")
        self.assertEqual(result["generation_calls"],0)
        self.assertIs(result["is_power_guarantee"],False)
        self.assertIs(result["is_model_benchmark_result"],False)
        self.assertEqual(result["required_reservation_usd"],"165.00")

    def test_draft_missing_prerequisites_cannot_launch(self):
        result=self.assess(protocol={"status":"DRAFT","target_runs":5},bank=None,interface=None,offline=None,bank_review=None)
        self.assertEqual(result["status"],"BLOCKED")
        for name in ("PROTOCOL_NOT_FROZEN","FORMAL_BANK_UNAVAILABLE","LIVE_INTERFACE_EXIT_NOT_MET","TOTAL_COST_CAP_UNFROZEN"):
            self.assertIn(name,result["reasons"])

    def test_engineering_fixtures_cannot_become_formal_by_repetition(self):
        bank=copy.deepcopy(self.bank);bank["purpose"]="ENGINEERING_ONLY"
        self.assertIn("FORMAL_BANK_UNAVAILABLE",self.assess(bank=bank)["reasons"])

    def test_duplicate_ids_and_renamed_families_do_not_create_sample_units(self):
        bank=copy.deepcopy(self.bank);bank["items"][1]["item_id"]=bank["items"][0]["item_id"]
        self.assertIn("BANK_ITEM_IDENTITY_INVALID",self.assess(bank=bank)["reasons"])
        bank=copy.deepcopy(self.bank);bank["items"][1]["lineage_ref"]=bank["items"][0]["lineage_ref"]
        self.assertIn("SAME_LINEAGE_SPLIT_INTO_MULTIPLE_FAMILIES",self.assess(bank=bank)["reasons"])

    def test_holdout_and_outcome_selected_items_are_rejected(self):
        bank=copy.deepcopy(self.bank);bank["items"][0]["split"]="CONFIRMATION"
        self.assertIn("BANK_KIND_OR_SPLIT_INVALID",self.assess(bank=bank)["reasons"])
        bank=copy.deepcopy(self.bank);bank["items"][0]["used_for_outcome_selection"]=True
        self.assertIn("OUTCOME_BASED_SELECTION_NOT_EXCLUDED",self.assess(bank=bank)["reasons"])

    def test_different_model_fake_transport_or_unverified_cap_is_rejected(self):
        for key,value in (("model_observed","another-model"),("mode","SIMULATED_TRANSPORT"),
                         ("generation_cap_verified",False),("cost_basis","another-basis")):
            interface={**self.interface,key:value}
            self.assertIn("LIVE_INTERFACE_EXIT_NOT_MET",self.assess(interface=interface)["reasons"])

    def test_missing_or_empty_identity_on_both_sides_is_not_a_match(self):
        for requested,observed in (("model","model_observed"),("effort","effort_requested"),("cost_basis","cost_basis")):
            for missing in (None,""," ",False,0):
                protocol={**self.protocol,requested:missing};interface={**self.interface,observed:missing}
                self.assertEqual(self.assess(protocol=protocol,interface=interface)["status"],"BLOCKED")

    def test_source_review_and_same_identity_integration_are_required(self):
        self.assertIn("FORMAL_BANK_REVIEW_MISSING",self.assess(bank_review=None)["reasons"])
        self.assertIn("OFFLINE_INTEGRATION_IDENTITY_UNVERIFIED",self.assess(offline={**self.offline,"source_identity_verified":False})["reasons"])

    def test_whole_rounds_and_infra_retry_are_in_budget(self):
        self.assertIn("REGISTERED_WHOLE_ROUNDS_NOT_RESERVED",self.assess(protocol={**self.protocol,"total_reservation_usd":"164.99"})["reasons"])
        for cost in (None,"NaN","Infinity","-1",True):
            self.assertIn("TOTAL_COST_CAP_UNFROZEN",self.assess(protocol={**self.protocol,"total_reservation_usd":cost})["reasons"])

    def test_changed_evidence_is_blocked_before_any_model_call(self):
        with tempfile.TemporaryDirectory(prefix="ensemble3-readiness-") as tmp:
            root=Path(tmp);evidence=root/"audit.json";evidence.write_text(json.dumps(self.interface))
            digest=hashlib.sha256(evidence.read_bytes()).hexdigest()
            protocol={**self.protocol,"instruction_surface":None,"interface":{"ref":str(evidence),"sha256":digest}}
            path=root/"protocol.json";path.write_text(json.dumps(protocol))
            evidence.write_text('{}')
            result=gate.check_file(path)
            self.assertEqual(result["status"],"BLOCKED")
            self.assertEqual(result["generation_calls"],0)
            self.assertIn("INTERFACE_ARTIFACT_INVALID",result["reasons"])

    def test_invalid_json_shape_and_stale_source_claims_cannot_pass(self):
        with tempfile.TemporaryDirectory(prefix="ensemble3-readiness-") as tmp:
            root=Path(tmp);path=root/"protocol.json";path.write_text('[]')
            self.assertEqual(gate.check_file(path)["status"],"BLOCKED")
            evidence=root/"offline.json";evidence.write_text(json.dumps({**self.offline,"source_hashes":{}}))
            protocol={**self.protocol,"instruction_surface":None,"offline":{
                "ref":str(evidence),"sha256":hashlib.sha256(evidence.read_bytes()).hexdigest()}}
            path.write_text(json.dumps(protocol))
            self.assertIn("OFFLINE_INTEGRATION_IDENTITY_UNVERIFIED",gate.check_file(path)["reasons"])

    def test_bank_review_must_bind_the_registered_bank(self):
        with tempfile.TemporaryDirectory(prefix="ensemble3-readiness-") as tmp:
            root=Path(tmp);review=root/"review.json";review.write_text(json.dumps({**self.review,"bank_manifest_sha256":"another-bank"}))
            protocol={**self.protocol,"instruction_surface":None,"bank_review":{
                "ref":str(review),"sha256":hashlib.sha256(review.read_bytes()).hexdigest()}}
            path=root/"protocol.json";path.write_text(json.dumps(protocol))
            self.assertIn("BANK_REVIEW_IDENTITY_INVALID",gate.check_file(path)["reasons"])

class BoundArtifactTests(unittest.TestCase):
    """All artifacts are artificial controls; never a real bank or LIVE receipt."""
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="ensemble3-bound-artifacts-")
        self.root=Path(self.temp.name);template,label=list(engineering_bank.fixtures())[0]
        instructions=self.root/"instructions.md";instructions.write_text("Artificial control; no provider.")
        instruction=self.record(instructions)
        self.reference=self.root/"reference_policy.py"
        self.reference.write_bytes((ROOT.parent/"fable-ensemble3-r1/ensemble_scoring.py").read_bytes())
        self.rows=[]
        for index in range(30):
            public=copy.deepcopy(template);private=copy.deepcopy(label)
            public.update(item_id="control-"+str(index),family="family-"+str(index%6),
                          source_ref="source-"+str(index%6),split="EXPLORATION",
                          exposure=[{"event":"ARTIFICIAL_ADMISSION_TEST"}])
            pub=self.pin(self.root/(str(index)+"-public.json"),public)
            private.update(item_id=public["item_id"],public_sha256=pub["sha256"])
            oracle=self.pin(self.root/(str(index)+"-private.json"),private)
            self.rows.append({"item_id":public["item_id"],"family":public["family"],"kind":"DECISION",
                              "split":"EXPLORATION","source_ref":public["source_ref"],"lineage_ref":"lineage-"+str(index%6),
                              "used_for_outcome_selection":False,"public_ref":pub["ref"],"public_sha256":pub["sha256"],
                              "oracle_ref":oracle["ref"],"oracle_sha256":oracle["sha256"]})
        self.interface={"status":"BENCHMARK_READY","mode":"LIVE","model_observed":"fixture-model",
                        "effort_requested":"medium","generation_cap_verified":True,"stateless_isolation_verified":True,
                        "usage_accounting_verified":True,"cost_basis":"fixture-price",
                        "instruction_sha256":instruction["sha256"]}
        self.protocol={"status":"FROZEN","selection_basis":"DEPLOYMENT_OBSERVABLE_FEATURES","target_runs":5,
                       "model":"fixture-model","effort":"medium","cost_basis":"fixture-price",
                       "attempt_reservation_usd":"0.55","total_reservation_usd":"165",
                       "instruction_surface":instruction,"reference_policy":self.record(self.reference)}

    def tearDown(self): self.temp.cleanup()

    def record(self,path):
        return {"ref":str(path),"sha256":hashlib.sha256(path.read_bytes()).hexdigest()}

    def pin(self,path,value):
        path.write_text(json.dumps(value,sort_keys=True));return self.record(path)

    def check(self,protocol=None,interface=None):
        protocol=copy.deepcopy(self.protocol if protocol is None else protocol)
        bank=self.pin(self.root/"bank.json",{"purpose":"FORMAL_DECISION_EXPLORATION","items":self.rows})
        protocol["bank"]=bank
        protocol["bank_review"]=self.pin(self.root/"review.json",{
            "verdict":"VERIFIED","scope":"FORMAL_BANK_AND_ORACLE","bank_manifest_sha256":bank["sha256"]})
        protocol["interface"]=self.pin(self.root/"interface.json",self.interface if interface is None else interface)
        protocol["offline"]=self.pin(self.root/"offline.json",{
            "status":"PASS","scope":"OFFLINE_ENGINEERING",
            "source_hashes":{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.glob("*.py")}})
        return gate.check_file(self.pin(self.root/"protocol.json",protocol)["ref"])

    def alter_public(self,index,key,value):
        row=self.rows[index];public_path=Path(row["public_ref"])
        public=json.loads(public_path.read_text());public[key]=value
        row["public_sha256"]=self.pin(public_path,public)["sha256"]
        oracle_path=Path(row["oracle_ref"]);oracle=json.loads(oracle_path.read_text())
        oracle["public_sha256"]=row["public_sha256"]
        row["oracle_sha256"]=self.pin(oracle_path,oracle)["sha256"]

    def test_complete_positive_artifact_control(self):
        self.assertEqual(self.check()["status"],"ELIGIBLE_FOR_REVIEWED_LAUNCH")

    def test_missing_identity_on_both_bound_artifacts_is_blocked(self):
        self.assertEqual(self.check()["status"],"ELIGIBLE_FOR_REVIEWED_LAUNCH")
        for requested,observed in (("model","model_observed"),("effort","effort_requested"),("cost_basis","cost_basis")):
            protocol=copy.deepcopy(self.protocol);interface=copy.deepcopy(self.interface)
            protocol.pop(requested);interface.pop(observed)
            self.assertEqual(self.check(protocol,interface)["status"],"BLOCKED")

    def test_public_engineering_split_cannot_be_hidden_by_manifest_rebinding(self):
        self.assertEqual(self.check()["status"],"ELIGIBLE_FOR_REVIEWED_LAUNCH")
        self.alter_public(0,"split","ENGINEERING_ONLY")
        self.assertIn("BANK_BINDING_INVALID",self.check()["reasons"])

    def test_public_source_cannot_be_missing_or_changed_after_rebinding(self):
        self.assertEqual(self.check()["status"],"ELIGIBLE_FOR_REVIEWED_LAUNCH")
        for value in ("",None,"different-source"):
            self.alter_public(0,"source_ref",value)
            self.assertIn("BANK_BINDING_INVALID",self.check()["reasons"])

    def test_registered_policy_is_required_and_hash_verified(self):
        self.assertEqual(self.check()["status"],"ELIGIBLE_FOR_REVIEWED_LAUNCH")
        original=self.reference.read_bytes();self.reference.write_bytes(original+b"\n# changed\n")
        self.assertIn("REFERENCE_POLICY_IDENTITY_INVALID",self.check()["reasons"])
        self.reference.write_bytes(original)
        protocol=copy.deepcopy(self.protocol);protocol["reference_policy"]["sha256"]="0"*64
        self.assertIn("REFERENCE_POLICY_IDENTITY_INVALID",self.check(protocol)["reasons"])
        protocol=copy.deepcopy(self.protocol);protocol.pop("reference_policy")
        self.assertIn("REFERENCE_POLICY_IDENTITY_INVALID",self.check(protocol)["reasons"])


if __name__=="__main__": unittest.main()
