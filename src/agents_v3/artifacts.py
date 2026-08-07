"""Content-addressed artifacts with provenance and fail-closed promotion gates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping

from .profiles import validate_contract


REQUIRED_DETERMINISTIC_GATES = frozenset(
    {
        "SCHEMA",
        "PATH_ALLOWLIST",
        "REFERENCE_EXISTENCE",
        "COUNT",
        "CHECKSUM",
        "LEAKAGE_SCAN",
        "KNOWLEDGE_CUTOFF",
        "PROVENANCE",
    }
)


def file_sha256(path: str | Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ArtifactRecord:
    manifest: Mapping[str, Any]

    def to_manifest(self) -> dict[str, Any]:
        return json.loads(json.dumps(dict(self.manifest), sort_keys=True))

    @property
    def artifact_id(self) -> str:
        return str(self.manifest["artifact_id"])

    @property
    def path(self) -> str:
        return str(self.manifest["staging_path"])

    @property
    def checksum(self) -> str:
        return str(self.manifest["sha256"])

    @property
    def producer_task(self) -> str:
        return str(self.manifest["producer_task_id"])

    @property
    def dependencies(self) -> tuple[str, ...]:
        return tuple(item["artifact_id"] for item in self.manifest["source_artifacts"])

    @property
    def state(self) -> str:
        status = self.manifest["promotion_status"]
        if status == "PROMOTED":
            return "CANONICAL"
        if status == "REJECTED":
            return "INVALIDATED"
        checks = {item["name"]: item["status"] for item in self.manifest["deterministic_checks"]}
        semantic = self.manifest["semantic_gate"]
        if REQUIRED_DETERMINISTIC_GATES <= checks.keys() and all(
            checks[name] == "PASSED" for name in REQUIRED_DETERMINISTIC_GATES
        ) and semantic in {"NOT_REQUIRED", "PASSED"}:
            return "VALIDATED"
        return "STAGING"


class ArtifactRegistry:
    """Atomic single-writer registry; every row validates against the schema."""

    def __init__(
        self,
        manifest_path: str | Path,
        project_root: str | Path,
        *,
        run_id: str = "local-run",
    ) -> None:
        if not run_id:
            raise ValueError("run_id is required")
        self.manifest_path = Path(manifest_path).resolve()
        self.project_root = Path(project_root).resolve()
        self.run_id = run_id
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.manifest_path.exists():
            self._write({"schema_version": "3.0.0", "run_id": run_id, "artifacts": {}})
        else:
            data = self._load()
            if data["run_id"] != run_id:
                raise ValueError("artifact registry is bound to a different run_id")

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid artifact registry: {exc}") from exc
        if (
            not isinstance(data, dict)
            or data.get("schema_version") != "3.0.0"
            or not isinstance(data.get("run_id"), str)
            or not isinstance(data.get("artifacts"), dict)
        ):
            raise ValueError("invalid artifact registry envelope")
        for artifact_id, manifest in data["artifacts"].items():
            canonical = validate_contract(manifest, "artifact_manifest.schema.json")
            if canonical["artifact_id"] != artifact_id:
                raise ValueError("artifact registry key does not match artifact_id")
        return data

    def _write(self, data: Mapping[str, Any]) -> None:
        encoded = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self.manifest_path.name}.", dir=self.manifest_path.parent, text=True
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.manifest_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _inside_root(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.project_root)
            return True
        except ValueError:
            return False

    def register(
        self,
        *,
        artifact_id: str,
        path: str | Path,
        producer_task_id: str | None = None,
        producer_task: str | None = None,
        artifact_type: str = "generic",
        source_artifacts: Iterable[str | Mapping[str, str]] = (),
        provenance: Mapping[str, Any],
        knowledge_cutoff: str | None = None,
        contains_protected_truth: bool = False,
        semantic_required: bool = False,
    ) -> ArtifactRecord:
        task_id = producer_task_id or producer_task
        if not task_id:
            raise ValueError("producer_task_id is required")
        resolved = Path(path).resolve()
        if not self._inside_root(resolved):
            raise ValueError("artifact path is outside project_root")
        if not resolved.is_file():
            raise FileNotFoundError(resolved)
        data = self._load()
        if artifact_id in data["artifacts"]:
            raise ValueError(f"artifact already exists: {artifact_id}")

        references: list[dict[str, str]] = []
        for item in source_artifacts:
            if isinstance(item, str):
                dependency = data["artifacts"].get(item)
                if dependency is None:
                    raise ValueError(f"source artifact is not registered: {item}")
                references.append({"artifact_id": item, "sha256": dependency["sha256"]})
            elif isinstance(item, Mapping):
                references.append(
                    {"artifact_id": str(item.get("artifact_id", "")), "sha256": str(item.get("sha256", ""))}
                )
            else:
                raise TypeError("source_artifacts entries must be IDs or reference objects")

        manifest = {
            "schema_version": "3.0.0",
            "run_id": self.run_id,
            "artifact_id": artifact_id,
            "artifact_type": artifact_type,
            "producer_task_id": task_id,
            "created_at": _now(),
            "staging_path": str(resolved.relative_to(self.project_root)),
            "canonical_path": None,
            "sha256": file_sha256(resolved),
            "size_bytes": resolved.stat().st_size,
            "source_artifacts": sorted(references, key=lambda item: item["artifact_id"]),
            "provenance": dict(provenance),
            "knowledge_cutoff": knowledge_cutoff,
            "contains_protected_truth": contains_protected_truth,
            "deterministic_checks": [],
            "semantic_gate": "PENDING" if semantic_required else "NOT_REQUIRED",
            "promotion_status": "STAGED",
        }
        canonical = validate_contract(manifest, "artifact_manifest.schema.json")
        data["artifacts"][artifact_id] = canonical
        self._write(data)
        return ArtifactRecord(canonical)

    def get(self, artifact_id: str) -> ArtifactRecord:
        manifest = self._load()["artifacts"].get(artifact_id)
        if manifest is None:
            raise KeyError(artifact_id)
        return ArtifactRecord(validate_contract(manifest, "artifact_manifest.schema.json"))

    def record_check(
        self,
        artifact_id: str,
        name: str,
        status: str,
        *,
        details_sha256: str,
    ) -> ArtifactRecord:
        if name not in REQUIRED_DETERMINISTIC_GATES:
            raise ValueError(f"unknown deterministic gate: {name}")
        if status not in {"PASSED", "FAILED"}:
            raise ValueError("gate status must be PASSED or FAILED")
        data = self._load()
        manifest = data["artifacts"].get(artifact_id)
        if manifest is None:
            raise KeyError(artifact_id)
        if manifest["promotion_status"] != "STAGED":
            raise ValueError("checks are immutable after promotion/rejection")
        checks = {item["name"]: item for item in manifest["deterministic_checks"]}
        replacement = {"name": name, "status": status, "details_sha256": details_sha256}
        existing = checks.get(name)
        if existing is not None and existing != replacement:
            raise ValueError(f"gate {name} already has a different result")
        checks[name] = replacement
        manifest["deterministic_checks"] = [checks[key] for key in sorted(checks)]
        if status == "FAILED":
            manifest["promotion_status"] = "REJECTED"
        canonical = validate_contract(manifest, "artifact_manifest.schema.json")
        data["artifacts"][artifact_id] = canonical
        self._write(data)
        return ArtifactRecord(canonical)

    def set_semantic_gate(self, artifact_id: str, status: str) -> ArtifactRecord:
        if status not in {"PASSED", "FAILED"}:
            raise ValueError("semantic status must be PASSED or FAILED")
        data = self._load()
        manifest = data["artifacts"].get(artifact_id)
        if manifest is None:
            raise KeyError(artifact_id)
        if manifest["semantic_gate"] == "NOT_REQUIRED":
            raise ValueError("semantic gate was not required for this artifact")
        if manifest["semantic_gate"] not in {"PENDING", status}:
            raise ValueError("semantic gate is immutable once resolved")
        manifest["semantic_gate"] = status
        if status == "FAILED":
            manifest["promotion_status"] = "REJECTED"
        canonical = validate_contract(manifest, "artifact_manifest.schema.json")
        data["artifacts"][artifact_id] = canonical
        self._write(data)
        return ArtifactRecord(canonical)

    def validate_for_promotion(
        self, artifact_id: str, *, allow_protected_truth: bool = False
    ) -> ArtifactRecord:
        record = self.get(artifact_id)
        manifest = record.to_manifest()
        if manifest["promotion_status"] != "STAGED":
            raise ValueError("only STAGED artifacts may be promoted")
        source = self.project_root / manifest["staging_path"]
        if not source.is_file() or file_sha256(source) != manifest["sha256"]:
            raise ValueError("artifact content changed since registration")
        if source.stat().st_size != manifest["size_bytes"]:
            raise ValueError("artifact size changed since registration")
        checks = {item["name"]: item["status"] for item in manifest["deterministic_checks"]}
        missing = sorted(REQUIRED_DETERMINISTIC_GATES - checks.keys())
        failed = sorted(name for name, status in checks.items() if status != "PASSED")
        if missing or failed:
            raise ValueError(f"artifact gates are not promotable; missing={missing}, failed={failed}")
        if manifest["semantic_gate"] not in {"NOT_REQUIRED", "PASSED"}:
            raise ValueError("semantic gate has not passed")
        if manifest["contains_protected_truth"] and not allow_protected_truth:
            raise ValueError("protected-truth artifact cannot enter the ordinary canonical area")
        registry = self._load()["artifacts"]
        for reference in manifest["source_artifacts"]:
            source_manifest = registry.get(reference["artifact_id"])
            if source_manifest is None or source_manifest["sha256"] != reference["sha256"]:
                raise ValueError("source artifact provenance no longer resolves")
        return record

    def transition(self, artifact_id: str, new_state: str, *, evaluation: str | None = None) -> None:
        """Compatibility shim; readiness is derived from gates, never asserted."""

        del evaluation
        if new_state == "VALIDATED":
            self.validate_for_promotion(artifact_id)
            return
        if new_state == "INVALIDATED":
            self.reject(artifact_id)
            return
        raise ValueError("CANONICAL is reachable only through promote_file")

    def reject(self, artifact_id: str) -> ArtifactRecord:
        data = self._load()
        manifest = data["artifacts"].get(artifact_id)
        if manifest is None:
            raise KeyError(artifact_id)
        if manifest["promotion_status"] == "PROMOTED":
            raise ValueError("promoted artifact cannot be silently reclassified")
        manifest["promotion_status"] = "REJECTED"
        canonical = validate_contract(manifest, "artifact_manifest.schema.json")
        data["artifacts"][artifact_id] = canonical
        self._write(data)
        return ArtifactRecord(canonical)

    def promote_file(
        self,
        artifact_id: str,
        destination: str | Path,
        *,
        allow_protected_truth: bool = False,
    ) -> Path:
        record = self.validate_for_promotion(
            artifact_id, allow_protected_truth=allow_protected_truth
        )
        source = self.project_root / record.path
        target = Path(destination).resolve()
        if not self._inside_root(target):
            raise ValueError("destination is outside project_root")
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        os.close(descriptor)
        try:
            shutil.copyfile(source, temporary)
            if file_sha256(temporary) != record.checksum:
                raise ValueError("copy checksum mismatch")
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

        data = self._load()
        manifest = data["artifacts"][artifact_id]
        manifest["canonical_path"] = str(target.relative_to(self.project_root))
        manifest["promotion_status"] = "PROMOTED"
        canonical = validate_contract(manifest, "artifact_manifest.schema.json")
        data["artifacts"][artifact_id] = canonical
        self._write(data)
        return target

