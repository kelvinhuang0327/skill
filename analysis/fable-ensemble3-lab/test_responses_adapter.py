"""Provider-contract tests. No live API calls; no quota or USD consumed."""
import copy
import json
import os
import unittest
from unittest.mock import patch
import build_engineering_bank as bank
import responses_adapter as api


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.item,_=list(bank.fixtures())[0]
        self.answer={'item_id':self.item['item_id'],'disposition':'ATTACH_CHILD',
                     'evidence_refs':list({c for cs in self.item['required_checks'].values() for c in cs}),'contradictions':[]}
        self.response={'id':'resp_fixture','model':api.MODEL,'service_tier':'default','status':'completed',
                       'usage':{'input_tokens':100,'output_tokens':20,'input_tokens_details':{'cached_tokens':0}},
                       'output':[{'type':'message','role':'assistant','content':[{'type':'output_text','text':json.dumps(self.answer)}]}]}

    def test_stateless_request_contains_only_public_input(self):
        body=api.build_request(self.item,'pinned Fable instructions')
        self.assertEqual(body['model'],api.MODEL)
        self.assertEqual(body['reasoning']['effort'],'medium')
        self.assertIs(body['store'],False)
        self.assertEqual(body['tools'],[])
        self.assertNotIn('previous_response_id',body)
        self.assertNotIn('conversation',body)
        self.assertNotIn('acceptable',body['input'])
        self.assertEqual(body['max_output_tokens'],4096)

    def test_provider_identity_and_usage_are_parsed_without_claiming_billed_usd(self):
        receipt=api.parse_response(self.response,self.item)
        self.assertEqual(receipt['observed_model'],api.MODEL)
        self.assertEqual(receipt['status'],'OK')
        self.assertIsNone(receipt['actual_usd'])
        self.assertEqual(receipt['estimated_usd'],'0.000044')
        self.assertEqual(receipt['cost_upper_usd'],'0.000049')

    def test_different_or_missing_model_is_a_capability_failure(self):
        for model in (None,'gpt-6-luna','gpt-5.6-sol'):
            with self.assertRaises(api.CapabilityBlocked): api.parse_response({**self.response,'model':model},self.item)

    def test_output_cap_reached_stays_failed_with_usage(self):
        receipt=api.parse_response({**self.response,'status':'incomplete','incomplete_details':{'reason':'max_output_tokens'}},self.item)
        self.assertEqual(receipt['status'],'BUDGET_HALT')
        self.assertIsNone(receipt['output'])
        self.assertIn('usage',receipt)

    def test_unknown_service_tier_and_tools_fail_closed(self):
        with self.assertRaises(api.CapabilityBlocked): api.parse_response({**self.response,'service_tier':'priority'},self.item)
        with self.assertRaises(api.CapabilityBlocked): api.parse_response({**self.response,'output':[{'type':'function_call'}]},self.item)

    def test_bad_structured_output_is_invalid_and_keeps_cost(self):
        for text in ('not JSON','[]','null','42','true','"answer"'):
            response=copy.deepcopy(self.response); response['output'][0]['content'][0]['text']=text
            receipt=api.parse_response(response,self.item)
            self.assertEqual(receipt['status'],'INVALID_OUTPUT')
            self.assertIsNotNone(receipt['cost_upper_usd'])

    def test_bad_usage_is_not_zeroed(self):
        for value in (-1,True,None,'100'):
            with self.assertRaises(ValueError): api.usage_estimate({'input_tokens':value,'output_tokens':20})
        with self.assertRaises(api.CapabilityBlocked): api.usage_estimate({'input_tokens':100,'output_tokens':4097})

    def test_no_credentials_blocks_before_transport(self):
        adapter=api.ResponsesAdapter('pinned')
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(api.CapabilityBlocked): adapter.preflight()

    def test_excessive_input_stops_before_generation(self):
        calls=[]
        def transport(path,body): calls.append(path); return {'input_tokens':180001}
        with self.assertRaises(api.CapabilityBlocked): api.ResponsesAdapter('pinned',transport).invoke(self.item)
        self.assertEqual(calls,['responses/input_tokens'])

    def test_simulated_transport_is_not_labeled_as_a_live_result(self):
        calls=[]
        def transport(path,body):
            calls.append(path)
            return {'input_tokens':100} if path.endswith('input_tokens') else self.response
        receipt=api.ResponsesAdapter('pinned',transport).invoke(self.item)
        self.assertEqual(receipt['mode'],'SIMULATED_TRANSPORT')
        self.assertEqual(calls,['responses/input_tokens','responses'])


if __name__=='__main__': unittest.main()
