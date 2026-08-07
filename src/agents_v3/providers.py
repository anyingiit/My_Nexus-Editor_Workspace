"""Provider boundary with reproducible request profiles and bounded retries.

The core never embeds provider credentials and never silently changes a provider
during an evaluation run. Real network clients should implement ``Provider`` in
a separate, least-privilege process. The included mock is deterministic and is
used by the end-to-end smoke test.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import threading
import time
from typing import Any, Mapping, Protocol


class ProviderError(RuntimeError):
    """Base provider failure with a stable diagnostic category."""

    def __init__(self, category: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.category = category
        self.retryable = retryable


@dataclass(frozen=True)
class EvaluationProfile:
    provider: str
    model: str
    prompt_version: str
    schema_version: str
    temperature: float = 0.0
    top_p: float = 1.0
    seed: int | None = 0
    max_output_tokens: int = 1024

    def fingerprint(self) -> str:
        raw = json.dumps(self.__dict__, sort_keys=True, separators=(",", ":"))
        return sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProviderRequest:
    request_id: str
    task_id: str
    profile: EvaluationProfile
    prompt: str
    metadata: Mapping[str, Any]

    def cache_key(self) -> str:
        payload = {
            "profile": self.profile.fingerprint(),
            "prompt": self.prompt,
            "metadata": dict(self.metadata),
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProviderResponse:
    request_id: str
    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    provider_request_id: str | None = None


class Provider(Protocol):
    name: str

    def complete(self, request: ProviderRequest) -> ProviderResponse: ...


class ProviderGate:
    """Process-local rate gate and circuit breaker.

    A production adapter may replace this with a distributed queue. The gate is
    deliberately provider-specific so one 429 pauses all workers sharing that
    provider instead of causing independent retry storms.
    """

    def __init__(
        self,
        *,
        requests_per_second: float,
        failure_threshold: int = 3,
        cooldown_seconds: float = 30.0,
    ) -> None:
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        self._interval = 1.0 / requests_per_second
        self._failure_threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._last_started = 0.0
        self._failures = 0
        self._open_until = 0.0
        self._lock = threading.Lock()

    def before_request(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._open_until:
                raise ProviderError(
                    "circuit_open",
                    f"provider circuit open for {self._open_until - now:.2f}s",
                    retryable=True,
                )
            wait = self._interval - (now - self._last_started)
            if wait > 0:
                time.sleep(wait)
            self._last_started = time.monotonic()

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._open_until = 0.0

    def record_failure(self, *, retryable: bool) -> None:
        if not retryable:
            return
        with self._lock:
            self._failures += 1
            if self._failures >= self._failure_threshold:
                self._open_until = time.monotonic() + self._cooldown


class GatedProvider:
    def __init__(self, provider: Provider, gate: ProviderGate) -> None:
        self.provider = provider
        self.gate = gate
        self.name = provider.name

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.gate.before_request()
        try:
            response = self.provider.complete(request)
        except ProviderError as exc:
            self.gate.record_failure(retryable=exc.retryable)
            raise
        except Exception as exc:  # normalize unknown client failures
            self.gate.record_failure(retryable=False)
            raise ProviderError("client_bug", str(exc), retryable=False) from exc
        self.gate.record_success()
        return response


class DeterministicMockProvider:
    """No-network provider for smoke tests and harness development."""

    name = "mock"

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        start = time.monotonic()
        digest = sha256(request.prompt.encode("utf-8")).hexdigest()[:12]
        text = json.dumps(
            {"decision": "APPROVE", "reason_codes": [], "digest": digest},
            sort_keys=True,
        )
        latency = max(0, int((time.monotonic() - start) * 1000))
        return ProviderResponse(
            request_id=request.request_id,
            text=text,
            input_tokens=max(1, len(request.prompt) // 4),
            output_tokens=max(1, len(text) // 4),
            latency_ms=latency,
            provider_request_id=f"mock-{digest}",
        )


class DisabledNetworkProvider:
    """Fail-closed default used until an explicit adapter is configured."""

    name = "disabled"

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        raise ProviderError(
            "provider_disabled",
            "No network provider is configured; use the mock or an external adapter",
            retryable=False,
        )
