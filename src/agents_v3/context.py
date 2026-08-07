"""Deterministic candidate/annex context packing.

Evaluation never lets a model decide which annex to load. The ordered manifest,
size limit and overflow policy are frozen in the evaluation profile.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path


@dataclass(frozen=True)
class ContextManifest:
    core_path: str
    annex_paths: tuple[str, ...] = ()
    max_utf8_bytes: int = 200_000
    overflow_policy: str = "reject"

    def validate(self) -> None:
        if not self.core_path:
            raise ValueError("core_path is required")
        if self.max_utf8_bytes <= 0:
            raise ValueError("max_utf8_bytes must be positive")
        if self.overflow_policy != "reject":
            raise ValueError("only fail-closed overflow_policy=reject is supported")
        paths = (self.core_path, *self.annex_paths)
        if len(set(paths)) != len(paths):
            raise ValueError("context paths must be unique")

    def fingerprint(self) -> str:
        self.validate()
        payload = {
            "core_path": self.core_path,
            "annex_paths": self.annex_paths,
            "max_utf8_bytes": self.max_utf8_bytes,
            "overflow_policy": self.overflow_policy,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return sha256(encoded.encode("utf-8")).hexdigest()


def _resolve_under(root: Path, relative: str) -> Path:
    if Path(relative).is_absolute():
        raise ValueError("context paths must be relative")
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"context path escapes root: {relative}") from exc
    return resolved


def pack_context(root: str | Path, manifest: ContextManifest) -> tuple[str, dict[str, str]]:
    manifest.validate()
    base = Path(root).resolve()
    if not base.is_dir():
        raise FileNotFoundError(base)
    ordered = (manifest.core_path, *manifest.annex_paths)
    chunks: list[str] = []
    checksums: dict[str, str] = {}
    total = 0
    for relative in ordered:
        path = _resolve_under(base, relative)
        data = path.read_bytes()
        total += len(data)
        if total > manifest.max_utf8_bytes:
            raise ValueError(
                f"context exceeds frozen limit {manifest.max_utf8_bytes} bytes"
            )
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"context file is not UTF-8: {relative}") from exc
        checksums[relative] = sha256(data).hexdigest()
        chunks.append(
            f'<CONTEXT_FILE path={json.dumps(relative)}>\n{text}\n</CONTEXT_FILE>'
        )
    return "\n\n".join(chunks), checksums
