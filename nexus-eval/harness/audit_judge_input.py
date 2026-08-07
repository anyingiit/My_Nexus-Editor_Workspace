#!/usr/bin/env python3
"""Deterministically audit judge inputs using train/dev only.

This audit deliberately never opens ``dataset/ground_truth`` or any test PR.
It verifies that first-review patches, replayed checks, and the complete
layered ours text reach the same prompt builder used by ``judge.py``. It also
checks that the common review method asks the judge to use the supplied rules
without changing labels or introducing a default decision.
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

from judge import (JUDGE_SYSTEM, build_user_prompt, render_checks, render_files,
                   variant_text, PER_FILE_PATCH_FLOOR, PER_FILE_PATCH_MAX,
                   TOTAL_PATCH_CHARS, MAX_AGENTS_CHARS)


FORBIDDEN_SNAPSHOT_KEYS = {
    "golden_decision", "gt_basis", "admin_review", "reviews",
    "review_comments", "issue_comments",
}


def _load_split():
    with open(os.path.join(ROOT, "dataset", "split.json")) as fh:
        data = json.load(fh)
    return [p for p in data["prs"] if p["split"] in {"train", "dev"}]


def _assert(condition, message, failures):
    if not condition:
        failures.append(message)


def main():
    prs = sorted(_load_split(), key=lambda p: p["number"])
    ours = variant_text("ours")
    failures = []
    patch_count = 0
    patch_omitted_count = 0
    check_count = 0
    prompt_lengths = []
    split_counts = {"train": 0, "dev": 0}

    _assert(len(ours) <= MAX_AGENTS_CHARS,
            f"ours injection exceeds cap: {len(ours)}", failures)
    sample = {
        "pr_number": 0,
        "title": "",
        "body": "",
        "commits_at_first_review": [],
        "compare_first_review": {"files": []},
    }
    _assert("Required review method" in build_user_prompt(sample, ours),
            "review method is absent from prompt", failures)
    _assert("Patch review checklist" in build_user_prompt(sample, ours),
            "patch review checklist is absent from prompt", failures)
    for marker in ("first-review failure", "regexp + wholeWord", "build gate"):
        _assert(marker in ours or marker in JUDGE_SYSTEM,
                f"audit marker absent: {marker}", failures)

    for pr in prs:
        n = pr["number"]
        split_counts[pr["split"]] += 1
        path = os.path.join(ROOT, "dataset", "snapshots", str(n), "first.json")
        with open(path) as fh:
            snap = json.load(fh)
        keys = set(snap)
        _assert(not (keys & FORBIDDEN_SNAPSHOT_KEYS),
                f"PR {n}: ground-truth key in first snapshot: " +
                repr(sorted(keys & FORBIDDEN_SNAPSHOT_KEYS)), failures)

        compare = snap.get("compare_first_review") or {}
        files = compare.get("files") or []
        has_patch = any(bool(f.get("patch")) for f in files)
        if has_patch:
            patch_count += 1
            rendered = render_files(snap)
            for f in files:
                patch = f.get("patch") or ""
                if patch:
                    # every file must be visible: its name/stats header always,
                    # and its patch head either present or explicitly omitted.
                    omitted = (f"- {f.get('filename')}: "
                               "[patch omitted, budget exhausted]")
                    if patch[:80] not in rendered:
                        _assert(omitted in rendered,
                                f"PR {n}: patch head of {f.get('filename')} is "
                                "neither rendered nor marked omitted", failures)
                    if omitted in rendered and patch[:80] not in rendered:
                        patch_omitted_count += 1
        checks = snap.get("status_first") or snap.get("check_runs_first")
        if checks:
            check_count += 1
            rendered_checks = render_checks(snap)
            _assert(rendered_checks != "(no check results recorded for this state)",
                    f"PR {n}: checks were dropped by renderer", failures)

        prompt = build_user_prompt(snap, ours)
        start = prompt.index("--- START AGENTS.md ---") + len(
            "--- START AGENTS.md ---\n")
        end = prompt.index("\n--- END AGENTS.md ---")
        injected = prompt[start:end]
        _assert(injected == ours[:MAX_AGENTS_CHARS],
                f"PR {n}: ours injection is not complete/exact", failures)
        _assert("@@" in prompt if has_patch else True,
                f"PR {n}: prompt lacks a diff hunk", failures)
        _assert("golden_decision" not in prompt and "admin_review" not in prompt,
                f"PR {n}: forbidden GT token leaked into prompt", failures)
        prompt_lengths.append(len(prompt))

        # All variants share the same first-review evidence and review method;
        # only their contribution-rule block may differ.
        empty = build_user_prompt(snap, "")
        baseline = build_user_prompt(snap, variant_text("baseline"))
        for other_name, other in (("empty", empty), ("baseline", baseline)):
            _assert(prompt.split("--- START AGENTS.md ---", 1)[0] ==
                    other.split("--- START AGENTS.md ---", 1)[0],
                    f"PR {n}: {other_name} evidence prefix differs", failures)
            _assert(prompt.split("--- END AGENTS.md ---", 1)[1] ==
                    other.split("--- END AGENTS.md ---", 1)[1],
                    f"PR {n}: {other_name} review suffix differs", failures)

    report = {
        "source_splits": ["train", "dev"],
        "split_counts": split_counts,
        "n_pr": len(prs),
        "patch_pr": patch_count,
        "patch_files_omitted_by_prompt_cap": patch_omitted_count,
        "checks_pr": check_count,
        "ours_chars": len(ours),
        "ours_sha256": hashlib.sha256(ours.encode()).hexdigest(),
        "prompt_budget": {
            "PER_FILE_PATCH_FLOOR": PER_FILE_PATCH_FLOOR,
            "PER_FILE_PATCH_MAX": PER_FILE_PATCH_MAX,
            "TOTAL_PATCH_CHARS": TOTAL_PATCH_CHARS,
            "MAX_AGENTS_CHARS": MAX_AGENTS_CHARS,
        },
        "prompt_chars_min": min(prompt_lengths) if prompt_lengths else 0,
        "prompt_chars_max": max(prompt_lengths) if prompt_lengths else 0,
        "judge_system_sha256": hashlib.sha256(JUDGE_SYSTEM.encode()).hexdigest(),
        "passed": not failures,
        "failures": failures,
    }
    out = os.path.join(ROOT, "reports", "judge_input_audit_round6r7.json")
    with open(out, "w") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
