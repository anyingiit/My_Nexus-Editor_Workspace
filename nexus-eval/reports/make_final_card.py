#!/usr/bin/env python3
"""Assemble FINAL report_card.json from the scored runs + consistency + cost +
bootstrap + provenance (AGENTS freeze / split hash / judge model / run info).

The dev numbers come from the audited time split + repaired first-review
snapshots + dev-only AGENTS iteration. The test numbers are from the SINGLE
formal post-freeze run. Both are read from tag-bound score files so stale
rounds can never be confused with the official numbers.

Usage: make_final_card.py [--tag final] [--split-policy "<policy>"]
"""
import argparse
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
REPORTS = os.path.join(HERE)
HARNESS = os.path.join(ROOT, "harness")
VARIANTS = ["empty", "baseline", "ours"]


def load(name):
    with open(os.path.join(REPORTS, name)) as fh:
        return json.load(fh)


def load_or(path, default):
    if os.path.exists(path):
        return json.load(open(path))
    return default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="final")
    ap.add_argument("--dev-tag", default=None, help="tag used for the dev-score files "
                    "(defaults to --tag)")
    ap.add_argument("--consistency-tag", default=None,
                    help="tag used for the consistency result (defaults to --dev-tag)")
    args = ap.parse_args()
    tag = args.tag
    dev_tag = args.dev_tag or tag
    consistency_tag = args.consistency_tag or dev_tag

    results = {}
    for split in ("dev", "test"):
        results[split] = {}
        for v in VARIANTS:
            t = tag if split == "test" else dev_tag
            results[split][v] = load(f"score_{v}_{split}_{t}.json")

    consistency = load_or(os.path.join(HARNESS, "state", f"consistency_result_{consistency_tag}.json"),
                          {"C": None, "n_pr": 0, "n_runs": 0, "variant": "ours",
                           "split": "dev", "tag": consistency_tag, "decisions": {}})
    C = consistency.get("C")

    test = results["test"]
    dev = results["dev"]
    acc = {v: test[v]["decision_accuracy"] for v in VARIANTS}
    dacc = {v: dev[v]["decision_accuracy"] for v in VARIANTS}

    # THE TEST bootstrap is the formal comparison; it is read from the
    # split/tag-bound file written by bootstrap.py (never the generic name,
    # which a later dev bootstrap could clobber).
    boot = load(f"bootstrap_{'test'}_{tag}.json")
    freeze = load_or(os.path.join(ROOT, "dataset", "AGENTS_freeze.json"), {})
    split_meta = load_or(os.path.join(ROOT, "dataset", "split.json"), {}) or {}

    report = {
        "provenance": {
            "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "round": "round12-data-audit-v7",
            "tag": tag,
            "judge_model": freeze.get("judge_model"),
            "judge_provider": freeze.get("judge_provider"),
            "judge_reasoning": freeze.get("judge_reasoning", "default"),
            "judge_fallback_enabled": freeze.get("fallback_enabled"),
            "agents_combined_sha256": freeze.get("combined_ours_sha256"),
            "agents_files": freeze.get("files"),
            "split_sha256": freeze.get("split_sha256"),
            "split_policy": freeze.get("split_policy"),
            "excluded_unrecoverable_first_review": freeze.get("excluded_unrecoverable_first_review"),
            "excluded_self_finalized": freeze.get("excluded_self_finalized"),
            "open_included": split_meta.get("open_included"),
            "golden_distribution": split_meta.get("golden_distribution"),
            "test_run_note": ("test judged ONCE after AGENTS freeze. Round 12 / v7 "
                              "data-audit universe = finalized PRs with an "
                              "evidence-gated final actor UNION open PRs with an "
                              "admin DECISION formal review whose REVIEW ACTOR's own "
                              "author_association is OWNER/MEMBER/COLLABORATOR OR "
                              "carries direct-push weak evidence (any other "
                              "commenter's association never substitutes for the "
                              "review actor; GT = earliest admin decision-review "
                              "state, open PRs without such a review are outside "
                              "the literal set); author-self-finalized PRs are "
                              "INCLUDED only when the author is an evidenced "
                              "maintainer and EXCLUDED otherwise; unrecoverable "
                              "first-review snapshots excluded (1.2); every "
                              "review's author_association/state/submitted_at/"
                              "commit_id saved in "
                              "dataset/evidence/reviews_full_v7.json"),
        },
        "variants": VARIANTS,
        "results": results,
        "consistency": consistency,
        "test_accuracy": acc,
        "dev_accuracy": dacc,
        "test_reject_recall": {v: test[v]["reject_class_recall"] for v in VARIANTS},
        "test_per_pr_pass": {v: test[v]["per_pr_pass_rate"] for v in VARIANTS},
        "test_ci_match": {v: test[v]["ci_match"] for v in VARIANTS},
        "test_coverage_mean": {v: test[v]["coverage_mean"] for v in VARIANTS},
        "test_precision_mean": {v: test[v]["precision_mean"] for v in VARIANTS},
        "delta_ours_vs_empty_acc": round(acc["ours"] - acc["empty"], 4),
        "delta_ours_vs_baseline_acc": round(acc["ours"] - acc["baseline"], 4),
        "delta_ours_vs_baseline_dev_acc": round(dacc["ours"] - dacc["baseline"], 4),
        "delta_ours_vs_empty_pass": round(test["ours"]["per_pr_pass_rate"] - test["empty"]["per_pr_pass_rate"], 4),
        "delta_ours_vs_baseline_pass": round(test["ours"]["per_pr_pass_rate"] - test["baseline"]["per_pr_pass_rate"], 4),
        "bootstrap_p_ours_vs_baseline": boot.get("ours_vs_baseline", {}).get("p_value"),
        "bootstrap_p_ours_vs_empty": boot.get("ours_vs_empty", {}).get("p_value"),
        "C": C,
    }

    with open(os.path.join(REPORTS, "report_card.json"), "w") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    print("final report_card.json written")
    print(json.dumps({
        "test_accuracy": acc, "dev_accuracy": dacc,
        "delta_ours_vs_baseline_acc": report["delta_ours_vs_baseline_acc"],
        "delta_ours_vs_empty_acc": report["delta_ours_vs_empty_acc"],
        "bootstrap_p": (boot.get("ours_vs_baseline", {}).get("p_value"),
                        boot.get("ours_vs_empty", {}).get("p_value")),
        "C": C, "tag": tag}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
