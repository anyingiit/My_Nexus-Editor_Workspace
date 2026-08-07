"""Durable, bounded human decision requests.

The orchestrator may continue unrelated DAG branches while a decision is open.
Every request has a declared default action; absence of a response can never
silently mean permission to spend more, weaken safety, or alter the protocol.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping


@dataclass(frozen=True)
class DecisionOption:
    option_id: str
    description: str
    estimated_cost_usd: float = 0.0
    estimated_duration_seconds: int = 0
    effects: tuple[str, ...] = ()

    def validate(self) -> None:
        if not self.option_id or not self.description:
            raise ValueError("decision option requires id and description")
        if self.estimated_cost_usd < 0 or self.estimated_duration_seconds < 0:
            raise ValueError("decision estimates must be nonnegative")


@dataclass(frozen=True)
class DecisionRequest:
    request_id: str
    question: str
    evidence: tuple[str, ...]
    options: tuple[DecisionOption, ...]
    default_option_id: str
    expires_at: str
    created_at: str
    category: str

    def validate(self) -> None:
        if not self.request_id or not self.question:
            raise ValueError("decision request requires id and question")
        if self.category not in {
            "budget_change",
            "protocol_change",
            "model_escalation",
            "security_exception",
            "scope_change",
        }:
            raise ValueError("invalid decision category")
        if len(self.options) < 2:
            raise ValueError("at least two decision options are required")
        for option in self.options:
            option.validate()
        ids = [option.option_id for option in self.options]
        if len(ids) != len(set(ids)) or self.default_option_id not in ids:
            raise ValueError("option ids must be unique and include the default")
        expires = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        if expires.tzinfo is None:
            raise ValueError("expires_at must include a timezone")


class DecisionStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({"schema_version": 1, "requests": {}, "resolutions": {}})

    def _load(self) -> dict[str, Any]:
        with self.path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if data.get("schema_version") != 1:
            raise ValueError("unsupported decision-store schema")
        return data

    def _write(self, data: Mapping[str, Any]) -> None:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.", dir=self.path.parent, text=True
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def create(self, request: DecisionRequest) -> None:
        request.validate()
        data = self._load()
        if request.request_id in data["requests"]:
            raise ValueError("duplicate decision request")
        row = asdict(request)
        data["requests"][request.request_id] = row
        self._write(data)

    def resolve(self, request_id: str, option_id: str, *, actor: str = "human") -> None:
        data = self._load()
        request = data["requests"].get(request_id)
        if request is None:
            raise KeyError(request_id)
        if request_id in data["resolutions"]:
            raise ValueError("decision already resolved")
        valid = {option["option_id"] for option in request["options"]}
        if option_id not in valid:
            raise ValueError("unknown decision option")
        data["resolutions"][request_id] = {
            "option_id": option_id,
            "actor": actor,
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        }
        self._write(data)

    def effective_option(self, request_id: str, *, now: datetime | None = None) -> str | None:
        data = self._load()
        request = data["requests"].get(request_id)
        if request is None:
            raise KeyError(request_id)
        resolution = data["resolutions"].get(request_id)
        if resolution:
            return str(resolution["option_id"])
        current = now or datetime.now(timezone.utc)
        expires = datetime.fromisoformat(request["expires_at"].replace("Z", "+00:00"))
        if current >= expires:
            return str(request["default_option_id"])
        return None
