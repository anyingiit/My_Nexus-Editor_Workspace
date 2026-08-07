#!/usr/bin/env python3
"""Outer wall-clock and output-byte watchdog for the container launcher."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def directory_bytes(path: Path) -> int:
    total = 0
    for root, directories, files in os.walk(path, followlinks=False):
        directories[:] = [
            name for name in directories if not (Path(root) / name).is_symlink()
        ]
        for name in files:
            candidate = Path(root) / name
            try:
                if candidate.is_symlink():
                    total += candidate.lstat().st_size
                else:
                    total += candidate.stat().st_size
            except FileNotFoundError:
                continue
    return total


def terminate_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wall-seconds", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-output-bytes", type=int, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if args.wall_seconds <= 0 or args.max_output_bytes <= 0 or not args.command:
        parser.error("positive limits and a command are required")
    output = args.output_dir.resolve(strict=True)
    if not output.is_dir():
        parser.error("output-dir must be an existing directory")

    started = time.monotonic()
    process = subprocess.Popen(args.command, start_new_session=True)
    try:
        while process.poll() is None:
            if time.monotonic() - started > args.wall_seconds:
                terminate_group(process)
                print("runner wall-clock limit exceeded", file=sys.stderr)
                return 124
            if directory_bytes(output) > args.max_output_bytes:
                terminate_group(process)
                print("runner output-byte limit exceeded", file=sys.stderr)
                return 125
            time.sleep(0.1)
        if directory_bytes(output) > args.max_output_bytes:
            print("runner output-byte limit exceeded", file=sys.stderr)
            return 125
        return int(process.returncode or 0)
    except KeyboardInterrupt:
        terminate_group(process)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
