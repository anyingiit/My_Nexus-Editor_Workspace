#!/usr/bin/env python3
"""Freeze the audited AGENTS.md + split for the single formal TEST run.

Writes dataset/AGENTS_freeze.json recording:
  - SHA-256 of every agents/ file and of the combined `ours` text exactly as the
    judge injects it (so the manifest of the test judge run can be cross-checked)
  - split sha256 + policy + boundaries + basis counts + unrecoverable exclusions

Run AFTER dev-only iteration has converged and BEFORE the one-shot test run.
"""
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from judge import (variant_text, AGENTS_DIR, judge_provider_name,
                    judge_model_name, judge_reasoning_name,
                    judge_fallback_enabled)  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))


def sha_file(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def main():
    agents_files = {}
    for name in sorted(os.listdir(AGENTS_DIR)):
        if name.endswith(".md"):
            agents_files[name] = sha_file(os.path.join(AGENTS_DIR, name))

    combined = hashlib.sha256(variant_text("ours").encode("utf-8")).hexdigest()

    # The reason the harness rendering must be frozen together with AGENTS:
    # the judge's effective test-standard is (AGENTS text) x (prompt renderer).
    # A renderer change (patch budget / checklist) alters every prompt, so the
    # freeze binds the rendering code (this judge.py) hash explicitly. Any such
    # change makes the old formal test run VOID (documented in PROGRESS.md).
    harness_sha = hashlib.sha256(
        open(os.path.join(HERE, "judge.py"), "rb").read()).hexdigest()

    split = json.load(open(os.path.join(ROOT, "dataset", "split.json")))
    split_blob = json.dumps(split, sort_keys=True).encode("utf-8")
    split_sha = hashlib.sha256(split_blob).hexdigest()

    import llm_client
    judge_provider = judge_provider_name()
    judge_model = judge_model_name(judge_provider)
    judge_reasoning = judge_reasoning_name()

    freeze = {
        "frozen_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "combined_ours_sha256": combined,
        "harness_sha256": harness_sha,
        "files": agents_files,
        "split_sha256": split_sha,
        "split_policy": split.get("split_policy"),
        "split_boundaries": split.get("dates"),
        "first_review_basis_counts": split.get("first_review_basis_counts"),
        "literal_first_review_counts": split.get("literal_first_review_counts"),
        "excluded_unrecoverable_first_review": split.get("excluded_unrecoverable_first_review"),
        "excluded_self_finalized": split.get("excluded_self_finalized"),
        "n_candidates": split.get("n_candidates"),
        "judge_model": judge_model,
        "judge_provider": judge_provider,
        "judge_reasoning": judge_reasoning,
        "fallback_enabled": judge_fallback_enabled(),
        "note": ("Frozen after dev-only iteration and dev criteria met. The formal "
                 "test run happens once post-freeze. Round 6 universe = "
                 "finalized PRs with an evidence-gated final actor UNION open PRs "
                 "with an admin decision formal review (GT = earliest admin "
                 "decision-review state); open PRs without an admin decision "
                 "review excluded; author-self-finalized WITHOUT maintainer "
                 "evidence excluded (1.1); unrecoverable first-review snapshots "
                 "are excluded (1.2). Judge model+provider+reasoning are bound "
                 "to this freeze."),
    }
    with open(os.path.join(ROOT, "dataset", "AGENTS_freeze.json"), "w") as fh:
        json.dump(freeze, fh, indent=2, ensure_ascii=False)
    print("freeze written:")
    print("  combined_ours_sha256:", combined)
    print("  harness_sha256:", harness_sha)
    print("  split_sha256:", split_sha)
    print("  files:", {k: v[:8] for k, v in agents_files.items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
