"""Authoritative, fail-closed V3 run-manifest loading.

There is deliberately one accepted shape: ``schemas/run_config.schema.json``.
Legacy flat dictionaries are not a public compatibility path because they can
silently omit security, provider, approval, and tool-call controls.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping, Sequence

import yaml

from .models import BudgetVector, ProviderCallLimit, RunPhase, usd_to_microusd
from .resources import schema_path
from .schema_gate import SchemaValidationError, assert_schema


class ConfigError(ValueError):
    """The run manifest is incomplete, ambiguous, expired, or unsafe."""


_PLACEHOLDERS = {"", "required", "__required__", "todo", "tbd", "null", "none", "unlimited", "infinite", "inf"}
_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_DURATION_RE = re.compile(r"^(?P<number>[0-9]+)(?P<unit>s|m|h|d)?$")

_TOP_KEYS = {
    "schema_version", "run_id", "mode", "dependency_lock_sha256", "approval", "time", "tokens",
    "cash_usd", "provider_quota", "tool_calls", "stage_caps", "iteration",
    "concurrency", "status", "retry", "providers", "dataset", "metrics",
    "statistics", "stability", "sandbox", "final_test",
}
_STAGES = ("protocol", "census", "vertical_slice", "dev", "freeze_test", "closeout")
_STAGE_PHASES = {
    "protocol": (RunPhase.PROTOCOL,),
    "census": (RunPhase.CENSUS,),
    "vertical_slice": (RunPhase.VERTICAL_SLICE,),
    "dev": (RunPhase.DEVELOPMENT,),
    "freeze_test": (RunPhase.FREEZE, RunPhase.FINAL_TEST),
    "closeout": (RunPhase.CLOSEOUT,),
}
_ERROR_CLASSES = {
    "TRANSPORT", "RATE_LIMIT", "HTTP_4XX", "HTTP_5XX", "TIMEOUT",
    "EMPTY_RESPONSE", "TRUNCATED", "CONTEXT_OVERFLOW", "JSON_INVALID",
    "SCHEMA_INVALID", "SAFETY_REFUSAL", "SEMANTIC_INVALID",
}


@dataclass(frozen=True)
class RunConfig:
    schema_version: str
    run_id: str
    mode: str
    deadline_at: datetime
    max_wall_clock_seconds: int
    max_total_tokens: int
    max_incremental_cash_microusd: int
    max_provider_cash_microusd: int
    max_tool_calls: int
    closeout_reserve_time_seconds: int
    closeout_reserve_tokens: int
    closeout_reserve_cash_microusd: int
    closeout_reserve_provider_cash_microusd: int
    closeout_reserve_tool_calls: int
    max_dev_iterations: int
    low_gain_patience: int
    min_dev_gain_pp: float
    max_cash_per_gain_pp_microusd: int
    phase_deadlines: Mapping[RunPhase, datetime]
    phase_caps: Mapping[str, BudgetVector]
    phase_stages: Mapping[RunPhase, str]
    soft_watermark_tokens: int
    soft_watermark_incremental_cash_microusd: int
    soft_watermark_provider_cash_microusd: int
    soft_watermark_tool_calls: int
    soft_watermark_wall_seconds: int
    max_parallel_tasks: int
    lease_seconds: int
    max_status_staleness_seconds: int
    evaluation_limit: ProviderCallLimit
    non_evaluation_limits: Mapping[tuple[str, str], ProviderCallLimit]
    approval_payload_hash: str
    manifest_fingerprint: str
    raw: Mapping[str, Any] = field(repr=False, compare=False)

    @property
    def ceiling(self) -> BudgetVector:
        return BudgetVector(
            tokens=self.max_total_tokens,
            incremental_cash_microusd=self.max_incremental_cash_microusd,
            provider_cash_microusd=self.max_provider_cash_microusd,
            wall_seconds=self.max_wall_clock_seconds,
            tool_calls=self.max_tool_calls,
        )

    @property
    def closeout_reserve(self) -> BudgetVector:
        return BudgetVector(
            tokens=self.closeout_reserve_tokens,
            incremental_cash_microusd=self.closeout_reserve_cash_microusd,
            provider_cash_microusd=self.closeout_reserve_provider_cash_microusd,
            wall_seconds=self.closeout_reserve_time_seconds,
            tool_calls=self.closeout_reserve_tool_calls,
        )

    @property
    def soft_watermark(self) -> BudgetVector:
        return BudgetVector(
            tokens=self.soft_watermark_tokens,
            incremental_cash_microusd=self.soft_watermark_incremental_cash_microusd,
            provider_cash_microusd=self.soft_watermark_provider_cash_microusd,
            wall_seconds=self.soft_watermark_wall_seconds,
            tool_calls=self.soft_watermark_tool_calls,
        )

    def fingerprint(self) -> str:
        return self.manifest_fingerprint

    def stage_for(self, phase: RunPhase | str) -> str:
        try:
            return self.phase_stages[RunPhase(phase)]
        except KeyError as exc:
            raise ConfigError(f"phase {phase!s} has no budget stage") from exc

    def call_limit(self, provider: str, model: str, purpose: str) -> ProviderCallLimit:
        if purpose in {"evaluation", "judge", "final_test"}:
            limit = self.evaluation_limit
            if (provider, model) != (limit.provider, limit.model):
                raise ConfigError("evaluation calls must use the frozen evaluation provider/model")
            return limit
        try:
            return self.non_evaluation_limits[(provider, model)]
        except KeyError as exc:
            raise ConfigError(f"provider/model is not allowlisted: {provider}/{model}") from exc


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"manifest must be canonical JSON-compatible data: {exc}") from exc


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_deep_freeze(item) for item in value)
    return value


def canonical_approval_payload(data: Mapping[str, Any]) -> dict[str, Any]:
    """Return the exact payload a human approves.

    The digest field is removed (rather than nulled) so computing and then
    inserting the digest is non-recursive and language independent.
    """

    payload = deepcopy(dict(data))
    approval = payload.get("approval")
    if not isinstance(approval, dict):
        raise ConfigError("approval must be a mapping")
    approval.pop("manifest_sha256", None)
    return payload


def approval_payload_sha256(data: Mapping[str, Any]) -> str:
    return sha256(_canonical_bytes(canonical_approval_payload(data))).hexdigest()


def _is_placeholder(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip().lower() in _PLACEHOLDERS)


def _reject_unknown(mapping: Mapping[str, Any], allowed: set[str], field: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise ConfigError(f"{field} contains unknown fields: {', '.join(unknown)}")
    missing = sorted(allowed - set(mapping))
    if missing:
        raise ConfigError(f"{field} is missing required fields: {', '.join(missing)}")


def _map(data: Mapping[str, Any], key: str, allowed: set[str]) -> Mapping[str, Any]:
    value = data.get(key)
    if not isinstance(value, Mapping):
        raise ConfigError(f"{key} must be a mapping")
    _reject_unknown(value, allowed, key)
    return value


def _positive_int(value: Any, name: str, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{name} must be an integer")
    if value < (0 if allow_zero else 1):
        raise ConfigError(f"{name} must be {'non-negative' if allow_zero else 'positive'}")
    return value


def _number(value: Any, name: str, *, minimum: Decimal | None = None, maximum: Decimal | None = None, exclusive_minimum: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise ConfigError(f"{name} must be numeric")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ConfigError(f"{name} must be numeric") from exc
    if not parsed.is_finite():
        raise ConfigError(f"{name} must be finite")
    if minimum is not None and (parsed <= minimum if exclusive_minimum else parsed < minimum):
        raise ConfigError(f"{name} is below its allowed minimum")
    if maximum is not None and parsed > maximum:
        raise ConfigError(f"{name} exceeds its allowed maximum")
    return parsed


def _duration(value: Any, name: str) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return _positive_int(value, name)
    if not isinstance(value, str):
        raise ConfigError(f"{name} must be seconds or a duration such as 30m")
    match = _DURATION_RE.fullmatch(value.strip().lower())
    if not match:
        raise ConfigError(f"{name} must be seconds or a duration such as 30m")
    multiplier = {None: 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[match.group("unit")]
    return _positive_int(int(match.group("number")) * multiplier, name)


def _datetime(value: Any, name: str) -> datetime:
    if not isinstance(value, str) or _is_placeholder(value):
        raise ConfigError(f"{name} must be an ISO-8601 timestamp with timezone")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConfigError(f"{name} must be an ISO-8601 timestamp with timezone") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ConfigError(f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _cash(value: Any, name: str, *, positive: bool = False) -> int:
    try:
        result = usd_to_microusd(value, field_name=name)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    if positive and result <= 0:
        raise ConfigError(f"{name} must be positive")
    return result


def _load(source: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(source, Mapping):
        data = deepcopy(dict(source))
    else:
        path = Path(source)
        if not path.is_file():
            raise ConfigError(f"configuration file does not exist: {path}")
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ConfigError(f"cannot parse configuration: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("configuration root must be a mapping")
    return data


def _provider_limit(value: Mapping[str, Any], field: str, *, require_failover: bool) -> ProviderCallLimit:
    allowed = {"provider", "model", "max_input_tokens_per_call", "max_output_tokens_per_call", "max_cash_usd_per_call"}
    if require_failover:
        allowed.add("automatic_failover")
    _reject_unknown(value, allowed, field)
    if require_failover and value["automatic_failover"] is not False:
        raise ConfigError("providers.evaluation.automatic_failover must be false")
    provider = value["provider"]
    model = value["model"]
    if not isinstance(provider, str) or _is_placeholder(provider) or not isinstance(model, str) or _is_placeholder(model):
        raise ConfigError(f"{field} provider/model must be non-empty")
    return ProviderCallLimit(
        provider=provider,
        model=model,
        max_input_tokens=_positive_int(value["max_input_tokens_per_call"], f"{field}.max_input_tokens_per_call"),
        max_output_tokens=_positive_int(value["max_output_tokens_per_call"], f"{field}.max_output_tokens_per_call"),
        max_cash_microusd=_cash(value["max_cash_usd_per_call"], f"{field}.max_cash_usd_per_call", positive=True),
    )


def load_config(source: str | Path | Mapping[str, Any], *, now: datetime | None = None) -> RunConfig:
    """Load the sole authoritative V3 manifest and enforce cross-field rules."""

    data = _load(source)
    run_schema_path = schema_path("run_config.schema.json")
    try:
        schema = json.loads(run_schema_path.read_text(encoding="utf-8"))
        assert_schema(data, schema)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"authoritative run schema is unavailable or invalid: {exc}") from exc
    except SchemaValidationError as exc:
        raise ConfigError(f"run manifest does not conform to the authoritative schema: {exc}") from exc
    _reject_unknown(data, _TOP_KEYS, "manifest")
    if data["schema_version"] != "3.0.0":
        raise ConfigError("schema_version must be 3.0.0")
    if not isinstance(data["run_id"], str) or _RUN_ID_RE.fullmatch(data["run_id"]) is None:
        raise ConfigError("run_id must match ^[A-Za-z0-9._-]{1,128}$")
    mode = data["mode"]
    if mode not in {"demo", "probe", "development", "final"}:
        raise ConfigError("mode must be demo, probe, development, or final")
    if not isinstance(data["dependency_lock_sha256"], str) or _HASH_RE.fullmatch(
        data["dependency_lock_sha256"]
    ) is None:
        raise ConfigError("dependency_lock_sha256 must be a lowercase SHA-256")

    approval = _map(data, "approval", {"confirmed", "confirmed_by", "confirmed_at", "manifest_sha256"})
    if approval["confirmed"] is not True:
        raise ConfigError("approval.confirmed must be true")
    if not isinstance(approval["confirmed_by"], str) or _is_placeholder(approval["confirmed_by"]):
        raise ConfigError("approval.confirmed_by is required")
    confirmed_at = _datetime(approval["confirmed_at"], "approval.confirmed_at")
    instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if confirmed_at > instant and mode != "demo":
        raise ConfigError("approval.confirmed_at cannot be in the future")
    payload_hash = approval_payload_sha256(data)
    supplied_hash = approval["manifest_sha256"]
    if mode != "demo":
        if not isinstance(supplied_hash, str) or _HASH_RE.fullmatch(supplied_hash) is None:
            raise ConfigError("non-demo runs require approval.manifest_sha256")
        if supplied_hash != payload_hash:
            raise ConfigError("approval.manifest_sha256 does not match the canonical approval payload")
    elif supplied_hash is not None:
        if not isinstance(supplied_hash, str) or supplied_hash != payload_hash:
            raise ConfigError("demo approval hash, when supplied, must match the canonical payload")

    time = _map(data, "time", {"deadline_at", "max_wall_clock_seconds", "soft_watermark_seconds", "closeout_reserve_seconds"})
    deadline = _datetime(time["deadline_at"], "time.deadline_at")
    if mode != "demo" and deadline <= instant:
        raise ConfigError("live run deadline_at must be in the future")
    max_wall = _duration(time["max_wall_clock_seconds"], "time.max_wall_clock_seconds")
    soft_wall = _duration(time["soft_watermark_seconds"], "time.soft_watermark_seconds")
    reserve_wall = _duration(time["closeout_reserve_seconds"], "time.closeout_reserve_seconds")

    integer_budgets: dict[str, tuple[int, int, int]] = {}
    for key in ("tokens", "tool_calls"):
        section = _map(data, key, {"max_total", "soft_watermark", "closeout_reserve"})
        integer_budgets[key] = (
            _positive_int(section["max_total"], f"{key}.max_total"),
            _positive_int(section["soft_watermark"], f"{key}.soft_watermark", allow_zero=True),
            _positive_int(section["closeout_reserve"], f"{key}.closeout_reserve"),
        )
    cash = _map(data, "cash_usd", {"max_incremental", "soft_watermark", "closeout_reserve"})
    provider_quota = _map(data, "provider_quota", {"unit", "max_total", "soft_watermark", "closeout_reserve"})
    if provider_quota["unit"] != "usd_equivalent":
        raise ConfigError("provider_quota.unit must be usd_equivalent")
    cash_values = (
        _cash(cash["max_incremental"], "cash_usd.max_incremental", positive=True),
        _cash(cash["soft_watermark"], "cash_usd.soft_watermark"),
        _cash(cash["closeout_reserve"], "cash_usd.closeout_reserve", positive=True),
    )
    provider_values = (
        _cash(provider_quota["max_total"], "provider_quota.max_total", positive=True),
        _cash(provider_quota["soft_watermark"], "provider_quota.soft_watermark"),
        _cash(provider_quota["closeout_reserve"], "provider_quota.closeout_reserve", positive=True),
    )
    for name, values in {
        "wall": (max_wall, soft_wall, reserve_wall),
        "tokens": integer_budgets["tokens"],
        "tool_calls": integer_budgets["tool_calls"],
        "cash": cash_values,
        "provider_quota": provider_values,
    }.items():
        maximum, soft, reserve = values
        if soft > maximum or reserve > maximum:
            raise ConfigError(f"{name} soft watermark/reserve exceeds its hard ceiling")

    stages = _map(data, "stage_caps", set(_STAGES))
    phase_caps: dict[str, BudgetVector] = {}
    phase_stages: dict[RunPhase, str] = {}
    stage_seconds: dict[str, int] = {}
    for stage_name in _STAGES:
        section = stages[stage_name]
        if not isinstance(section, Mapping):
            raise ConfigError(f"stage_caps.{stage_name} must be a mapping")
        _reject_unknown(section, {"max_wall_clock_seconds", "max_tokens", "max_cash_usd", "max_provider_quota_usd", "max_tool_calls"}, f"stage_caps.{stage_name}")
        cap = BudgetVector(
            tokens=_positive_int(section["max_tokens"], f"stage_caps.{stage_name}.max_tokens", allow_zero=True),
            incremental_cash_microusd=_cash(section["max_cash_usd"], f"stage_caps.{stage_name}.max_cash_usd"),
            provider_cash_microusd=_cash(section["max_provider_quota_usd"], f"stage_caps.{stage_name}.max_provider_quota_usd"),
            wall_seconds=_duration(section["max_wall_clock_seconds"], f"stage_caps.{stage_name}.max_wall_clock_seconds"),
            tool_calls=_positive_int(section["max_tool_calls"], f"stage_caps.{stage_name}.max_tool_calls", allow_zero=True),
        )
        phase_caps[stage_name] = cap
        stage_seconds[stage_name] = cap.wall_seconds
        for phase in _STAGE_PHASES[stage_name]:
            phase_stages[phase] = stage_name
    summed = BudgetVector()
    for cap in phase_caps.values():
        summed += cap
    if not summed.fits_within(BudgetVector(integer_budgets["tokens"][0], cash_values[0], provider_values[0], max_wall, integer_budgets["tool_calls"][0])):
        raise ConfigError("sum of stage caps exceeds one or more global ceilings")

    # Deadlines are pre-registered stage boundaries inside the immutable run window.
    window_start = deadline - timedelta(seconds=max_wall)
    elapsed = 0
    phase_deadlines: dict[RunPhase, datetime] = {}
    for stage_name in _STAGES:
        elapsed += stage_seconds[stage_name]
        stage_deadline = window_start + timedelta(seconds=elapsed)
        for phase in _STAGE_PHASES[stage_name]:
            phase_deadlines[phase] = stage_deadline

    iteration = _map(data, "iteration", {"max_dev_iterations", "low_gain_patience", "min_dev_gain_pp", "max_cash_per_gain_pp"})
    max_dev = _positive_int(iteration["max_dev_iterations"], "iteration.max_dev_iterations")
    patience = _positive_int(iteration["low_gain_patience"], "iteration.low_gain_patience")
    if patience > max_dev:
        raise ConfigError("low_gain_patience cannot exceed max_dev_iterations")
    min_gain = _number(iteration["min_dev_gain_pp"], "iteration.min_dev_gain_pp", minimum=Decimal(0))
    max_cash_gain = _cash(iteration["max_cash_per_gain_pp"], "iteration.max_cash_per_gain_pp", positive=True)

    concurrency = _map(data, "concurrency", {"max_total_slots", "max_worker_slots", "evaluator_consumes_slot", "min_slots", "rate_limit_backoff_threshold", "failure_rate_reduce_threshold"})
    total_slots = _positive_int(concurrency["max_total_slots"], "concurrency.max_total_slots")
    worker_slots = _positive_int(concurrency["max_worker_slots"], "concurrency.max_worker_slots")
    min_slots = _positive_int(concurrency["min_slots"], "concurrency.min_slots")
    if worker_slots > total_slots or min_slots > worker_slots:
        raise ConfigError("concurrency slot bounds are inconsistent")
    if concurrency["evaluator_consumes_slot"] is not True or worker_slots + 1 > total_slots:
        raise ConfigError("an evaluator slot must be explicitly reserved from max_total_slots")
    for key in ("rate_limit_backoff_threshold", "failure_rate_reduce_threshold"):
        _number(concurrency[key], f"concurrency.{key}", minimum=Decimal(0), maximum=Decimal(1))

    status = _map(data, "status", {"max_staleness_seconds", "heartbeat_seconds", "lease_seconds"})
    staleness = _positive_int(status["max_staleness_seconds"], "status.max_staleness_seconds")
    heartbeat = _positive_int(status["heartbeat_seconds"], "status.heartbeat_seconds")
    lease = _positive_int(status["lease_seconds"], "status.lease_seconds")
    if heartbeat >= lease or staleness < heartbeat:
        raise ConfigError("status requires heartbeat < lease and max_staleness >= heartbeat")

    retry = _map(data, "retry", {"max_attempts", "base_backoff_seconds", "max_backoff_seconds", "retryable_classes", "nonretryable_classes"})
    _positive_int(retry["max_attempts"], "retry.max_attempts")
    base_backoff = _positive_int(retry["base_backoff_seconds"], "retry.base_backoff_seconds", allow_zero=True)
    max_backoff = _positive_int(retry["max_backoff_seconds"], "retry.max_backoff_seconds", allow_zero=True)
    if base_backoff > max_backoff:
        raise ConfigError("retry base_backoff_seconds cannot exceed max_backoff_seconds")
    retryable, nonretryable = retry["retryable_classes"], retry["nonretryable_classes"]
    for name, values in (("retryable_classes", retryable), ("nonretryable_classes", nonretryable)):
        if not isinstance(values, list) or len(values) != len(set(values)) or not set(values) <= _ERROR_CLASSES:
            raise ConfigError(f"retry.{name} contains duplicates or unknown classes")
    if set(retryable) & set(nonretryable):
        raise ConfigError("retryable and nonretryable classes must be disjoint")

    providers = _map(data, "providers", {"evaluation", "non_evaluation"})
    evaluation_raw = providers["evaluation"]
    non_eval_raw = providers["non_evaluation"]
    if not isinstance(evaluation_raw, Mapping) or not isinstance(non_eval_raw, Mapping):
        raise ConfigError("provider sections must be mappings")
    evaluation_limit = _provider_limit(evaluation_raw, "providers.evaluation", require_failover=True)
    _reject_unknown(non_eval_raw, {"allowed", "max_input_tokens_per_call", "max_output_tokens_per_call", "max_cash_usd_per_call"}, "providers.non_evaluation")
    allowed = non_eval_raw["allowed"]
    if not isinstance(allowed, list) or not allowed:
        raise ConfigError("providers.non_evaluation.allowed must be non-empty")
    shared_input = _positive_int(non_eval_raw["max_input_tokens_per_call"], "providers.non_evaluation.max_input_tokens_per_call")
    shared_output = _positive_int(non_eval_raw["max_output_tokens_per_call"], "providers.non_evaluation.max_output_tokens_per_call")
    shared_cash = _cash(non_eval_raw["max_cash_usd_per_call"], "providers.non_evaluation.max_cash_usd_per_call", positive=True)
    non_eval_limits: dict[tuple[str, str], ProviderCallLimit] = {}
    for index, item in enumerate(allowed):
        if not isinstance(item, Mapping):
            raise ConfigError(f"providers.non_evaluation.allowed[{index}] must be a mapping")
        _reject_unknown(item, {"provider", "model"}, f"providers.non_evaluation.allowed[{index}]")
        pair = (item["provider"], item["model"])
        if not all(isinstance(value, str) and not _is_placeholder(value) for value in pair) or pair in non_eval_limits:
            raise ConfigError("non-evaluation provider/model pairs must be unique and non-empty")
        non_eval_limits[pair] = ProviderCallLimit(pair[0], pair[1], shared_input, shared_output, shared_cash)
    if mode == "demo":
        pairs = {(evaluation_limit.provider, evaluation_limit.model), *non_eval_limits.keys()}
        if any(provider != "deterministic_stub" for provider, _ in pairs):
            raise ConfigError("demo mode may use only deterministic_stub providers")

    dataset = _map(data, "dataset", {"train_ratio", "dev_ratio", "test_ratio", "event_selection", "knowledge_cutoff", "near_duplicate_threshold", "hide_repository_identifiers"})
    ratios = [_number(dataset[name], f"dataset.{name}", minimum=Decimal(0), maximum=Decimal(1), exclusive_minimum=True) for name in ("train_ratio", "dev_ratio", "test_ratio")]
    if sum(ratios) != Decimal("1"):
        raise ConfigError("dataset train/dev/test ratios must sum exactly to 1")
    if dataset["event_selection"] != "EARLIEST_QUALIFIED_MAINTAINER_REVIEW" or dataset["hide_repository_identifiers"] is not True:
        raise ConfigError("dataset event and identifier isolation settings are immutable")
    _number(dataset["near_duplicate_threshold"], "dataset.near_duplicate_threshold", minimum=Decimal(0), maximum=Decimal(1))
    if mode != "demo":
        cutoff = _datetime(dataset["knowledge_cutoff"], "dataset.knowledge_cutoff")
        if cutoff > instant:
            raise ConfigError("dataset.knowledge_cutoff cannot be in the future")
    elif dataset["knowledge_cutoff"] is not None:
        _datetime(dataset["knowledge_cutoff"], "dataset.knowledge_cutoff")

    metrics = _map(data, "metrics", {"decision_accuracy_floor", "reject_recall_floor", "balanced_accuracy_floor", "min_effect_pp", "ci_pass_subset_is_coprimary"})
    for key in ("decision_accuracy_floor", "reject_recall_floor", "balanced_accuracy_floor"):
        _number(metrics[key], f"metrics.{key}", minimum=Decimal(0), maximum=Decimal(1))
    _number(metrics["min_effect_pp"], "metrics.min_effect_pp", minimum=Decimal(0), maximum=Decimal(100), exclusive_minimum=True)
    if not isinstance(metrics["ci_pass_subset_is_coprimary"], bool):
        raise ConfigError("metrics.ci_pass_subset_is_coprimary must be boolean")

    statistics = _map(data, "statistics", {"alpha", "target_power", "bootstrap_resamples", "bootstrap_seed", "paired_test", "multiplicity", "insufficient_sample_terminal"})
    _number(statistics["alpha"], "statistics.alpha", minimum=Decimal(0), maximum=Decimal(1), exclusive_minimum=True)
    _number(statistics["target_power"], "statistics.target_power", minimum=Decimal(0), maximum=Decimal(1), exclusive_minimum=True)
    if _positive_int(statistics["bootstrap_resamples"], "statistics.bootstrap_resamples") < 1000 or isinstance(statistics["bootstrap_seed"], bool) or not isinstance(statistics["bootstrap_seed"], int):
        raise ConfigError("statistics bootstrap settings are invalid")
    if (statistics["paired_test"], statistics["multiplicity"], statistics["insufficient_sample_terminal"]) != ("EXACT_MCNEMAR", "HOLM_TWO_BASELINES", "INCONCLUSIVE"):
        raise ConfigError("statistics test, multiplicity, and insufficient-sample terminal are immutable")

    stability = _map(data, "stability", {"repeats", "subset_size", "low_pairwise_agreement_threshold", "diagnostic_only"})
    if _positive_int(stability["repeats"], "stability.repeats") < 2 or _positive_int(stability["subset_size"], "stability.subset_size") < 2:
        raise ConfigError("stability repeats/subset_size must be at least 2")
    _number(stability["low_pairwise_agreement_threshold"], "stability.low_pairwise_agreement_threshold", minimum=Decimal(0), maximum=Decimal(1))
    if stability["diagnostic_only"] is not True:
        raise ConfigError("stability must be diagnostic_only")

    sandbox = _map(data, "sandbox", {"untrusted_code_execution", "require_rootless", "network", "read_only_rootfs", "no_new_privileges", "launcher_attestation_key_sha256", "max_cpus", "max_memory_mb", "max_pids", "max_disk_mb", "max_output_mb", "max_wall_clock_seconds_per_job"})
    if sandbox["require_rootless"] is not True or sandbox["network"] != "none" or sandbox["read_only_rootfs"] is not True or sandbox["no_new_privileges"] is not True:
        raise ConfigError("sandbox must be rootless, networkless, no-new-privileges, and read-only")
    if sandbox["untrusted_code_execution"] not in {"disabled", "enabled_after_capability_gate"}:
        raise ConfigError("sandbox.untrusted_code_execution is invalid")
    if mode == "demo" and sandbox["untrusted_code_execution"] != "disabled":
        raise ConfigError("demo mode must disable untrusted code execution")
    if not isinstance(sandbox["launcher_attestation_key_sha256"], str) or not re.fullmatch(
        r"[a-f0-9]{64}", sandbox["launcher_attestation_key_sha256"]
    ):
        raise ConfigError("sandbox.launcher_attestation_key_sha256 must be a lowercase SHA-256")
    if mode == "final" and sandbox["launcher_attestation_key_sha256"] == "0" * 64:
        raise ConfigError("final mode requires a resolved launcher attestation key fingerprint")
    _number(sandbox["max_cpus"], "sandbox.max_cpus", minimum=Decimal(0), exclusive_minimum=True)
    for key in ("max_memory_mb", "max_pids", "max_disk_mb", "max_output_mb", "max_wall_clock_seconds_per_job"):
        _positive_int(sandbox[key], f"sandbox.{key}")

    final_test = _map(data, "final_test", {"run_once", "immutable_profile_required", "resumable_same_profile", "repair_after_valid_test"})
    if final_test != {"run_once": True, "immutable_profile_required": True, "resumable_same_profile": True, "repair_after_valid_test": False}:
        raise ConfigError("final_test barrier settings are immutable")

    return RunConfig(
        schema_version="3.0.0", run_id=data["run_id"], mode=mode,
        deadline_at=deadline, max_wall_clock_seconds=max_wall,
        max_total_tokens=integer_budgets["tokens"][0],
        max_incremental_cash_microusd=cash_values[0],
        max_provider_cash_microusd=provider_values[0],
        max_tool_calls=integer_budgets["tool_calls"][0],
        closeout_reserve_time_seconds=reserve_wall,
        closeout_reserve_tokens=integer_budgets["tokens"][2],
        closeout_reserve_cash_microusd=cash_values[2],
        closeout_reserve_provider_cash_microusd=provider_values[2],
        closeout_reserve_tool_calls=integer_budgets["tool_calls"][2],
        max_dev_iterations=max_dev, low_gain_patience=patience,
        min_dev_gain_pp=float(min_gain), max_cash_per_gain_pp_microusd=max_cash_gain,
        phase_deadlines=MappingProxyType(phase_deadlines),
        phase_caps=MappingProxyType(phase_caps),
        phase_stages=MappingProxyType(phase_stages),
        soft_watermark_tokens=integer_budgets["tokens"][1],
        soft_watermark_incremental_cash_microusd=cash_values[1],
        soft_watermark_provider_cash_microusd=provider_values[1],
        soft_watermark_tool_calls=integer_budgets["tool_calls"][1],
        soft_watermark_wall_seconds=soft_wall,
        max_parallel_tasks=worker_slots, lease_seconds=lease,
        max_status_staleness_seconds=staleness,
        evaluation_limit=evaluation_limit,
        non_evaluation_limits=MappingProxyType(non_eval_limits),
        approval_payload_hash=payload_hash,
        manifest_fingerprint=sha256(_canonical_bytes(data)).hexdigest(),
        raw=_deep_freeze(data),
    )


def load_run_config(source: str | Path | Mapping[str, Any], *, now: datetime | None = None) -> RunConfig:
    return load_config(source, now=now)
