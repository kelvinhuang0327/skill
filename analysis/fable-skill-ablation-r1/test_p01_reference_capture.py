#!/usr/bin/env python3
"""Offline acceptance for the one-time zero-turn P01 reference-init capture.

Every test uses a fake or injected process boundary.  The real provider binary
is never invoked, no reference artifact escapes the ephemeral test directory,
and no epoch, ledger, reservation or lock state is created anywhere.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping
from unittest import mock


sys.dont_write_bytecode = True
MODULE_PATH = Path(__file__).with_name("p01_reference_capture.py")
SPEC = importlib.util.spec_from_file_location(
    "fable_ablation_p01_reference_capture", MODULE_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import {MODULE_PATH}")
capture = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = capture
SPEC.loader.exec_module(capture)

production = capture.production
driver = capture.driver
runner = capture.runner
execution_builder = capture.execution_builder
claude = capture.claude
controller = capture.controller

REPOSITORY_ROOT = MODULE_PATH.resolve().parents[2]
SEALED_MANIFEST_PATH = Path(
    "/Users/kelvin/FableAblation-Epoch-56a62a62e5aae9fe3a230cbbed831a2a/build/manifest.json"
)
SEALED_SHA256 = driver.SEALED_MANIFEST_SHA256
PROVIDER_BINARY_PATH = "/Users/kelvin/.local/bin/claude"
_ABSENT = object()
_EXECUTION_MANIFEST: Mapping = {}


def execution_manifest() -> Mapping:
    global _EXECUTION_MANIFEST
    if not _EXECUTION_MANIFEST:
        sealed = json.loads(SEALED_MANIFEST_PATH.read_text(encoding="utf-8"))
        _EXECUTION_MANIFEST = execution_builder.derive_p01_execution_manifest(
            sealed, expected_sealed_manifest_sha256=SEALED_SHA256
        )
    return copy.deepcopy(dict(_EXECUTION_MANIFEST))


def plain_git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"git {' '.join(arguments)} failed: {result.stderr.decode('utf-8', 'replace')}"
        )
    return result.stdout.decode("utf-8").strip()


def init_event(
    *,
    skills=("reference-skill", "another-skill"),
    tools=("Bash", "Read", "Edit"),
    agents=("general-purpose",),
    mcp_servers=("local-reference",),
    api_key_source=_ABSENT,
    drop=(),
    empty=(),
) -> dict:
    event = {
        "type": "system",
        "subtype": "init",
        "session_id": "reference-session",
        "model": claude.CTO_MODEL_ID,
        "skills": list(skills),
        "tools": list(tools),
        "agents": list(agents),
        "mcp_servers": list(mcp_servers),
    }
    if api_key_source is not _ABSENT:
        event["apiKeySource"] = api_key_source
    for name in drop:
        event.pop(name, None)
    for name in empty:
        event[name] = []
    return event


def zero_turn_stdout(
    *,
    init: Mapping = None,
    cost: Any = "0.0000000",
    num_turns: Any = 0,
    before=(),
    after=(),
    include_init: bool = True,
    include_result: bool = True,
) -> bytes:
    records = list(before)
    if include_init:
        records.append(init_event() if init is None else init)
    records.extend(after)
    if include_result:
        terminal = {
            "type": "result",
            "subtype": "success",
            "session_id": "reference-session",
            "is_error": False,
        }
        if num_turns is not _ABSENT:
            terminal["num_turns"] = num_turns
        if cost is not _ABSENT:
            terminal["total_cost_usd"] = cost
        records.append(terminal)
    if not records:
        return b""
    return (
        b"\n".join(
            json.dumps(record, separators=(",", ":")).encode("utf-8")
            for record in records
        )
        + b"\n"
    )


class FakeProviderIdentity:
    def __init__(self, executable: str) -> None:
        self.executable = executable
        self.realpath = "/offline/claude"
        self.sha256 = json.loads(SEALED_MANIFEST_PATH.read_text(encoding="utf-8"))[
            "provider_runtime"
        ]["sha256"]
        self.version = json.loads(SEALED_MANIFEST_PATH.read_text(encoding="utf-8"))[
            "provider_runtime"
        ]["version"]


class RecordingBoundary:
    """A pure fake: it records the request and returns a scripted result."""

    def __init__(self, stdout: bytes = None, **overrides: Any) -> None:
        self.requests = []
        self.stdout = zero_turn_stdout() if stdout is None else stdout
        self.overrides = overrides

    def __call__(self, request):
        self.requests.append(request)
        values = {"returncode": 0, "stdout": self.stdout}
        values.update(self.overrides)
        return capture.CaptureProcessResult(**values)


class SpawningFakeBoundary:
    """Spawns a real *fake* executable, proving the process contract exactly."""

    def __init__(self, executable: Path) -> None:
        self.executable = executable
        self.requests = []
        self.observed_argv = None
        self.observed_stdin = None

    def __call__(self, request):
        self.requests.append(request)
        argv = [str(self.executable), *request.logical_argv[1:]]
        completed = subprocess.run(
            argv,
            input=request.stdin_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=request.cwd,
            env=dict(request.environment),
            shell=False,
            check=False,
            timeout=request.timeout_seconds,
        )
        self.observed_argv = tuple(argv)
        return capture.CaptureProcessResult(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            physical_argv=tuple(argv),
        )


class ReferenceCaptureTestCase(unittest.TestCase):
    carrier = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.carrier = production.resolve_carrier_source(repository=REPOSITORY_ROOT)
        cls.head = plain_git(REPOSITORY_ROOT, "rev-parse", "HEAD")
        cls.tree = plain_git(REPOSITORY_ROOT, "rev-parse", "HEAD^{tree}")

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.workspaces = self.root / "workspaces"
        self.workspaces.mkdir()
        self.hold_out = self.root / "held-out"
        self.hold_out.mkdir()
        self.artifact = self.root / "p01_reference_init.json"
        self.counter = 0

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def token(self) -> str:
        self.counter += 1
        return "%032x" % self.counter

    def request(self, **overrides: Any):
        values = {
            "execution_manifest": execution_manifest(),
            "workspace_parent": self.workspaces,
            "hold_out_root": self.hold_out,
            "carrier": self.carrier,
            "source_repository": REPOSITORY_ROOT,
            "authorized_head": self.head,
            "authorized_tree": self.tree,
            "artifact_path": self.artifact,
            "opaque_id_factory": self.token,
        }
        values.update(overrides)
        return capture.ReferenceCaptureRequest(**values)

    def run_capture(self, boundary=None, **overrides: Any):
        return capture.capture_reference_init(
            self.request(**overrides),
            process_boundary=RecordingBoundary() if boundary is None else boundary,
            provider_identity_resolver=FakeProviderIdentity,
        )

    def assert_aborts(self, reason: str = None, boundary=None, **overrides: Any):
        with self.assertRaises(capture.ReferenceCaptureAbort) as context:
            self.run_capture(boundary=boundary, **overrides)
        if reason is not None:
            self.assertEqual(context.exception.reason, reason)
        self.assertFalse(
            self.artifact.exists(), msg="an aborted capture must write no artifact"
        )
        return context.exception


class ZeroTurnCaptureTests(ReferenceCaptureTestCase):
    def test_01_correct_zero_turn_init_stream_is_accepted(self) -> None:
        boundary = RecordingBoundary()
        outcome = self.run_capture(boundary=boundary)
        self.assertTrue(self.artifact.is_file())
        self.assertEqual(outcome.record["capture_mode"], "ZERO_TURN_INIT_ONLY")
        self.assertEqual(outcome.record["pair_id"], "P01")
        self.assertEqual(outcome.record["task_id"], "A01")
        self.assertEqual(outcome.record["sealed_manifest_sha256"], SEALED_SHA256)
        self.assertEqual(outcome.record["observed_total_cost_usd"], "0.0000000")
        self.assertEqual(outcome.record["observed_model_turn_count"], 0)
        self.assertEqual(outcome.record["observed_assistant_message_count"], 0)
        self.assertEqual(
            outcome.record["init_event_sha256"],
            production.init_event_sha256(outcome.record["init_event"]),
        )
        self.assertEqual(
            outcome.sha256, hashlib.sha256(self.artifact.read_bytes()).hexdigest()
        )
        self.assertTrue(outcome.surface_audit.passed)
        # The persisted artifact is immediately usable as production authority.
        loaded = production.load_reference_init(
            self.artifact, expected_reference_sha256=outcome.sha256
        )
        self.assertEqual(loaded.record, outcome.record)
        delta = production.treatment_delta_expectation(loaded)
        self.assertEqual(
            delta.on_expected_skill_identities,
            delta.reference_skill_identities | {runner.FABLE_SKILL_IDENTITY},
        )

    def test_02_budget_ceiling_is_physically_omitted_and_turns_zeroed(self) -> None:
        argv = capture.build_reference_argv(PROVIDER_BINARY_PATH)
        self.assertNotIn("--max-budget-usd", argv)
        self.assertEqual(argv[argv.index("--max-turns") + 1], "0")

        paid = list(claude.build_cto_argv(PROVIDER_BINARY_PATH))
        self.assertIn("--max-budget-usd", paid)
        self.assertEqual(len(argv), len(paid) - 2)
        # argv is exactly paid with the budget pair removed and the turns
        # value zeroed; nothing else may differ.
        budget_index = paid.index("--max-budget-usd")
        without_budget = paid[:budget_index] + paid[budget_index + 2 :]
        turns_index = without_budget.index("--max-turns")
        expected = list(without_budget)
        expected[turns_index + 1] = "0"
        self.assertEqual(list(argv), expected)

        boundary = RecordingBoundary()
        self.run_capture(boundary=boundary)
        observed = list(boundary.requests[0].logical_argv)
        self.assertNotIn("--max-budget-usd", observed)
        self.assertEqual(observed[observed.index("--max-turns") + 1], "0")

        capture.assert_zero_spend_reference_argv(argv)

        physically_present = list(argv[:1]) + ["--max-budget-usd", "0.0000000"] + list(
            argv[1:]
        )
        with self.assertRaises(capture.ReferenceCaptureAbort) as context:
            capture.assert_zero_spend_reference_argv(physically_present)
        self.assertEqual(context.exception.reason, "BUDGET_CEILING_ARGUMENT_PRESENT")

        bad_turns = list(argv)
        bad_turns[bad_turns.index("--max-turns") + 1] = "1"
        with self.assertRaises(capture.ReferenceCaptureAbort) as context:
            capture.assert_zero_spend_reference_argv(bad_turns)
        self.assertEqual(context.exception.reason, "MAX_TURNS_NOT_ZERO")

    def test_02b_budget_and_turns_are_derived_from_argv_not_a_constant(self) -> None:
        argv = capture.build_reference_argv(PROVIDER_BINARY_PATH)
        self.assertEqual(
            capture.derive_budget_ceiling_from_argv(argv), (False, None)
        )
        self.assertEqual(capture.derive_configured_max_turns_from_argv(argv), 0)

        with_budget = list(argv) + ["--max-budget-usd", "3.5000000"]
        self.assertEqual(
            capture.derive_budget_ceiling_from_argv(with_budget), (True, "3.5000000")
        )

        with_other_turns = list(argv)
        with_other_turns[with_other_turns.index("--max-turns") + 1] = "7"
        self.assertEqual(
            capture.derive_configured_max_turns_from_argv(with_other_turns), 7
        )

    def test_02c_reference_record_reports_omitted_budget_honestly(self) -> None:
        outcome = self.run_capture()
        configuration = outcome.record["capture_configuration"]
        self.assertNotIn("--max-budget-usd", configuration["argv"])
        self.assertIs(configuration["budget_ceiling_present"], False)
        self.assertIsNone(configuration["max_budget_usd"])
        self.assertEqual(configuration["logical_reference_budget_usd"], "0.0000000")
        self.assertEqual(configuration["max_turns"], 0)
        self.assertEqual(
            set(configuration), set(production.CAPTURE_CONFIGURATION_KEYS)
        )

    def test_03_user_task_prompt_is_not_sent(self) -> None:
        boundary = RecordingBoundary()
        self.run_capture(boundary=boundary)
        request = boundary.requests[0]
        self.assertEqual(request.stdin_bytes, b"")
        manifest = execution_manifest()
        task_prompt = manifest["tasks"][0]["prompt"]
        self.assertNotIn(task_prompt.encode("utf-8"), request.stdin_bytes)
        for value in request.logical_argv:
            self.assertNotIn(task_prompt, value)
        self.assertNotIn(task_prompt, request.cwd)

        off_id = execution_builder.p01_run_ids(SEALED_SHA256)[0]
        slot = runner.select_manifest_slot(manifest, off_id)
        self.assertTrue(slot.prompt)
        workspace, opaque = runner.create_condition_neutral_workspace(
            self.workspaces, self.token
        )
        invocation = capture.build_reference_invocation(slot, workspace, opaque)
        self.assertEqual(invocation.prompt, "")
        self.assertNotEqual(invocation.prompt, slot.prompt)

    def analyze(self, **overrides: Any) -> Any:
        """Exercise analyze_zero_turn_stream directly: no manifest fixture,
        no workspace, no process boundary -- just the stream-analysis gate.
        """

        return capture.analyze_zero_turn_stream(
            capture.parse_stream_records(zero_turn_stdout(**overrides))
        )

    def assert_analyze_aborts(self, reason: str, **overrides: Any) -> Any:
        with self.assertRaises(capture.ReferenceCaptureAbort) as context:
            self.analyze(**overrides)
        self.assertEqual(context.exception.reason, reason)
        return context.exception

    def test_04_model_turn_event_is_rejected(self) -> None:
        self.assert_aborts(
            "MODEL_TURN_OBSERVED",
            boundary=RecordingBoundary(
                stdout=zero_turn_stdout(
                    after=({"type": "user", "session_id": "reference-session"},)
                )
            ),
        )
        self.assert_analyze_aborts(
            "MODEL_TURN_OBSERVED",
            after=({"type": "tool_use", "session_id": "reference-session"},),
        )

    def test_04b_terminal_num_turns_is_strictly_present_int_and_zero(self) -> None:
        # The terminal result's own num_turns counter is a different
        # observable from the stream-level user/tool_use gate above (tested
        # in test_04), so a mismatch here must use its own, distinct reason.
        self.assert_analyze_aborts("TERMINAL_NUM_TURNS_INVALID", num_turns=_ABSENT)
        for bad in (True, False, "0", 0.0, None, 1, -1):
            with self.subTest(num_turns=bad):
                self.assert_analyze_aborts("TERMINAL_NUM_TURNS_INVALID", num_turns=bad)
        # Positive control: the only accepted value still passes this gate.
        stream = self.analyze(num_turns=0)
        self.assertEqual(stream.model_turn_count, 0)

    def test_04c_observed_counts_are_stream_derived(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in ("model_turn_count=0,", "assistant_message_count=0,"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        self.assertIn("model_turn_count=len(turns)", source)
        self.assertIn("assistant_message_count=len(assistant)", source)

        stream = self.analyze()
        self.assertEqual(stream.model_turn_count, 0)
        self.assertEqual(stream.assistant_message_count, 0)
        self.assertIsInstance(stream.model_turn_count, int)
        self.assertIsInstance(stream.assistant_message_count, int)

    def test_04d_api_key_source_none_is_not_a_capture(self) -> None:
        # Even an otherwise-flawless zero-turn, zero-cost stream must not
        # pass this gate, and it must run before terminal/cost acceptance:
        # a simultaneously bad cost/num_turns still surfaces this reason.
        self.assert_analyze_aborts(
            "NOT_A_CAPTURE_API_KEY_SOURCE_NONE",
            init=init_event(api_key_source="none"),
        )
        self.assert_analyze_aborts(
            "NOT_A_CAPTURE_API_KEY_SOURCE_NONE",
            init=init_event(api_key_source="none"),
            cost="1.0000000",
            num_turns=1,
        )

    def test_04f_api_key_source_none_refuses_the_full_pipeline_too(self) -> None:
        self.assert_aborts(
            "NOT_A_CAPTURE_API_KEY_SOURCE_NONE",
            boundary=RecordingBoundary(
                stdout=zero_turn_stdout(init=init_event(api_key_source="none"))
            ),
        )

    def test_04e_api_key_source_other_than_none_is_not_rejected_by_this_gate(
        self,
    ) -> None:
        for source in ("user", "project", "org", "ANTHROPIC_API_KEY"):
            with self.subTest(api_key_source=source):
                stream = self.analyze(init=init_event(api_key_source=source))
                self.assertEqual(stream.init_event["apiKeySource"], source)

    def test_05_assistant_message_is_rejected(self) -> None:
        assistant = {
            "type": "assistant",
            "session_id": "reference-session",
            "message": {"id": "msg-1", "role": "assistant", "model": claude.CTO_MODEL_ID},
        }
        self.assert_aborts(
            "ASSISTANT_MESSAGE_OBSERVED",
            boundary=RecordingBoundary(stdout=zero_turn_stdout(after=(assistant,))),
        )
        role_only = {"session_id": "reference-session", "message": {"role": "assistant"}}
        self.assert_aborts(
            "ASSISTANT_MESSAGE_OBSERVED",
            boundary=RecordingBoundary(stdout=zero_turn_stdout(after=(role_only,))),
        )

    def test_06_nonzero_observed_cost_is_rejected(self) -> None:
        for cost in ("0.0000001", "0.5000000", 0.25, "2.0000000"):
            with self.subTest(cost=cost):
                exception = self.assert_aborts(
                    boundary=RecordingBoundary(stdout=zero_turn_stdout(cost=cost))
                )
                self.assertEqual(exception.reason, "NONZERO_OBSERVED_COST")

    def test_07_absent_or_ambiguous_cost_is_rejected(self) -> None:
        for cost in (_ABSENT, None, True, "not-a-number", "NaN"):
            with self.subTest(cost=cost):
                exception = self.assert_aborts(
                    boundary=RecordingBoundary(stdout=zero_turn_stdout(cost=cost))
                )
                self.assertEqual(exception.reason, "AMBIGUOUS_OBSERVED_COST")
        self.assert_aborts(
            "AMBIGUOUS_PROCESS_OUTCOME",
            boundary=RecordingBoundary(
                stdout=zero_turn_stdout(include_result=False)
            ),
        )
        self.assert_aborts(
            "AMBIGUOUS_PROCESS_OUTCOME", boundary=RecordingBoundary(returncode=1)
        )
        self.assert_aborts(
            "AMBIGUOUS_PROCESS_OUTCOME",
            boundary=RecordingBoundary(returncode=-1, timed_out=True),
        )

    def test_08_provider_identity_mismatch_is_rejected(self) -> None:
        class WrongSha(FakeProviderIdentity):
            def __init__(self, executable: str) -> None:
                super().__init__(executable)
                self.sha256 = "0" * 64

        class WrongVersion(FakeProviderIdentity):
            def __init__(self, executable: str) -> None:
                super().__init__(executable)
                self.version = "0.0.1 (Impostor)"

        boundary = RecordingBoundary()
        for resolver in (WrongSha, WrongVersion):
            with self.subTest(resolver=resolver.__name__):
                with self.assertRaises(capture.ReferenceCaptureAbort) as context:
                    capture.capture_reference_init(
                        self.request(),
                        process_boundary=boundary,
                        provider_identity_resolver=resolver,
                    )
                self.assertEqual(
                    context.exception.reason, "PROVIDER_BINARY_IDENTITY_MISMATCH"
                )
                self.assertFalse(self.artifact.exists())
        self.assertEqual(boundary.requests, [])

        def unresolvable(executable: str):
            raise FileNotFoundError(executable)

        with self.assertRaises(capture.ReferenceCaptureAbort) as context:
            capture.capture_reference_init(
                self.request(),
                process_boundary=boundary,
                provider_identity_resolver=unresolvable,
            )
        self.assertEqual(context.exception.reason, "PROVIDER_BINARY_IDENTITY_MISMATCH")
        self.assertEqual(boundary.requests, [])

    def test_09_missing_init_is_rejected(self) -> None:
        self.assert_aborts(
            "MISSING_INIT_EVENT",
            boundary=RecordingBoundary(stdout=zero_turn_stdout(include_init=False)),
        )
        self.assert_aborts(
            "EMPTY_STREAM",
            boundary=RecordingBoundary(
                stdout=zero_turn_stdout(include_init=False, include_result=False)
            ),
        )

    def test_10_conflicting_or_multiple_init_is_rejected(self) -> None:
        self.assert_aborts(
            "CONFLICTING_INIT_AUTHORITY",
            boundary=RecordingBoundary(stdout=zero_turn_stdout(before=(init_event(),))),
        )
        mistyped = init_event()
        mistyped["type"] = "control"
        self.assert_aborts(
            "CONFLICTING_INIT_AUTHORITY",
            boundary=RecordingBoundary(stdout=zero_turn_stdout(before=(mistyped,))),
        )
        self.assert_aborts(
            "MALFORMED_STREAM_RECORD",
            boundary=RecordingBoundary(stdout=b'{"type": "system"\n'),
        )

    def test_11_missing_required_inventory_is_rejected(self) -> None:
        for dimension in sorted(runner.REQUIRED_PURITY_DIMENSIONS):
            with self.subTest(dimension=dimension, mode="absent"):
                exception = self.assert_aborts(
                    boundary=RecordingBoundary(
                        stdout=zero_turn_stdout(init=init_event(drop=(dimension,)))
                    )
                )
                self.assertIn(
                    exception.reason,
                    ("INVALID_INIT_EVENT", "MISSING_REQUIRED_INVENTORY"),
                )
            with self.subTest(dimension=dimension, mode="empty"):
                self.assert_aborts(
                    "MISSING_REQUIRED_INVENTORY",
                    boundary=RecordingBoundary(
                        stdout=zero_turn_stdout(init=init_event(empty=(dimension,)))
                    ),
                )
        self.assert_aborts(
            "MISSING_REQUIRED_INVENTORY",
            boundary=RecordingBoundary(
                stdout=zero_turn_stdout(init=init_event(skills=()))
            ),
        )

    def test_12_reference_containing_fable_method_is_rejected(self) -> None:
        engaged = init_event(skills=("reference-skill", runner.FABLE_SKILL_IDENTITY))
        self.assert_aborts(
            "REFERENCE_CONTAINS_FABLE_METHOD",
            boundary=RecordingBoundary(stdout=zero_turn_stdout(init=engaged)),
        )

        workspace = self.root / "planted"
        (workspace / production.CARRIER_DESTINATION).mkdir(parents=True)
        with self.assertRaises(capture.ReferenceCaptureAbort) as context:
            capture.assert_reference_workspace_has_no_carrier(workspace)
        self.assertEqual(
            context.exception.reason, "REFERENCE_WORKSPACE_CONTAINS_FABLE_METHOD"
        )

    def test_13_artifact_serialization_is_deterministic(self) -> None:
        outcome = self.run_capture()
        record = dict(outcome.record)
        shuffled = {key: record[key] for key in sorted(record, reverse=True)}
        self.assertNotEqual(list(record), list(shuffled))
        self.assertEqual(
            production.canonical_reference_bytes(record),
            production.canonical_reference_bytes(shuffled),
        )
        self.assertEqual(
            production.canonical_reference_bytes(record), self.artifact.read_bytes()
        )
        self.assertTrue(self.artifact.read_bytes().endswith(b"}\n"))
        self.assertNotIn(b"\n ", self.artifact.read_bytes())

    def test_14_artifact_sha256_is_deterministic(self) -> None:
        outcome = self.run_capture()
        record = dict(outcome.record)
        shuffled = {key: record[key] for key in sorted(record, reverse=True)}
        self.assertEqual(
            production.reference_artifact_sha256(record),
            production.reference_artifact_sha256(shuffled),
        )
        self.assertEqual(production.reference_artifact_sha256(record), outcome.sha256)
        self.assertEqual(
            outcome.sha256, hashlib.sha256(self.artifact.read_bytes()).hexdigest()
        )
        mutated = copy.deepcopy(record)
        mutated["init_event"] = init_event(skills=("different-skill",))
        mutated["init_event_sha256"] = production.init_event_sha256(
            mutated["init_event"]
        )
        self.assertNotEqual(
            production.reference_artifact_sha256(mutated), outcome.sha256
        )

    def test_15_artifact_is_create_once_with_no_silent_overwrite(self) -> None:
        outcome = self.run_capture()
        original = self.artifact.read_bytes()
        with self.assertRaises(capture.ReferenceCaptureAbort) as context:
            capture.write_reference_artifact(self.artifact, outcome.record)
        self.assertEqual(context.exception.reason, "ARTIFACT_WRITE_REFUSED")
        self.assertEqual(self.artifact.read_bytes(), original)

        with self.assertRaises(capture.ReferenceCaptureAbort) as context:
            self.run_capture()
        self.assertEqual(context.exception.reason, "ARTIFACT_ALREADY_EXISTS")
        self.assertEqual(self.artifact.read_bytes(), original)

    def test_16_every_failure_creates_no_artifact(self) -> None:
        cases = {
            "wrong_head": {"authorized_head": "0" * 40},
            "wrong_tree": {"authorized_tree": "0" * 40},
            "wrong_manifest_sha": {"expected_manifest_sha256": "0" * 64},
        }
        for name, overrides in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(capture.ReferenceCaptureAbort):
                    self.run_capture(**overrides)
                self.assertFalse(self.artifact.exists())
        self.assert_aborts(
            boundary=RecordingBoundary(stdout=zero_turn_stdout(cost="1.0000000"))
        )
        self.assertEqual(sorted(self.root.glob("p01_reference_init.json")), [])

    def test_17_capture_creates_no_epoch_ledger_or_lock_state(self) -> None:
        self.run_capture()
        self.assertFalse(driver.CANONICAL_AUTHORITY_ROOT.exists())
        self.assertFalse(driver.CANONICAL_EPOCH_ROOT.exists())
        self.assertFalse(controller.PRODUCTION_LOCK_PATH.exists())
        self.assertEqual(
            sorted(entry.name for entry in self.root.iterdir()),
            ["held-out", "p01_reference_init.json", "workspaces"],
        )
        source = MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "EpochLock(",
            "BudgetLedger(",
            "provider_start_intent",
            "ensure_pair_reservations",
            "execute_pair(",
            "reserve(",
            "settle(",
            "CANONICAL_EPOCH_ROOT",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_18_fake_binary_proves_the_process_invocation_contract(self) -> None:
        script = self.root / "fake-provider"
        payload = zero_turn_stdout().decode("utf-8")
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "sys.stdin.buffer.read()\n"
            "sys.stdout.write(%r)\n" % payload,
            encoding="utf-8",
        )
        script.chmod(0o755)

        boundary = SpawningFakeBoundary(script)
        outcome = self.run_capture(boundary=boundary)
        self.assertTrue(self.artifact.is_file())
        request = boundary.requests[0]
        self.assertEqual(request.stdin_bytes, b"")
        self.assertEqual(request.cwd, request.session_root)
        self.assertTrue(Path(request.cwd).name.startswith("session-"))
        self.assertNotIn("--max-budget-usd", boundary.observed_argv)
        observed = list(boundary.observed_argv)
        self.assertEqual(observed[observed.index("--max-turns") + 1], "0")
        self.assertEqual(observed[0], str(script))
        self.assertEqual(outcome.record["observed_total_cost_usd"], "0.0000000")

    def test_18b_real_boundary_has_no_unsandboxed_fallback(self) -> None:
        workspace, opaque = runner.create_condition_neutral_workspace(
            self.workspaces, self.token
        )
        argv = capture.build_reference_argv(PROVIDER_BINARY_PATH)
        request = capture.CaptureProcessRequest(
            logical_argv=argv,
            cwd=str(workspace),
            stdin_bytes=b"",
            environment={},
            timeout_seconds=1.0,
            session_root=str(workspace),
            profile_path=str(controller.SANDBOX_PROFILE_PATH),
            sandbox_exec_path=str(controller.SANDBOX_EXEC_PATH),
        )
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=zero_turn_stdout(), stderr=b""
        )
        with mock.patch.object(
            capture.subprocess, "run", return_value=completed
        ) as spawn:
            result = capture.SandboxedZeroBudgetCapture()(request)
        spawn.assert_called_once()
        physical = list(spawn.call_args.args[0])
        self.assertEqual(physical[0], str(controller.SANDBOX_EXEC_PATH))
        self.assertEqual(physical[1], "-f")
        self.assertEqual(physical[3], "-D")
        self.assertEqual(physical[4], f"SESSION_ROOT={workspace.resolve()}")
        self.assertEqual(tuple(physical[5:]), argv)
        self.assertEqual(result.physical_argv, tuple(physical))

        missing_sandbox = capture.CaptureProcessRequest(
            logical_argv=argv,
            cwd=str(workspace),
            stdin_bytes=b"",
            environment={},
            timeout_seconds=1.0,
            session_root=str(workspace),
            profile_path=str(controller.SANDBOX_PROFILE_PATH),
            sandbox_exec_path=str(self.root / "no-such-sandbox-exec"),
        )
        with mock.patch.object(capture.subprocess, "run") as spawn:
            with self.assertRaises(controller.SandboxPolicyError):
                capture.SandboxedZeroBudgetCapture()(missing_sandbox)
        spawn.assert_not_called()

    def test_19_reference_workspace_is_the_off_arm_of_the_same_materializer(self) -> None:
        boundary = RecordingBoundary()
        outcome = self.run_capture(boundary=boundary)
        workspace = Path(outcome.workspace_path)
        self.assertFalse((workspace / production.CARRIER_DESTINATION).exists())
        state = runner.capture_git_state(workspace)
        self.assertTrue(state.state_resolved, msg=state.errors)
        manifest = execution_manifest()
        self.assertEqual(
            state.final_head_tree, manifest["tasks"][0]["initial_git_tree"]
        )
        self.assertTrue((workspace / "seqtools.py").is_file())
        held_out = sorted(self.hold_out.rglob("A01.py"))
        self.assertEqual(len(held_out), 1)
        with self.assertRaises(ValueError):
            held_out[0].resolve().relative_to(workspace.resolve())

    def test_20_source_authority_must_match_the_authorized_capture_state(self) -> None:
        self.assertEqual(
            capture.verify_source_authority(
                REPOSITORY_ROOT, authorized_head=self.head, authorized_tree=self.tree
            ),
            (self.head, self.tree),
        )
        for overrides in (
            {"authorized_head": "0" * 40},
            {"authorized_tree": "0" * 40},
            {"authorized_head": "not-a-git-oid"},
        ):
            with self.subTest(overrides=overrides):
                values = {
                    "authorized_head": self.head,
                    "authorized_tree": self.tree,
                }
                values.update(overrides)
                with self.assertRaises(capture.ReferenceCaptureAbort) as context:
                    capture.verify_source_authority(REPOSITORY_ROOT, **values)
                self.assertEqual(context.exception.reason, "SOURCE_AUTHORITY_MISMATCH")

    def test_21_record_carries_no_treatment_outcome_data(self) -> None:
        outcome = self.run_capture()
        self.assertEqual(set(outcome.record), set(production.REFERENCE_RECORD_KEYS))
        serialized = self.artifact.read_text(encoding="utf-8")
        for forbidden in (
            "disposition",
            "run_countable",
            "purity_pass",
            "terminal",
            "FABLE_ON",
            "FABLE_OFF",
            "fable-method",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, serialized)


class ReferenceArtifactPresenceTests(unittest.TestCase):
    def test_22_canonical_reference_artifact_is_not_created(self) -> None:
        self.assertFalse(production.REFERENCE_ARTIFACT_PATH.exists())
        self.assertEqual(
            capture.REFERENCE_ARTIFACT_PATH, production.REFERENCE_ARTIFACT_PATH
        )

    def test_23_capture_module_defines_one_shared_record_contract(self) -> None:
        self.assertIs(capture.REFERENCE_INIT_SCHEMA, production.REFERENCE_INIT_SCHEMA)
        self.assertIs(
            capture.canonical_reference_bytes, production.canonical_reference_bytes
        )
        self.assertIs(
            capture.validate_reference_record, production.validate_reference_record
        )
        self.assertEqual(capture.CAPTURE_MODE, "ZERO_TURN_INIT_ONLY")
        self.assertEqual(capture.REFERENCE_MAX_BUDGET_USD, "0.0000000")
        self.assertEqual(capture.REFERENCE_MAX_TURNS, 0)
        self.assertEqual(capture.REFERENCE_PROMPT, "")


class ProcessFailureDiagnosticTests(ReferenceCaptureTestCase):
    """A failed physical capture must stay fail-closed *and* stay explainable."""

    UNIQUE_STDOUT = b"\x00unique-capture-stdout-\xff-bytes\n"
    UNIQUE_STDERR = b"sandbox-exec: unique-capture-stderr-\xfe\ndeny file-write\n"
    UNIQUE_ARGV = (
        "/usr/bin/sandbox-exec",
        "-f",
        "/offline/claude-runtime.sb",
        "-D",
        "SESSION_ROOT=/offline/session-unique",
        "/offline/claude",
        "--max-budget-usd",
        "0.0000000",
        "--max-turns",
        "0",
    )

    def assert_non_authoritative(self, diagnostic: Mapping) -> None:
        self.assertEqual(diagnostic["authority"], "NON_AUTHORITATIVE")
        self.assertIs(diagnostic["is_reference_authority"], False)
        self.assertEqual(
            diagnostic["schema"], "FABLE_ABLATION_P01_CAPTURE_FAILURE_DIAGNOSTIC/v1"
        )
        self.assertNotEqual(diagnostic["schema"], capture.REFERENCE_INIT_SCHEMA)
        # A diagnostic must never satisfy the reference-record contract.
        with self.assertRaises(Exception):
            production.validate_reference_record(
                dict(diagnostic), expected_sealed_manifest_sha256=SEALED_SHA256
            )

    def test_24_nonzero_exit_preserves_the_process_failure_evidence(self) -> None:
        exception = self.assert_aborts(
            "AMBIGUOUS_PROCESS_OUTCOME",
            boundary=RecordingBoundary(
                stdout=self.UNIQUE_STDOUT,
                stderr=self.UNIQUE_STDERR,
                returncode=1,
                timed_out=False,
                physical_argv=self.UNIQUE_ARGV,
            ),
        )
        diagnostic = exception.diagnostic
        self.assertIsNotNone(diagnostic)
        self.assertEqual(diagnostic["returncode"], 1)
        self.assertIs(diagnostic["timed_out"], False)
        self.assertEqual(tuple(diagnostic["physical_argv"]), self.UNIQUE_ARGV)
        for stream, raw in (
            ("stdout", self.UNIQUE_STDOUT),
            ("stderr", self.UNIQUE_STDERR),
        ):
            with self.subTest(stream=stream):
                described = diagnostic[stream]
                self.assertEqual(
                    base64.b64decode(described["base64"]),
                    raw,
                    msg="captured stream must be exactly reversible",
                )
                self.assertEqual(described["length"], len(raw))
                self.assertEqual(described["sha256"], hashlib.sha256(raw).hexdigest())
        self.assert_non_authoritative(diagnostic)
        # The abort message itself is unchanged by the added evidence.
        self.assertEqual(
            str(exception),
            "AMBIGUOUS_PROCESS_OUTCOME:timed_out=False:returncode=1",
        )

    def test_25_timeout_preserves_the_process_failure_evidence(self) -> None:
        exception = self.assert_aborts(
            "AMBIGUOUS_PROCESS_OUTCOME",
            boundary=RecordingBoundary(
                stdout=b"",
                stderr=b"",
                returncode=-1,
                timed_out=True,
                physical_argv=self.UNIQUE_ARGV,
            ),
        )
        diagnostic = exception.diagnostic
        self.assertIsNotNone(diagnostic)
        self.assertIs(diagnostic["timed_out"], True)
        self.assertEqual(diagnostic["returncode"], -1)
        self.assertEqual(tuple(diagnostic["physical_argv"]), self.UNIQUE_ARGV)
        self.assertEqual(diagnostic["stdout"]["length"], 0)
        self.assertEqual(diagnostic["stderr"]["length"], 0)
        self.assertEqual(diagnostic["stdout"]["base64"], "")
        self.assert_non_authoritative(diagnostic)

    def test_26_successful_and_post_process_paths_are_unchanged(self) -> None:
        outcome = self.run_capture()
        self.assertTrue(self.artifact.is_file())
        self.assertEqual(outcome.record["schema"], capture.REFERENCE_INIT_SCHEMA)
        self.assertEqual(outcome.record["observed_total_cost_usd"], "0.0000000")
        for absent in ("authority", "is_reference_authority", "returncode", "timed_out"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, outcome.record)
        self.artifact.unlink()
        # A gate that fails *after* a healthy process is not a process failure
        # and must not be reframed as one.
        exception = self.assert_aborts(
            "NONZERO_OBSERVED_COST",
            boundary=RecordingBoundary(stdout=zero_turn_stdout(cost="1.0000000")),
        )
        self.assertIsNone(exception.diagnostic)

    def test_27_diagnostic_serializes_no_environment_or_credentials(self) -> None:
        # Neither an isolation key (HOME/TMPDIR/CLAUDE_CONFIG_DIR/PATH) nor a
        # secret-shaped key: those are rejected pre-flight by
        # compose_isolated_environment (test_33/test_34) before the process
        # boundary this test means to exercise is ever reached.  This fixture
        # instead proves build_process_failure_diagnostic's closed allowlist
        # holds for arbitrary *permitted* environment content too.
        sensitive = "kelvin-real-machine-should-never-leak"
        exception = self.assert_aborts(
            "AMBIGUOUS_PROCESS_OUTCOME",
            boundary=RecordingBoundary(
                stdout=b"",
                stderr=b"",
                returncode=1,
                physical_argv=self.UNIQUE_ARGV,
            ),
            environment={"OPERATOR_WORKSPACE_LABEL": sensitive},
        )
        diagnostic = exception.diagnostic
        self.assertEqual(
            sorted(diagnostic),
            [
                "authority",
                "is_reference_authority",
                "physical_argv",
                "returncode",
                "schema",
                "stderr",
                "stdout",
                "timed_out",
            ],
            msg="the diagnostic surface is a closed allowlist",
        )
        text = capture.canonical_diagnostic_text(diagnostic)
        for forbidden in (sensitive, "OPERATOR_WORKSPACE_LABEL", "environment"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)

    def test_28_diagnostic_text_is_one_deterministic_parseable_line(self) -> None:
        exception = self.assert_aborts(
            "AMBIGUOUS_PROCESS_OUTCOME",
            boundary=RecordingBoundary(
                stdout=self.UNIQUE_STDOUT,
                stderr=self.UNIQUE_STDERR,
                returncode=1,
                physical_argv=self.UNIQUE_ARGV,
            ),
        )
        diagnostic = exception.diagnostic
        text = capture.canonical_diagnostic_text(diagnostic)
        self.assertNotIn("\n", text, msg="multi-line streams must not break the line")
        # Determinism must survive a different key insertion order, otherwise
        # this assertion would hold trivially for any serializer.
        shuffled = {key: diagnostic[key] for key in reversed(list(diagnostic))}
        self.assertNotEqual(list(shuffled), list(diagnostic))
        self.assertEqual(capture.canonical_diagnostic_text(shuffled), text)
        parsed = json.loads(text)
        self.assertEqual(
            base64.b64decode(parsed["stdout"]["base64"]), self.UNIQUE_STDOUT
        )
        self.assertEqual(
            base64.b64decode(parsed["stderr"]["base64"]), self.UNIQUE_STDERR
        )
        self.assertEqual(parsed["authority"], "NON_AUTHORITATIVE")

    def test_29_abort_report_writes_the_diagnostic_after_the_abort_line(self) -> None:
        exception = self.assert_aborts(
            "AMBIGUOUS_PROCESS_OUTCOME",
            boundary=RecordingBoundary(
                stdout=self.UNIQUE_STDOUT,
                stderr=self.UNIQUE_STDERR,
                returncode=1,
                physical_argv=self.UNIQUE_ARGV,
            ),
        )
        stream = io.StringIO()
        capture.write_abort_report(stream, exception)
        lines = stream.getvalue().splitlines()
        self.assertEqual(len(lines), 2, msg="exactly the abort line plus one diagnostic")
        self.assertEqual(
            lines[0],
            "P01_REFERENCE_CAPTURE_ABORTED:ReferenceCaptureAbort:"
            "AMBIGUOUS_PROCESS_OUTCOME:timed_out=False:returncode=1",
            msg="the pre-existing abort line must stay byte-identical and first",
        )
        self.assertTrue(
            lines[1].startswith("P01_REFERENCE_CAPTURE_FAILURE_DIAGNOSTIC:"),
            msg="the diagnostic must follow the abort line, never precede it",
        )
        self.assertEqual(
            json.loads(lines[1].split(":", 1)[1])["authority"], "NON_AUTHORITATIVE"
        )

        # An abort with no process diagnostic still writes exactly one line.
        plain = io.StringIO()
        capture.write_abort_report(
            plain, capture.ReferenceCaptureAbort("SOME_REASON", "some_detail")
        )
        self.assertEqual(
            plain.getvalue(),
            "P01_REFERENCE_CAPTURE_ABORTED:ReferenceCaptureAbort:"
            "SOME_REASON:some_detail\n",
        )
        # A foreign exception type must not crash the report.
        foreign = io.StringIO()
        capture.write_abort_report(foreign, RuntimeError("boom"))
        self.assertEqual(
            foreign.getvalue(), "P01_REFERENCE_CAPTURE_ABORTED:RuntimeError:boom\n"
        )
        # The entrypoint must route the report to stderr, never stdout.
        self.assertIn(
            "write_abort_report(sys.stderr, exc)",
            MODULE_PATH.read_text(encoding="utf-8"),
        )

    def test_30_large_streams_are_preserved_without_truncation(self) -> None:
        big_stdout = bytes((index * 7 + 11) % 256 for index in range(128 * 1024))
        big_stderr = b"E" * (64 * 1024) + b"\n\xff\x00tail"
        exception = self.assert_aborts(
            "AMBIGUOUS_PROCESS_OUTCOME",
            boundary=RecordingBoundary(
                stdout=big_stdout,
                stderr=big_stderr,
                returncode=1,
                physical_argv=self.UNIQUE_ARGV,
            ),
        )
        diagnostic = exception.diagnostic
        for stream, raw in (("stdout", big_stdout), ("stderr", big_stderr)):
            with self.subTest(stream=stream):
                described = diagnostic[stream]
                self.assertEqual(
                    described["length"],
                    len(raw),
                    msg="a truncating encoder must not pass this assertion",
                )
                self.assertEqual(base64.b64decode(described["base64"]), raw)
                self.assertEqual(described["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertNotIn("\n", capture.canonical_diagnostic_text(diagnostic))


class IsolatedRuntimeEnvironmentTests(ReferenceCaptureTestCase):
    """The subprocess must never see the operator's real environment."""

    def test_31_composed_environment_is_isolated_under_the_workspace(self) -> None:
        boundary = RecordingBoundary()
        outcome = self.run_capture(boundary=boundary)
        environment = boundary.requests[0].environment
        self.assertEqual(
            sorted(environment),
            ["CLAUDE_CONFIG_DIR", "HOME", "PATH", "TMPDIR"],
            msg="no extra key beyond the mandatory isolation set was supplied",
        )
        self.assertEqual(environment["PATH"], "/usr/bin:/bin")
        workspace = Path(outcome.workspace_path).resolve()
        for key in ("HOME", "TMPDIR", "CLAUDE_CONFIG_DIR"):
            with self.subTest(key=key):
                resolved = Path(environment[key]).resolve()
                self.assertTrue(
                    resolved.is_relative_to(workspace),
                    msg=f"{key}={resolved} escapes the sandboxed workspace {workspace}",
                )
                self.assertTrue(resolved.is_dir())

    def test_32_empty_environment_never_falls_back_to_the_operator_home(self) -> None:
        boundary = RecordingBoundary()
        self.run_capture(boundary=boundary)
        environment = boundary.requests[0].environment
        self.assertNotEqual(
            environment, {}, msg="env={} must be structurally unreachable"
        )
        self.assertNotEqual(
            environment.get("HOME"),
            str(Path.home()),
            msg="an unset caller environment must not fall back to the real operator home",
        )

    def test_33_secret_shaped_environment_key_is_rejected_before_any_process_call(
        self,
    ) -> None:
        for key in (
            "ANTHROPIC_API_KEY",
            "CLAUDE_CODE_OAUTH_TOKEN",
            "AWS_SECRET_ACCESS_KEY",
        ):
            with self.subTest(key=key):
                boundary = RecordingBoundary()
                exception = self.assert_aborts(
                    "ENVIRONMENT_KEY_LOOKS_LIKE_A_SECRET",
                    boundary=boundary,
                    environment={key: "should-never-reach-a-process"},
                )
                self.assertEqual(exception.detail, key)
                self.assertEqual(
                    boundary.requests,
                    [],
                    msg="a poisoned environment must never reach the process boundary",
                )

    def test_34_isolation_key_override_is_rejected_before_any_process_call(
        self,
    ) -> None:
        boundary = RecordingBoundary()
        exception = self.assert_aborts(
            "ENVIRONMENT_OVERRIDES_ISOLATION_KEY",
            boundary=boundary,
            environment={"HOME": "/offline/operator-home"},
        )
        self.assertEqual(exception.detail, "HOME")
        self.assertEqual(boundary.requests, [])

    def test_35_isolated_runtime_directories_exist_and_pass_the_surface_audit(
        self,
    ) -> None:
        outcome = self.run_capture()
        self.assertTrue(outcome.surface_audit.passed)
        runtime_root = Path(outcome.workspace_path) / capture._ISOLATED_RUNTIME_DIRNAME
        self.assertTrue(runtime_root.is_dir())
        for name in ("home", "tmp", "config"):
            with self.subTest(name=name):
                self.assertTrue((runtime_root / name).is_dir())


class CredentialAllowlistTests(unittest.TestCase):
    """The declared per-run credential is the only secret-shaped key allowed.

    These exercise ``compose_isolated_environment`` directly, because the
    allowlist is a caller declaration and no capture entrypoint declares one
    today.  Every credential value here is synthetic: no real credential, host
    Claude config or Keychain state is read, written or required.
    """

    SYNTHETIC = "synthetic-not-a-real-credential"

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.counter = 0

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def workspace(self) -> Path:
        """A fresh workspace each time: the runtime dirs are exist_ok=False."""

        self.counter += 1
        path = self.root / f"workspace-{self.counter}"
        path.mkdir()
        return path

    def assert_aborts(self, reason: str, **overrides: Any):
        workspace = self.workspace()
        with self.assertRaises(capture.ReferenceCaptureAbort) as context:
            capture.compose_isolated_environment(workspace, **overrides)
        self.assertEqual(context.exception.reason, reason)
        return context.exception

    def test_36_declared_credential_key_reaches_the_isolated_environment(self) -> None:
        workspace = self.workspace()
        environment = capture.compose_isolated_environment(
            workspace,
            {"ANTHROPIC_API_KEY": self.SYNTHETIC},
            credential_keys={"ANTHROPIC_API_KEY"},
        )
        self.assertEqual(environment["ANTHROPIC_API_KEY"], self.SYNTHETIC)
        resolved = workspace.resolve()
        for key in ("HOME", "TMPDIR", "CLAUDE_CONFIG_DIR"):
            with self.subTest(key=key):
                self.assertTrue(
                    Path(environment[key]).resolve().is_relative_to(resolved),
                    msg=f"declaring a credential must not relax {key} isolation",
                )
        self.assertEqual(environment["PATH"], "/usr/bin:/bin")

    def test_37_undeclared_credential_key_is_still_rejected(self) -> None:
        for overrides in (
            {"extra_environment": {"ANTHROPIC_API_KEY": self.SYNTHETIC}},
            {
                "extra_environment": {"ANTHROPIC_API_KEY": self.SYNTHETIC},
                "credential_keys": (),
            },
            {
                "extra_environment": {"ANTHROPIC_API_KEY": self.SYNTHETIC},
                "credential_keys": {"CLAUDE_CODE_OAUTH_TOKEN"},
            },
        ):
            with self.subTest(overrides=sorted(overrides)):
                exception = self.assert_aborts(
                    "ENVIRONMENT_KEY_LOOKS_LIKE_A_SECRET", **overrides
                )
                self.assertEqual(exception.detail, "ANTHROPIC_API_KEY")

    def test_38_declaring_one_key_does_not_admit_another_secret_shaped_key(
        self,
    ) -> None:
        exception = self.assert_aborts(
            "ENVIRONMENT_KEY_LOOKS_LIKE_A_SECRET",
            extra_environment={
                "ANTHROPIC_API_KEY": self.SYNTHETIC,
                "AWS_SECRET_ACCESS_KEY": self.SYNTHETIC,
            },
            credential_keys={"ANTHROPIC_API_KEY"},
        )
        self.assertEqual(exception.detail, "AWS_SECRET_ACCESS_KEY")

    def test_39_declaring_an_isolation_key_never_makes_it_overridable(self) -> None:
        for key in ("HOME", "TMPDIR", "CLAUDE_CONFIG_DIR", "PATH"):
            with self.subTest(key=key):
                exception = self.assert_aborts(
                    "ENVIRONMENT_OVERRIDES_ISOLATION_KEY",
                    extra_environment={key: "/offline/operator-controlled"},
                    credential_keys={key},
                )
                self.assertEqual(exception.detail, key)

    def test_40_malformed_credential_declarations_fail_closed(self) -> None:
        # A bare string must not degrade the exact-match test into a substring
        # test: "API_KEY" is a substring of "ANTHROPIC_API_KEY" and would be
        # admitted by `key in credential_keys` without normalization.
        for declared, supplied in (
            ("ANTHROPIC_API_KEY", "API_KEY"),
            ("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
        ):
            with self.subTest(declared=declared, supplied=supplied):
                exception = self.assert_aborts(
                    "ENVIRONMENT_KEY_LOOKS_LIKE_A_SECRET",
                    extra_environment={supplied: self.SYNTHETIC},
                    credential_keys=declared,
                )
                self.assertEqual(exception.detail, supplied)

        # A non-iterable declaration raises before any runtime directory exists.
        workspace = self.workspace()
        with self.assertRaises(TypeError):
            capture.compose_isolated_environment(
                workspace,
                {"ANTHROPIC_API_KEY": self.SYNTHETIC},
                credential_keys=None,
            )
        self.assertFalse(
            (workspace / capture._ISOLATED_RUNTIME_DIRNAME).exists(),
            msg="a malformed declaration must fail before any filesystem effect",
        )


if __name__ == "__main__":
    unittest.main()
