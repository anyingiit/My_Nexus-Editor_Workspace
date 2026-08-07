from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import threading
import unittest

import yaml

from agents_v3.budget import BudgetExceeded, BudgetManager, InvalidSettlement
from agents_v3.config import ConfigError, approval_payload_sha256, load_config
from agents_v3.final_test import FinalTestStatus as BarrierStatus
from agents_v3.models import BudgetVector, RunPhase, TaskSpec, TaskStatus
from agents_v3.profiles import freeze_profile, load_profile, profile_hash
from agents_v3.reporting import render_anytime_report, render_status
from agents_v3.readiness import REQUIRED_RESOLVED_ARTIFACT_PATHS
from agents_v3.runtime import runtime_report_hash, sign_runtime_capability_report
from agents_v3.scheduler import DAGError, LeaseError, Scheduler, SchedulerError
from agents_v3.state_machine import InvalidTransition, validate_task_transition
from agents_v3.store import ImmutableRunError, SQLiteStore, StoreError
from agents_v3.security import capability_manifest_hash


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_A_BYTES = b"fixture candidate A\n"
CANDIDATE_B_BYTES = b"fixture candidate B\n"
CANDIDATE_A = sha256(CANDIDATE_A_BYTES).hexdigest()
CANDIDATE_B = sha256(CANDIDATE_B_BYTES).hexdigest()
TEST_LAUNCHER_KEY = b"agents-v3-test-launcher-key-32b"


def resolve_profile_fixture(value: dict[str, object]) -> None:
    def visit(item: object) -> None:
        if isinstance(item, dict):
            for key, child in list(item.items()):
                if key.endswith("_sha256") and child == "0" * 64:
                    item[key] = "a" * 64
                elif key == "harness_commit" and child == "0" * 40:
                    item[key] = "a" * 40
                elif key == "sandbox_image_digest" and child == "sha256:" + "0" * 64:
                    item[key] = "sha256:" + "a" * 64
                elif isinstance(child, str) and (
                    "REPLACE" in child.upper() or child.upper().startswith("EXAMPLE-")
                ):
                    item[key] = "fixture-v1"
                else:
                    visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)


def final_config() -> dict[str, object]:
    data = valid_config()
    data["mode"] = "final"
    data["dataset"]["knowledge_cutoff"] = "2026-01-31T00:00:00Z"
    data["approval"]["confirmed_at"] = "2026-08-01T00:00:00Z"
    data["metrics"].update(
        decision_accuracy_floor=0.0,
        reject_recall_floor=0.0,
        balanced_accuracy_floor=0.0,
    )
    data["sandbox"]["launcher_attestation_key_sha256"] = sha256(
        TEST_LAUNCHER_KEY
    ).hexdigest()
    data["approval"]["manifest_sha256"] = approval_payload_sha256(data)
    return data


def _set_profile_path(value: dict, path: str, replacement: str) -> None:
    current = value
    parts = path.split(".")
    for component in parts[:-1]:
        current = current[component]
    current[parts[-1]] = replacement


def frozen_bundle(candidate_hash: str, config) -> tuple[dict, dict, dict, bytes, dict[str, bytes]]:
    value = load_profile(PROJECT_ROOT / "config" / "evaluation_profile.example.yaml")
    value["profile_id"] = "controller-integration"
    value["run_id"] = config.run_id
    resolve_profile_fixture(value)
    candidate_bytes = {
        CANDIDATE_A: CANDIDATE_A_BYTES,
        CANDIDATE_B: CANDIDATE_B_BYTES,
    }.get(candidate_hash)
    if candidate_bytes is None:
        raise ValueError("fixture candidate hash is not backed by bytes")
    resolved_artifacts = {
        path: (
            candidate_bytes
            if path == "candidate.core_sha256"
            else f"resolved fixture artifact: {path}\n".encode("utf-8")
        )
        for path in REQUIRED_RESOLVED_ARTIFACT_PATHS
    }
    for path, content in resolved_artifacts.items():
        _set_profile_path(value, path, sha256(content).hexdigest())
    value["knowledge"]["cutoff"] = config.raw["dataset"]["knowledge_cutoff"]
    value["judge"].update(
        provider=config.evaluation_limit.provider,
        model=config.evaluation_limit.model,
        max_output_tokens=config.evaluation_limit.max_output_tokens,
    )
    value["packing"]["max_input_tokens"] = config.evaluation_limit.max_input_tokens
    retry_policy = {
        key: list(item) if isinstance(item, tuple) else item
        for key, item in config.raw["retry"].items()
    }
    value["execution"]["retry_policy_sha256"] = sha256(
        json.dumps(
            retry_policy, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    ).hexdigest()
    value["analysis"].update(
        alpha=config.raw["statistics"]["alpha"],
        target_power=config.raw["statistics"]["target_power"],
        bootstrap_resamples=config.raw["statistics"]["bootstrap_resamples"],
        bootstrap_seed=config.raw["statistics"]["bootstrap_seed"],
        min_effect_pp=config.raw["metrics"]["min_effect_pp"],
        decision_accuracy_floor=config.raw["metrics"]["decision_accuracy_floor"],
        reject_recall_floor=config.raw["metrics"]["reject_recall_floor"],
        balanced_accuracy_floor=config.raw["metrics"]["balanced_accuracy_floor"],
        multiplicity=config.raw["statistics"]["multiplicity"],
        ci_pass_subset_role="DIAGNOSTIC",
        required_effective_groups=10,
        required_sample_count=10,
        required_reject_count=5,
        required_approve_count=5,
    )
    capability = json.loads(
        (PROJECT_ROOT / "sandbox" / "capability_manifest.example.json").read_text(
            encoding="utf-8"
        )
    )
    report = yaml.safe_load(
        (PROJECT_ROOT / "config" / "runtime_capability_report.example.yaml").read_text(
            encoding="utf-8"
        )
    )
    report["run_id"] = config.run_id
    report["generated_at"] = "2026-08-07T00:00:00Z"
    report["generated_by"] = "trusted-launcher-fixture"
    report["run_config_sha256"] = config.fingerprint()
    report["capability_manifest_sha256"] = capability_manifest_hash(capability)
    for role in report["roles"]:
        name = role["role"]
        policy = capability["roles"][name]
        role["process_identity"] = f"uid-fixture-{name}"
        role["executable"] = f"/opt/agents-v3/{name}"
        role["network"] = {
            "mode": policy["network"]["mode"],
            "allowed_hosts": policy["network"]["hosts"],
            "allowed_methods": policy["network"]["methods"],
        }
        role["mounts"] = [
            dict(item)
            for item in policy["mounts"]
        ]
        role["writable_paths"] = [
            item["target"] for item in policy["mounts"] if item["access"] in {"rw", "wo"}
        ]
        role["probes"] = {key: True for key in role["probes"]}
        if name == "untrusted_code_runner":
            role["image_digest"] = "sha256:" + "a" * 64
            controls = policy["controls"]["resources"]
            role["resource_limits"].update(
                cpus=controls["cpus"],
                memory_mb=controls["memory_mb"],
                pids=controls["pids"],
                disk_mb=controls["disk_mb"],
                output_mb=config.raw["sandbox"]["max_output_mb"],
                wall_clock_seconds=controls["wall_seconds"],
            )
    report["all_required_probes_passed"] = True
    report = sign_runtime_capability_report(
        report, TEST_LAUNCHER_KEY, nonce="fixture-single-use-nonce-0001"
    )
    lock_bytes = (PROJECT_ROOT / "requirements.lock").read_bytes()
    lock_hash = sha256(lock_bytes).hexdigest()
    value["runtime"].update(
        run_config_sha256=config.fingerprint(),
        runtime_capability_report_sha256=runtime_report_hash(report),
        capability_manifest_sha256=capability_manifest_hash(capability),
        dependency_lock_sha256=lock_hash,
        sandbox_image_digest="sha256:" + "a" * 64,
    )
    profile = freeze_profile(value, frozen_at="2026-08-07T00:01:00Z")
    return profile, report, capability, lock_bytes, resolved_artifacts


def barrier_result(profile: dict, sample: str, arm: str, decision: str, repeat: int = 0) -> dict:
    return {
        "schema_version": "3.0.0",
        "run_id": profile["run_id"],
        "profile_sha256": profile["profile_sha256"],
        "sample_id": sample,
        "split_group_id": f"group-{sample}",
        "arm": arm,
        "repeat_index": repeat,
        "cache_key": f"{sample}-{arm}-{repeat}",
        "packed_input_sha256": "f" * 64,
        "decision": decision,
        "reasons": [],
        "schema_valid": True,
        "truncated": False,
        "provider_request_id": "fixture",
        "usage": {"input_tokens": 1, "output_tokens": 1, "cash_usd": 0.0, "provider_quota": 0.0},
        "latency_ms": 1,
        "attempt": 1,
        "canonical": True,
        "created_at": "2026-08-07T00:00:00Z",
    }


def valid_config() -> dict[str, object]:
    return yaml.safe_load((PROJECT_ROOT / "config" / "run_config.example.yaml").read_text(encoding="utf-8"))


def small_config() -> dict[str, object]:
    data = valid_config()
    data["tokens"] = {"max_total": 100, "soft_watermark": 60, "closeout_reserve": 20}
    data["cash_usd"] = {"max_incremental": 1.0, "soft_watermark": 0.6, "closeout_reserve": 0.1}
    data["provider_quota"] = {"unit": "usd_equivalent", "max_total": 1.0, "soft_watermark": 0.6, "closeout_reserve": 0.1}
    data["tool_calls"] = {"max_total": 100, "soft_watermark": 60, "closeout_reserve": 10}
    stage_tokens = {"protocol": 80, "census": 0, "vertical_slice": 0, "dev": 0, "freeze_test": 0, "closeout": 20}
    stage_cash = {"protocol": 0.8, "census": 0.0, "vertical_slice": 0.0, "dev": 0.0, "freeze_test": 0.0, "closeout": 0.2}
    stage_tools = {"protocol": 80, "census": 0, "vertical_slice": 0, "dev": 0, "freeze_test": 0, "closeout": 20}
    for name, stage in data["stage_caps"].items():
        stage["max_tokens"] = stage_tokens[name]
        stage["max_cash_usd"] = stage_cash[name]
        stage["max_provider_quota_usd"] = stage_cash[name]
        stage["max_tool_calls"] = stage_tools[name]
    return data


def admission_vector(tokens: int = 10, *, tool_calls: int = 1) -> BudgetVector:
    return BudgetVector(tokens=tokens, wall_seconds=5, tool_calls=tool_calls)


class ConfigTests(unittest.TestCase):
    def test_authoritative_example_loads_and_includes_tool_caps(self) -> None:
        config = load_config(PROJECT_ROOT / "config" / "run_config.example.yaml")
        self.assertEqual(config.mode, "demo")
        self.assertEqual(config.ceiling.tool_calls, 500)
        self.assertEqual(config.phase_caps["census"].tool_calls, 20)

    def test_legacy_flat_shape_and_unknown_fields_are_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            load_config({"schema_version": "3.0.0", "run_id": "bypass"})
        data = valid_config()
        data["max_total_tokens"] = 999999
        with self.assertRaises(ConfigError):
            load_config(data)

    def test_cross_field_ratios_and_demo_safety_fail_closed(self) -> None:
        data = valid_config()
        data["dataset"]["train_ratio"] = 0.50
        with self.assertRaises(ConfigError):
            load_config(data)
        data = valid_config()
        data["providers"]["evaluation"]["provider"] = "network_provider"
        with self.assertRaises(ConfigError):
            load_config(data)
        data = valid_config()
        data["sandbox"]["untrusted_code_execution"] = "enabled_after_capability_gate"
        with self.assertRaises(ConfigError):
            load_config(data)

    def test_live_hash_mismatch_and_expired_deadline_are_rejected(self) -> None:
        now = datetime(2026, 8, 7, tzinfo=timezone.utc)
        data = valid_config()
        data["mode"] = "development"
        data["dataset"]["knowledge_cutoff"] = "2026-01-01T00:00:00Z"
        data["approval"]["confirmed_at"] = "2026-08-01T00:00:00Z"
        data["time"]["deadline_at"] = "2026-08-06T00:00:00Z"
        data["approval"]["manifest_sha256"] = "0" * 64
        with self.assertRaisesRegex(ConfigError, "does not match"):
            load_config(data, now=now)
        data["approval"]["manifest_sha256"] = approval_payload_sha256(data)
        with self.assertRaisesRegex(ConfigError, "future"):
            load_config(data, now=now)

    def test_canonical_approval_hash_accepts_live_manifest(self) -> None:
        now = datetime(2026, 8, 7, tzinfo=timezone.utc)
        data = valid_config()
        data["mode"] = "probe"
        data["dataset"]["knowledge_cutoff"] = "2026-01-01T00:00:00Z"
        data["approval"]["confirmed_at"] = "2026-08-01T00:00:00Z"
        data["time"]["deadline_at"] = "2099-01-01T00:00:00Z"
        data["approval"]["manifest_sha256"] = approval_payload_sha256(data)
        self.assertEqual(load_config(data, now=now).approval_payload_hash, data["approval"]["manifest_sha256"])


class ControllerFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp.name) / "state.sqlite3")
        self.config = load_config(small_config())
        self.start = datetime(2098, 12, 31, 22, 0, tzinfo=timezone.utc)
        self.manager = BudgetManager(self.store, self.config, started_at=self.start)
        self.store.transition_run_phase(RunPhase.PROTOCOL)
        self.scheduler = Scheduler(
            self.store,
            config=self.config,
            budget_manager=self.manager,
            lease_seconds=10,
            max_parallel_tasks=2,
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def admit(self, task_id: str, worker: str = "worker", *, tokens: int = 10, key: str | None = None, now: datetime | None = None):
        return self.scheduler.admit_and_claim(
            task_id=task_id,
            worker_id=worker,
            provider="deterministic_stub",
            model="fixture-worker-v1",
            worst_case=admission_vector(tokens),
            input_tokens=max(0, tokens - 1),
            output_tokens=1,
            idempotency_key=key or f"{task_id}-attempt",
            now=now or self.start,
        )

    def settle(self, lease, *, actual: BudgetVector | None = None) -> None:
        self.manager.commit(lease.reservation_id, actual or admission_vector(), now=self.start)


class BudgetTests(ControllerFixture):
    def test_concurrent_stage_reservations_cannot_pierce_cap(self) -> None:
        barrier = threading.Barrier(2)
        outcomes: list[str] = []

        def reserve(name: str) -> None:
            barrier.wait()
            try:
                self.manager.reserve(
                    name,
                    "deterministic_stub",
                    "fixture-worker-v1",
                    admission_vector(50),
                    input_tokens=49,
                    output_tokens=1,
                    reservation_id=name,
                    now=self.start,
                )
            except BudgetExceeded:
                outcomes.append("denied")
            else:
                outcomes.append("reserved")

        threads = [threading.Thread(target=reserve, args=(f"r{i}",)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertCountEqual(outcomes, ["reserved", "denied"])
        self.assertEqual(self.manager.snapshot(now=self.start).reserved.tool_calls, 1)
        self.assertTrue(self.store.verify_event_chain())

    def test_settlement_is_bounded_and_tool_calls_persist(self) -> None:
        reservation = self.manager.reserve(
            "t1", "deterministic_stub", "fixture-worker-v1", admission_vector(20, tool_calls=3),
            input_tokens=19, output_tokens=1, reservation_id="same", now=self.start,
        )
        with self.assertRaises(InvalidSettlement):
            self.manager.commit(reservation.reservation_id, admission_vector(21, tool_calls=4), now=self.start)
        committed = self.manager.commit(
            reservation.reservation_id,
            BudgetVector(tokens=10, wall_seconds=2, tool_calls=2),
            now=self.start,
        )
        self.assertEqual(committed.actual.tool_calls, 2)

    def test_phase_cap_and_phase_deadline_are_enforced(self) -> None:
        with self.assertRaises(BudgetExceeded) as cap:
            self.manager.reserve(
                "huge", "deterministic_stub", "fixture-worker-v1", admission_vector(81),
                input_tokens=32_000, output_tokens=1, reservation_id="huge", now=self.start,
            )
        self.assertTrue(any("stage:protocol" in reason for reason in cap.exception.reasons))
        deadline = self.config.phase_deadlines[RunPhase.PROTOCOL]
        with self.assertRaises(BudgetExceeded) as late:
            self.manager.reserve(
                "late", "deterministic_stub", "fixture-worker-v1", admission_vector(),
                input_tokens=9, output_tokens=1, reservation_id="late", now=deadline,
            )
        self.assertIn("phase_deadline:PROTOCOL", late.exception.reasons)

    def test_provider_model_and_per_call_caps_are_enforced(self) -> None:
        with self.assertRaises(BudgetExceeded) as wrong_model:
            self.manager.reserve(
                "wrong", "deterministic_stub", "unapproved-model", admission_vector(),
                input_tokens=9, output_tokens=1, reservation_id="wrong", now=self.start,
            )
        self.assertIn("provider_model_not_allowlisted", wrong_model.exception.reasons)
        with self.assertRaises(BudgetExceeded) as output_cap:
            self.manager.reserve(
                "oversize", "deterministic_stub", "fixture-worker-v1",
                BudgetVector(tokens=5000, wall_seconds=5, tool_calls=1),
                input_tokens=1, output_tokens=4001, reservation_id="oversize", now=self.start,
            )
        self.assertIn("output_tokens_per_call", output_cap.exception.reasons)

    def test_caller_cannot_label_exploration_critical_to_spend_reserve(self) -> None:
        with self.assertRaises(BudgetExceeded) as denied:
            self.manager.reserve(
                "reserve-bypass",
                "deterministic_stub",
                "fixture-worker-v1",
                admission_vector(81),
                input_tokens=32_000,
                output_tokens=1,
                purpose="critical",
                reservation_id="reserve-bypass",
                now=self.start,
            )
        self.assertIn("global:tokens", denied.exception.reasons)

        with self.assertRaises(BudgetExceeded) as wrong_phase:
            self.manager.reserve(
                "false-final",
                "deterministic_stub",
                "fixture-judge-v1",
                admission_vector(10),
                input_tokens=9,
                output_tokens=1,
                purpose="final_test",
                reservation_id="false-final",
                now=self.start,
            )
        self.assertIn("purpose_phase_mismatch", wrong_phase.exception.reasons)


class SchedulerTests(ControllerFixture):
    def test_direct_claim_is_disabled_and_atomic_admission_is_idempotent(self) -> None:
        self.scheduler.add_tasks([TaskSpec("collect", RunPhase.PROTOCOL)])
        with self.assertRaises(SchedulerError):
            self.scheduler.claim_ready("worker")
        lease = self.admit("collect", key="claim-1")
        duplicate = self.admit("collect", key="claim-1")
        self.assertEqual(lease, duplicate)
        self.assertEqual(self.manager.snapshot(now=self.start).reserved.tool_calls, 1)

    def test_atomic_admission_complete_and_dependency_promotion(self) -> None:
        self.scheduler.add_tasks([
            TaskSpec("collect", RunPhase.PROTOCOL),
            TaskSpec("score", RunPhase.PROTOCOL, dependencies=("collect",)),
        ])
        first = self.admit("collect", "worker-a")
        with self.assertRaises(LeaseError):
            self.scheduler.complete("collect", "worker-a")
        self.manager.commit(first.reservation_id, admission_vector(), now=self.start)
        self.scheduler.complete("collect", "worker-a", {"records": 7}, now=self.start)
        self.assertEqual(self.scheduler.get_task("score")["status"], TaskStatus.READY.value)
        self.assertEqual(self.admit("score", "worker-b").task_id, "score")

    def test_phase_mismatch_rejected_without_reservation(self) -> None:
        self.scheduler.add_tasks([TaskSpec("future", RunPhase.CENSUS)])
        with self.assertRaisesRegex(SchedulerError, "phase"):
            self.admit("future")
        rows = self.store.connection.execute("SELECT * FROM reservations").fetchall()
        self.assertEqual(rows, [])

    def test_atomic_cap_denial_rolls_back_reservation_and_lease(self) -> None:
        self.scheduler.add_tasks([TaskSpec("too-large", RunPhase.PROTOCOL)])
        with self.assertRaises(BudgetExceeded):
            self.scheduler.admit_and_claim(
                task_id="too-large", worker_id="worker",
                provider="deterministic_stub", model="fixture-worker-v1",
                worst_case=admission_vector(81), input_tokens=32_000,
                output_tokens=1, idempotency_key="denied", now=self.start,
            )
        self.assertEqual(self.scheduler.get_task("too-large")["status"], "READY")
        self.assertIsNone(self.store.connection.execute(
            "SELECT 1 FROM reservations WHERE reservation_id='denied'"
        ).fetchone())

    def test_stale_leases_charge_worst_case_retry_then_fail(self) -> None:
        self.scheduler.add_tasks([TaskSpec("fragile", RunPhase.PROTOCOL, max_attempts=2)])
        first = self.admit("fragile", now=self.start)
        self.scheduler.recover_stale(now=self.start + timedelta(seconds=11))
        self.assertEqual(self.scheduler.get_task("fragile")["status"], TaskStatus.READY.value)
        charged = self.store.connection.execute(
            "SELECT status, tool_calls_actual FROM reservations WHERE reservation_id=?", (first.reservation_id,)
        ).fetchone()
        self.assertEqual((charged["status"], charged["tool_calls_actual"]), ("COMMITTED", 1))
        self.admit("fragile", "worker-2", key="fragile-retry", now=self.start + timedelta(seconds=11))
        self.scheduler.recover_stale(now=self.start + timedelta(seconds=22))
        self.assertEqual(self.scheduler.get_task("fragile")["status"], TaskStatus.FAILED.value)

    def test_lease_recovery_survives_controller_restart(self) -> None:
        self.scheduler.add_tasks([TaskSpec("restart", RunPhase.PROTOCOL, max_attempts=2)])
        lease = self.admit("restart", now=self.start)
        database_path = Path(self.store.path)
        self.store.close()
        self.store = SQLiteStore(database_path)
        self.manager = BudgetManager(self.store, self.config)
        self.scheduler = Scheduler(
            self.store, config=self.config, budget_manager=self.manager,
            lease_seconds=10, max_parallel_tasks=2,
        )
        self.assertEqual(
            self.scheduler.recover_stale(now=self.start + timedelta(seconds=11)),
            ["restart"],
        )
        row = self.store.connection.execute(
            "SELECT status FROM reservations WHERE reservation_id=?", (lease.reservation_id,)
        ).fetchone()
        self.assertEqual(row["status"], "COMMITTED")

    def test_split_supersedes_parent_and_rewires_descendant(self) -> None:
        self.scheduler.add_tasks([
            TaskSpec("large", RunPhase.PROTOCOL),
            TaskSpec("downstream", RunPhase.PROTOCOL, dependencies=("large",)),
        ])
        lease = self.admit("large")
        self.manager.commit(lease.reservation_id, admission_vector(), now=self.start)
        self.scheduler.split(
            "large", "worker",
            [TaskSpec("part-a", RunPhase.PROTOCOL), TaskSpec("part-b", RunPhase.PROTOCOL)],
            reason="bounded packets", now=self.start,
        )
        self.assertEqual(self.scheduler.get_task("large")["status"], TaskStatus.SUPERSEDED.value)
        self.assertEqual(self.scheduler.get_task("downstream")["dependencies"], ["part-a", "part-b"])

    def test_cycle_is_rejected_atomically(self) -> None:
        with self.assertRaises(DAGError):
            self.scheduler.add_tasks([
                TaskSpec("a", RunPhase.PROTOCOL, dependencies=("b",)),
                TaskSpec("b", RunPhase.PROTOCOL, dependencies=("a",)),
            ])
        self.assertEqual(self.store.task_counts()[TaskStatus.PENDING.value], 0)


class CandidateTerminalAndReportingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = SQLiteStore(self.root / "state.sqlite3")
        self.config = load_config(final_config())
        self.manager = BudgetManager(self.store, self.config)

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def advance_to_freeze(self) -> None:
        for phase in (
            RunPhase.PROTOCOL,
            RunPhase.CENSUS,
            RunPhase.VERTICAL_SLICE,
            RunPhase.FREEZE,
        ):
            self.store.transition_run_phase(phase)

    def test_best_so_far_dev_patience_and_final_barrier(self) -> None:
        self.store.register_candidate(CANDIDATE_A, "candidates/a.md", 0.4, {"accuracy": 0.4})
        self.store.promote_best_candidate(CANDIDATE_A)
        self.store.register_candidate(CANDIDATE_B, "candidates/b.md", 0.8, {"accuracy": 0.8})
        self.store.promote_best_candidate(CANDIDATE_B)
        self.assertEqual(self.store.best_candidate()["candidate_hash"], CANDIDATE_B)
        first = self.store.record_dev_iteration(
            candidate_hash=CANDIDATE_A, hypothesis="h1", gain_pp=0.1, cash_microusd=10_000,
            elapsed_seconds=5,
        )
        second = self.store.record_dev_iteration(
            candidate_hash=CANDIDATE_B, hypothesis="h2", gain_pp=0.2, cash_microusd=10_000,
            elapsed_seconds=5,
        )
        self.assertFalse(first["stop"])
        self.assertTrue(second["stop"])
        with self.assertRaises(StoreError):
            self.store.record_dev_iteration(
                candidate_hash="c", hypothesis="h3", gain_pp=2, cash_microusd=1,
                elapsed_seconds=1,
            )
        self.advance_to_freeze()
        profile, runtime, capability, lock_bytes, artifacts = frozen_bundle(CANDIDATE_B, self.config)
        self.store.freeze_final_test(
            CANDIDATE_B,
            profile,
            config=self.config,
            runtime_report=runtime,
            capability_manifest=capability,
            dependency_lock_bytes=lock_bytes,
            launcher_attestation_key=TEST_LAUNCHER_KEY,
            resolved_artifacts=artifacts,
        )
        with self.assertRaises(ImmutableRunError):
            self.store.register_candidate("c" * 64, "candidates/c.md", 0.9, {})

    def test_success_requires_completed_final_test_evidence(self) -> None:
        with self.assertRaises(ImmutableRunError):
            self.store.mark_terminal("SUCCEEDED", reason="manual bypass")
        self.store.register_candidate(CANDIDATE_A, "candidate.md", 1.0, {})
        self.store.promote_best_candidate(CANDIDATE_A)
        self.advance_to_freeze()
        profile, runtime, capability, lock_bytes, artifacts = frozen_bundle(CANDIDATE_A, self.config)
        self.store.freeze_final_test(
            CANDIDATE_A,
            profile,
            config=self.config,
            runtime_report=runtime,
            capability_manifest=capability,
            dependency_lock_bytes=lock_bytes,
            launcher_attestation_key=TEST_LAUNCHER_KEY,
            resolved_artifacts=artifacts,
        )
        barrier = self.store.start_final_test(
            self.root / "final-test.json",
            expected_sample_ids=[f"s{index}" for index in range(10)],
        )
        with self.assertRaises(StoreError):
            self.store.finish_final_test(reason="caller cannot self-report success")
        truth = [
            {
                "sample_id": f"s{index}",
                "label": "APPROVE" if index % 2 == 0 else "REQUEST_CHANGES",
                "split_group_id": f"group-s{index}",
                "aggregate_ci_status": "CI_PASS",
            }
            for index in range(10)
        ]
        for item in truth:
            for arm in profile["execution"]["arms"]:
                prediction = item["label"] if arm == "CANDIDATE" else (
                    "REQUEST_CHANGES" if item["label"] == "APPROVE" else "APPROVE"
                )
                for repeat in range(profile["execution"]["repeats"]):
                    barrier.checkpoint_result(
                        barrier_result(profile, item["sample_id"], arm, prediction, repeat)
                    )
        barrier.finalize(truth=truth)
        self.assertEqual(barrier.status, BarrierStatus.SUCCESS)
        self.store.finish_final_test(reason="frozen scorer and complete tensor passed")
        self.assertEqual(self.store.get_run_state()["terminal_status"], "SUCCESS")

    def test_store_revalidates_bundle_instead_of_trusting_cli(self) -> None:
        self.store.register_candidate(CANDIDATE_A, "candidate.md", 1.0, {})
        self.store.promote_best_candidate(CANDIDATE_A)
        self.advance_to_freeze()
        profile, runtime, capability, lock_bytes, artifacts = frozen_bundle(CANDIDATE_A, self.config)
        profile["runtime"]["dependency_lock_sha256"] = "b" * 64
        profile["profile_sha256"] = profile_hash(profile)
        with self.assertRaisesRegex(StoreError, "readiness bundle"):
            self.store.freeze_final_test(
                CANDIDATE_A,
                profile,
                config=self.config,
                runtime_report=runtime,
                capability_manifest=capability,
                dependency_lock_bytes=lock_bytes,
                launcher_attestation_key=TEST_LAUNCHER_KEY,
                resolved_artifacts=artifacts,
            )
        self.assertEqual(self.store.get_run_state()["final_test_status"], "NOT_FROZEN")

        forged_lock = b"PyYAML==0.0.0\n"
        profile, runtime, capability, _, artifacts = frozen_bundle(CANDIDATE_A, self.config)
        profile["runtime"]["dependency_lock_sha256"] = sha256(forged_lock).hexdigest()
        profile["profile_sha256"] = profile_hash(profile)
        with self.assertRaisesRegex(StoreError, "approved_lock_mismatch"):
            self.store.freeze_final_test(
                CANDIDATE_A,
                profile,
                config=self.config,
                runtime_report=runtime,
                capability_manifest=capability,
                dependency_lock_bytes=forged_lock,
                launcher_attestation_key=TEST_LAUNCHER_KEY,
                resolved_artifacts=artifacts,
            )

        profile, runtime, capability, lock_bytes, artifacts = frozen_bundle(
            CANDIDATE_A, self.config
        )
        runtime["generated_by"] = "candidate-forged-launcher"
        runtime["roles"][0]["process_identity"] = "self-asserted"
        runtime["manifest_sha256"] = runtime_report_hash(runtime)
        profile["runtime"]["runtime_capability_report_sha256"] = runtime_report_hash(runtime)
        profile["profile_sha256"] = profile_hash(profile)
        with self.assertRaisesRegex(StoreError, "invalid_attestation"):
            self.store.freeze_final_test(
                CANDIDATE_A,
                profile,
                config=self.config,
                runtime_report=runtime,
                capability_manifest=capability,
                dependency_lock_bytes=lock_bytes,
                launcher_attestation_key=TEST_LAUNCHER_KEY,
                resolved_artifacts=artifacts,
            )

        profile, runtime, capability, lock_bytes, artifacts = frozen_bundle(
            CANDIDATE_A, self.config
        )
        artifacts = dict(artifacts)
        artifacts["dataset.split_manifest_sha256"] = b"post-freeze replacement"
        with self.assertRaisesRegex(StoreError, "artifact_bytes_mismatch"):
            self.store.freeze_final_test(
                CANDIDATE_A,
                profile,
                config=self.config,
                runtime_report=runtime,
                capability_manifest=capability,
                dependency_lock_bytes=lock_bytes,
                launcher_attestation_key=TEST_LAUNCHER_KEY,
                resolved_artifacts=artifacts,
            )

    def test_status_reports_freshness_and_tool_budget(self) -> None:
        status_path = self.root / "STATUS.md"
        report_path = self.root / "ANYTIME.md"
        status = render_status(self.store, self.config, status_path)
        render_anytime_report(self.store, self.config, report_path)
        self.assertIn("tool_calls", status["budget"]["remaining"])
        self.assertIn("status_stale", status["run"])
        self.assertTrue(status["ledger"]["checksum_chain_valid"])

    def test_transition_table_has_no_terminal_escape(self) -> None:
        with self.assertRaises(InvalidTransition):
            validate_task_transition(TaskStatus.SUCCEEDED, TaskStatus.READY)


if __name__ == "__main__":
    unittest.main()
