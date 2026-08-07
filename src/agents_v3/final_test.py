"""One-way final-test barrier over a complete nested V3 profile."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from enum import StrEnum
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence

from .evaluation import evaluate_three_arm
from .profiles import (
    SchemaValidationError,
    canonical_bytes,
    canonical_sha256,
    validate_contract,
    validate_resolved_frozen_profile,
)


class FinalTestStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    VALID_TEST_PASSED = "SUCCESS"  # compatibility alias; external name is SUCCESS
    VALID_TEST_FAILED = "VALID_TEST_FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"
    INFRA_INCOMPLETE = "INFRA_INCOMPLETE"
    PROTOCOL_INVALID = "PROTOCOL_INVALID"


class FinalTestStateError(RuntimeError):
    """Raised for an illegal final-test transition."""


class ProtocolViolation(FinalTestStateError):
    """Raised after an immutable run is terminally invalidated."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_hash(value: Any) -> str:
    return canonical_sha256(value)


def profile_fingerprint(profile: Mapping[str, Any]) -> str:
    return str(validate_resolved_frozen_profile(profile)["profile_sha256"])


def _checksum_state(state: Mapping[str, Any]) -> str:
    return canonical_hash({key: value for key, value in state.items() if key != "state_checksum"})


def _key_tuple(value: Sequence[Any]) -> tuple[str, str, int]:
    if isinstance(value, (str, bytes)) or len(value) != 3:
        raise ValueError("result key must be (sample_id, arm, repeat_index)")
    sample_id, arm, repeat_index = value
    if not isinstance(sample_id, str) or not sample_id:
        raise ValueError("result key sample_id must be non-empty")
    if arm not in {"EMPTY", "REPO_BASELINE", "CANDIDATE"}:
        raise ValueError("result key arm is invalid")
    if isinstance(repeat_index, bool) or not isinstance(repeat_index, int) or repeat_index < 0:
        raise ValueError("result key repeat_index must be a non-negative integer")
    return sample_id, str(arm), repeat_index


def _key_token(key: tuple[str, str, int]) -> str:
    return canonical_bytes(list(key)).decode("utf-8")


class FinalTestBarrier:
    """Persist exactly one immutable profile and its expected result tensor."""

    schema_version = "3.0.0"

    def __init__(self, path: Path, state: dict[str, Any]) -> None:
        self.path = path
        self._state = state

    @classmethod
    def start(
        cls,
        path: str | os.PathLike[str],
        *,
        profile: Mapping[str, Any],
        expected_sample_ids: Sequence[str] | None = None,
        expected_keys: Sequence[Sequence[Any]] | None = None,
        run_id: str | None = None,
        candidate_hash: str | None = None,
        dataset_hash: str | None = None,
    ) -> "FinalTestBarrier":
        target = Path(path)
        canonical_profile = validate_resolved_frozen_profile(profile)
        bound_run_id = canonical_profile["run_id"]
        if run_id is not None and run_id != bound_run_id:
            raise ValueError("run_id does not match frozen profile")
        if candidate_hash is not None and candidate_hash != canonical_profile["candidate"]["core_sha256"]:
            raise ValueError("candidate_hash does not match frozen profile")
        if dataset_hash is not None and dataset_hash != canonical_profile["dataset"]["eligibility_manifest_sha256"]:
            raise ValueError("dataset_hash does not match frozen profile")
        if expected_keys is not None and expected_sample_ids is not None:
            raise ValueError("provide expected_sample_ids or expected_keys, not both")
        if expected_keys is None:
            if (
                expected_sample_ids is None
                or isinstance(expected_sample_ids, (str, bytes))
                or not expected_sample_ids
            ):
                raise ValueError("expected_sample_ids must be a non-empty sequence")
            sample_ids = sorted(str(value) for value in expected_sample_ids)
            if any(not value for value in sample_ids) or len(set(sample_ids)) != len(sample_ids):
                raise ValueError("expected sample IDs must be non-empty and unique")
            expected = [
                (sample_id, arm, repeat)
                for sample_id in sample_ids
                for arm in canonical_profile["execution"]["arms"]
                for repeat in range(canonical_profile["execution"]["repeats"])
            ]
        else:
            expected = [_key_tuple(value) for value in expected_keys]
            if not expected or len(set(expected)) != len(expected):
                raise ValueError("expected_keys must be non-empty and unique")
            declared_arms = set(canonical_profile["execution"]["arms"])
            repeats = canonical_profile["execution"]["repeats"]
            if any(arm not in declared_arms or repeat >= repeats for _, arm, repeat in expected):
                raise ValueError("expected key falls outside the frozen execution profile")
            samples = sorted({sample for sample, _, _ in expected})
            complete = {
                (sample, arm, repeat)
                for sample in samples
                for arm in canonical_profile["execution"]["arms"]
                for repeat in range(repeats)
            }
            if set(expected) != complete:
                raise ValueError("expected_keys must be the complete sample x arm x repeat tensor")
        expected = sorted(expected)
        immutable = {
            "run_id": bound_run_id,
            "profile": canonical_profile,
            "profile_sha256": canonical_profile["profile_sha256"],
            "expected_keys": [list(value) for value in expected],
            "expected_keys_sha256": canonical_hash([list(value) for value in expected]),
        }
        if target.exists():
            return cls.open(target, profile=canonical_profile, expected_keys=expected)
        now = _now()
        state: dict[str, Any] = {
            "schema_version": cls.schema_version,
            "status": FinalTestStatus.RUNNING.value,
            "immutable": immutable,
            "results": {},
            "resume_count": 0,
            "infra_failures": [],
            "protocol_violations": [],
            "created_at": now,
            "updated_at": now,
            "finalized_at": None,
            "report": None,
            "report_hash": None,
        }
        state["state_checksum"] = _checksum_state(state)
        barrier = cls(target, state)
        barrier._write()
        return barrier

    @classmethod
    def open(
        cls,
        path: str | os.PathLike[str],
        *,
        profile: Mapping[str, Any] | None = None,
        expected_keys: Sequence[Sequence[Any]] | None = None,
        run_id: str | None = None,
        candidate_hash: str | None = None,
        dataset_hash: str | None = None,
        expected_sample_ids: Sequence[str] | None = None,
    ) -> "FinalTestBarrier":
        target = Path(path)
        try:
            state = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProtocolViolation(f"final-test state cannot be read: {exc}") from exc
        if not isinstance(state, dict) or state.get("state_checksum") != _checksum_state(state):
            raise ProtocolViolation("final-test state checksum mismatch; preserve for audit")
        barrier = cls(target, state)
        requested: dict[str, Any] = {}
        if profile is not None:
            canonical_profile = validate_resolved_frozen_profile(profile)
            requested["profile"] = canonical_profile
            requested["profile_sha256"] = canonical_profile["profile_sha256"]
        if run_id is not None:
            requested["run_id"] = run_id
        stored_profile = state.get("immutable", {}).get("profile", {})
        if candidate_hash is not None and candidate_hash != stored_profile.get("candidate", {}).get("core_sha256"):
            barrier._invalidate("candidate_hash changed during final-test resume")
        if dataset_hash is not None and dataset_hash != stored_profile.get("dataset", {}).get("eligibility_manifest_sha256"):
            barrier._invalidate("dataset_hash changed during final-test resume")
        if expected_sample_ids is not None:
            arms = stored_profile.get("execution", {}).get("arms", [])
            repeats = stored_profile.get("execution", {}).get("repeats", 0)
            expected_keys = [
                (str(sample), str(arm), repeat)
                for sample in sorted(expected_sample_ids)
                for arm in arms
                for repeat in range(repeats)
            ]
        if expected_keys is not None:
            normalized = sorted(_key_tuple(value) for value in expected_keys)
            requested["expected_keys"] = [list(value) for value in normalized]
            requested["expected_keys_sha256"] = canonical_hash(requested["expected_keys"])
        for key, value in requested.items():
            if state.get("immutable", {}).get(key) != value:
                barrier._invalidate(f"immutable field {key!r} changed during final-test resume")
        return barrier

    @property
    def status(self) -> FinalTestStatus:
        value = self._state["status"]
        if value == "VALID_TEST_PASSED":  # read-only compatibility with an early prototype
            value = "SUCCESS"
        return FinalTestStatus(value)

    @property
    def state(self) -> dict[str, Any]:
        return deepcopy(self._state)

    @property
    def pending_keys(self) -> tuple[tuple[str, str, int], ...]:
        completed = set(self._state["results"])
        return tuple(
            _key_tuple(value)
            for value in self._state["immutable"]["expected_keys"]
            if _key_token(_key_tuple(value)) not in completed
        )

    @property
    def pending_sample_ids(self) -> tuple[str, ...]:
        return tuple(sorted({sample for sample, _, _ in self.pending_keys}))

    def checkpoint_result(self, result: Mapping[str, Any]) -> None:
        if self.status is not FinalTestStatus.RUNNING:
            raise FinalTestStateError(f"cannot checkpoint while status is {self.status.value}")
        if not isinstance(result, Mapping):
            self._invalidate("evaluation result is not an object")
        try:
            canonical = validate_contract(result, "evaluation_result.schema.json")
        except SchemaValidationError as exc:
            self._invalidate(f"evaluation result failed published schema: {exc}")
            return
        key = (canonical["sample_id"], canonical["arm"], canonical["repeat_index"])
        if list(key) not in self._state["immutable"]["expected_keys"]:
            self._invalidate(f"result key {key!r} was not preregistered")
        profile = self._state["immutable"]["profile"]
        if canonical["run_id"] != profile["run_id"]:
            self._invalidate("result run_id does not match frozen profile")
        if canonical["profile_sha256"] != profile["profile_sha256"]:
            self._invalidate("result profile_sha256 does not match frozen profile")
        prior_groups = {
            value["split_group_id"]
            for value in self._state["results"].values()
            if value["sample_id"] == canonical["sample_id"]
        }
        if prior_groups and canonical["split_group_id"] not in prior_groups:
            self._invalidate("one sample was assigned conflicting split_group_id values")
        token = _key_token(key)
        existing = self._state["results"].get(token)
        if existing is not None:
            if existing != canonical:
                self._invalidate(f"conflicting canonical result for {key!r}")
            return
        self._state["results"][token] = canonical
        self._persist_update()

    def mark_infra_incomplete(self, *, category: str, message: str) -> None:
        if self.status is not FinalTestStatus.RUNNING:
            raise FinalTestStateError(f"cannot mark infrastructure failure from {self.status.value}")
        if not category or not message:
            raise ValueError("infrastructure category and message are required")
        self._state["infra_failures"].append({"category": category, "message": message, "at": _now()})
        self._state["status"] = FinalTestStatus.INFRA_INCOMPLETE.value
        self._persist_update()

    def resume(self) -> None:
        if self.status is not FinalTestStatus.INFRA_INCOMPLETE:
            raise FinalTestStateError("only INFRA_INCOMPLETE may resume")
        self._state["status"] = FinalTestStatus.RUNNING.value
        self._state["resume_count"] += 1
        self._persist_update()

    def _aggregate_results(self) -> dict[str, list[dict[str, Any]]]:
        profile = self._state["immutable"]["profile"]
        vote_policy = profile["execution"]["vote_policy"]
        by_pair: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for result in self._state["results"].values():
            by_pair.setdefault((result["sample_id"], result["arm"]), []).append(result)
        arms: dict[str, list[dict[str, Any]]] = {arm: [] for arm in profile["execution"]["arms"]}
        for (sample_id, arm), values in sorted(by_pair.items()):
            values.sort(key=lambda item: item["repeat_index"])
            if vote_policy == "FIRST_CANONICAL":
                chosen = values[0]["decision"]
            else:
                approvals = sum(item["decision"] == "APPROVE" for item in values)
                chosen = "APPROVE" if approvals > len(values) / 2 else "REQUEST_CHANGES"
            reasons = sorted(
                {
                    reason["reason_id"]
                    for item in values
                    if item["decision"] == chosen
                    for reason in item["reasons"]
                }
            )
            arms[arm].append(
                {"sample_id": sample_id, "prediction": chosen, "reason_codes": reasons}
            )
        return arms

    def finalize(
        self,
        *,
        truth: Mapping[str, str] | Sequence[Mapping[str, Any]],
        observed_review_reasons: Mapping[str, Sequence[str]] | None = None,
    ) -> dict[str, Any]:
        """Run the frozen scorer; callers cannot self-report PASS/FAIL."""

        if self.status is not FinalTestStatus.RUNNING:
            raise FinalTestStateError(f"cannot finalize while status is {self.status.value}")
        if self.pending_keys:
            self.mark_infra_incomplete(
                category="missing_results",
                message=f"finalization attempted with {len(self.pending_keys)} result keys incomplete",
            )
            raise FinalTestStateError("incomplete final test is INFRA_INCOMPLETE; resume same profile")
        profile = self._state["immutable"]["profile"]
        analysis = profile["analysis"]
        if not isinstance(truth, Mapping):
            truth_groups = {str(item.get("sample_id")): str(item.get("split_group_id")) for item in truth}
            result_groups = {
                value["sample_id"]: value["split_group_id"]
                for value in self._state["results"].values()
            }
            mismatch = sorted(
                sample for sample, group in result_groups.items() if truth_groups.get(sample) != group
            )
            if mismatch:
                self._invalidate(f"result/truth split_group_id mismatch for samples: {mismatch}")
        report = evaluate_three_arm(
            truth,
            self._aggregate_results(),
            thresholds=analysis,
            power_plan=analysis,
            candidate_arm="CANDIDATE",
            bootstrap_resamples=analysis["bootstrap_resamples"],
            seed=analysis["bootstrap_seed"],
            observed_review_reasons=observed_review_reasons,
        )
        status = report["status"]
        if status not in {"SUCCESS", "VALID_TEST_FAILED", "INCONCLUSIVE"}:
            self._invalidate(f"scorer returned an illegal terminal outcome: {status!r}")
        self._state["status"] = FinalTestStatus(status).value
        self._state["finalized_at"] = _now()
        self._state["report"] = report
        self._state["report_hash"] = canonical_hash(report)
        self._persist_update()
        return deepcopy(report)

    def _invalidate(self, reason: str) -> None:
        self._state.setdefault("protocol_violations", []).append({"reason": reason, "at": _now()})
        self._state["status"] = FinalTestStatus.PROTOCOL_INVALID.value
        self._state["finalized_at"] = _now()
        self._persist_update()
        raise ProtocolViolation(reason)

    def _persist_update(self) -> None:
        self._state["updated_at"] = _now()
        self._state["state_checksum"] = _checksum_state(self._state)
        self._write()

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = canonical_bytes(self._state) + b"\n"
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=self.path.parent, prefix=f".{self.path.name}.", delete=False
            ) as handle:
                temporary = handle.name
                os.chmod(temporary, 0o600)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and os.path.exists(temporary):
                os.unlink(temporary)
