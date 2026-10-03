"""Stateless Responses adapter. Unit-tested transport contract; live access is a separate gate.

No agent tools, filesystem access, conversation ids, or previous responses are sent.
USD fields derived from public pricing are estimates, never billed-cost receipts.
"""
from decimal import Decimal
import hashlib
import json
import os
import urllib.error
import urllib.request
import decision_engine as engine

MODEL = "gpt-5.6-luna"
EFFORT = "medium"
INPUT_LIMIT = 180000
OUTPUT_LIMIT = 4096
# Conservative standard-list-price upper bound at the documented maximum context,
# including the long-context and cache-write premiums. Not a statement about invoices.
CONTEXT_LIMIT = 1050000
ATTEMPT_PRICE_UPPER = Decimal("0.55")
PRICE_SOURCE = "https://developers.openai.com/api/docs/models/gpt-5.6-luna"


class CapabilityBlocked(Exception):
    pass


def token_integer(value):
    if type(value) is not int or value < 0:
        raise ValueError("expected a nonnegative integer token count")
    return value


def schema(item):
    return {"type":"object","additionalProperties":False,
            "properties":{"item_id":{"type":"string","enum":[item["item_id"]]},
                          "disposition":{"type":"string","enum":item["disposition_enum"]},
                          "evidence_refs":{"type":"array","items":{"type":"string"}},
                          "contradictions":{"type":"array","items":{"type":"object","additionalProperties":False,
                            "properties":{"against":{"type":"string","enum":item["disposition_enum"]},
                                          "check_id":{"type":"string"}},"required":["against","check_id"]}}},
            "required":["item_id","disposition","evidence_refs","contradictions"]}


def build_request(item,instructions):
    engine.validate_public(item)
    if not instructions:
        raise ValueError("pinned instruction surface is required")
    return {"model":MODEL,"reasoning":{"effort":EFFORT},"instructions":instructions,
            "input":json.dumps(item,sort_keys=True,separators=(",",":")),"tools":[],"store":False,
            "service_tier":"default","max_output_tokens":OUTPUT_LIMIT,"truncation":"disabled",
            "text":{"format":{"type":"json_schema","name":"independent_decision","strict":True,"schema":schema(item)}}}


def usage_estimate(usage):
    inputs=token_integer(usage["input_tokens"]); outputs=token_integer(usage["output_tokens"])
    cached=token_integer(usage.get("input_tokens_details",{}).get("cached_tokens",0))
    if cached>inputs or inputs>CONTEXT_LIMIT or outputs>OUTPUT_LIMIT:
        raise CapabilityBlocked("reported usage violates requested/documented bounds")
    multiplier=Decimal(2) if inputs>272000 else Decimal(1)
    output_multiplier=Decimal('1.5') if inputs>272000 else Decimal(1)
    estimate=(Decimal(inputs-cached)*Decimal('0.20')*multiplier+
              Decimal(cached)*Decimal('0.02')*multiplier+Decimal(outputs)*Decimal('1.20')*output_multiplier)/Decimal(1000000)
    # Cache-write details may be unavailable; retain a separate conservative upper.
    upper=(Decimal(inputs)*Decimal('0.25')*multiplier+Decimal(outputs)*Decimal('1.20')*output_multiplier)/Decimal(1000000)
    if upper>ATTEMPT_PRICE_UPPER:
        raise CapabilityBlocked("usage exceeds the conservative reservation")
    return str(estimate),str(upper)


def parse_response(response,item):
    if response.get("model") != MODEL:
        raise CapabilityBlocked("provider model receipt missing or mismatched")
    if response.get("service_tier") != "default":
        raise CapabilityBlocked("default service tier was not verified")
    if not isinstance(response.get("id"),str) or not response["id"]:
        raise CapabilityBlocked("provider response id missing")
    estimate,upper=usage_estimate(response["usage"])
    status=response.get("status")
    if status=="incomplete":
        reason=(response.get("incomplete_details") or {}).get("reason")
        return {"status":"BUDGET_HALT" if reason=="max_output_tokens" else "INCOMPLETE",
                "output":None,"provider_response_id":response["id"],"observed_model":response["model"],
                "usage":response["usage"],"actual_usd":None,"estimated_usd":estimate,"cost_upper_usd":upper}
    if status!="completed": raise CapabilityBlocked("provider response is not completed")
    texts=[]
    for output in response.get("output",[]):
        if output.get("type")=="message" and output.get("role")=="assistant":
            texts.extend(c["text"] for c in output.get("content",[]) if c.get("type")=="output_text")
        elif output.get("type")!="reasoning":
            raise CapabilityBlocked("unexpected tool or output type in a tool-free decision run")
    if len(texts)!=1: raise CapabilityBlocked("exactly one structured final message is required")
    try: result=json.loads(texts[0],object_pairs_hook=engine.no_duplicates)
    except (ValueError,TypeError): result={}
    verified=engine.verify_run(item,result)
    return {"status":verified["status"],"output":result,"provider_response_id":response["id"],
            "observed_model":response["model"],"usage":response["usage"],"actual_usd":None,
            "estimated_usd":estimate,"cost_upper_usd":upper}


class ResponsesAdapter:
    def __init__(self,instructions,transport=None):
        self.instructions=instructions
        self.instruction_sha256=hashlib.sha256(instructions.encode()).hexdigest()
        self.transport=transport or self._post
        self.live=transport is None

    def preflight(self):
        if self.live and not os.environ.get("OPENAI_API_KEY"):
            raise CapabilityBlocked("OPENAI_API_KEY_UNAVAILABLE: live Responses adapter not run")
        return {"mode":"LIVE_NOT_YET_VALIDATED" if self.live else "SIMULATED_TRANSPORT",
                "model":MODEL,"effort":EFFORT,"instruction_sha256":self.instruction_sha256,
                "reservation_usd":str(ATTEMPT_PRICE_UPPER),"cost_basis":"CONSERVATIVE_API_LIST_PRICE_UPPER_BOUND"}

    def _post(self,path,body):
        self.preflight()
        # Only the official endpoint. Do not print, serialize or persist credentials.
        request=urllib.request.Request('https://api.openai.com/v1/'+path,
                    data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+os.environ['OPENAI_API_KEY'],
                    'Content-Type':'application/json'},method='POST')
        with urllib.request.urlopen(request,timeout=60) as response:
            return json.loads(response.read(),object_pairs_hook=engine.no_duplicates)

    def invoke(self,item):
        self.preflight()
        body=build_request(item,self.instructions)
        count_body={key:body[key] for key in ('model','instructions','input','tools')}
        count=self.transport('responses/input_tokens',count_body)
        if token_integer(count.get('input_tokens'))>INPUT_LIMIT:
            raise CapabilityBlocked("input token preflight over cap; generation did not start")
        result=self.transport('responses',body)
        receipt=parse_response(result,item)
        receipt['mode']='LIVE' if self.live else 'SIMULATED_TRANSPORT'
        receipt['instruction_sha256']=self.instruction_sha256
        return receipt
