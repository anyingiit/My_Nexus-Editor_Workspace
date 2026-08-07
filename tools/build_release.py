#!/usr/bin/env python3
"""Build a deterministic source bundle and wheel, then emit checksums.

The build happens from a clean temporary copy so setuptools never writes
generated metadata into the canonical source tree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import zipfile


DEFAULT_SOURCE_DATE_EPOCH = 1_786_060_800  # 2026-08-07T00:00:00Z
EXCLUDED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "work",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


class ReleaseError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_files(root: Path) -> list[Path]:
    selected: list[Path] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in EXCLUDED_PARTS or part.endswith(".egg-info") for part in relative.parts):
            continue
        if path.is_symlink():
            raise ReleaseError(f"release input may not contain symlinks: {relative}")
        if path.is_file() and path.suffix not in EXCLUDED_SUFFIXES and path.name != ".DS_Store":
            selected.append(relative)
    return sorted(selected, key=lambda item: item.as_posix())


def write_source_zip(root: Path, destination: Path, version: str, epoch: int) -> None:
    prefix = f"agents-v3-project-{version}"
    timestamp = time.gmtime(max(epoch, 315_532_800))[:6]
    with zipfile.ZipFile(
        destination,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        strict_timestamps=True,
    ) as archive:
        for relative in source_files(root):
            source = root / relative
            info = zipfile.ZipInfo(f"{prefix}/{relative.as_posix()}", date_time=timestamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            mode = stat.S_IMODE(source.stat().st_mode)
            info.external_attr = mode << 16
            archive.writestr(info, source.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def copy_clean_source(root: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    for relative in source_files(root):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / relative, target)


def build_wheel(root: Path, destination: Path, epoch: int) -> Path:
    with tempfile.TemporaryDirectory(prefix="agents-v3-wheel-") as temporary:
        temporary_root = Path(temporary)
        clean_source = temporary_root / "source"
        wheel_dir = temporary_root / "wheel"
        copy_clean_source(root, clean_source)
        wheel_dir.mkdir()
        environment = dict(os.environ)
        environment["SOURCE_DATE_EPOCH"] = str(epoch)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--no-build-isolation",
                "--disable-pip-version-check",
                "--wheel-dir",
                str(wheel_dir),
                str(clean_source),
            ],
            check=True,
            env=environment,
        )
        wheels = sorted(wheel_dir.glob("*.whl"))
        if len(wheels) != 1:
            raise ReleaseError(f"expected one wheel, found {len(wheels)}")
        target = destination / wheels[0].name
        shutil.copy2(wheels[0], target)
        return target


def verify_wheel(wheel_path: Path) -> None:
    required_suffixes = {
        "share/agents-v3/AGENTS.md",
        "share/agents-v3/config/run_config.example.yaml",
        "share/agents-v3/schemas/run_config.schema.json",
        "share/agents-v3/protocol/evaluation.md",
        "share/agents-v3/sandbox/run-untrusted.sh",
    }
    with zipfile.ZipFile(wheel_path) as archive:
        names = set(archive.namelist())
        for suffix in required_suffixes:
            if not any(name.endswith(suffix) for name in names):
                raise ReleaseError(f"wheel is missing required resource: {suffix}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-date-epoch", type=int, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    try:
        output.relative_to(root)
    except ValueError:
        pass
    else:
        raise ReleaseError("release output must be outside the canonical source tree")
    epoch = args.source_date_epoch
    if epoch is None:
        epoch = int(os.environ.get("SOURCE_DATE_EPOCH", DEFAULT_SOURCE_DATE_EPOCH))
    if epoch < 315_532_800:
        raise ReleaseError("SOURCE_DATE_EPOCH must be at or after 1980-01-01")
    if output.exists() and any(output.iterdir()):
        raise ReleaseError(f"output directory must not exist or be empty: {output}")
    output.mkdir(parents=True, exist_ok=True)

    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    version = str(project["project"]["version"])
    source_zip = output / f"agents-v3-project-{version}.zip"
    write_source_zip(root, source_zip, version, epoch)
    wheel = build_wheel(root, output, epoch)
    verify_wheel(wheel)

    artifacts = {
        source_zip.name: sha256_file(source_zip),
        wheel.name: sha256_file(wheel),
    }
    manifest_path = output / "release-manifest.json"
    manifest = {
        "schema_version": "3.0",
        "project": "agents-v3",
        "version": version,
        "source_date_epoch": epoch,
        "artifacts": artifacts,
        "build": {
            "backend": "setuptools.build_meta",
            "build_requirements_lock": "build-requirements.lock",
            "wheel_no_build_isolation": True,
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    checksums = {**artifacts, manifest_path.name: sha256_file(manifest_path)}
    (output / "SHA256SUMS").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in sorted(checksums.items())),
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ReleaseError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"release build failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
