#!/usr/bin/env python3
"""Full evaluation driver (tag-bound, split-bound state).

Runs judge over the requested splits for the three variants (empty / baseline /
ours), scores them, and (for dev) measures consistency C. Judge state is stored
under harness/state/judge_<variant>__<split>__<tag>/ so results are never
silently reused across splits, rounds, or AGENTS.md contents (a stale manifest
causes a clean re-run). Does NOT write report_card.json -- that is assembled by
reports/make_final_card.py from deterministic score + bootstrap + freeze files.

Usage:
  run_all.py --tag final --splits dev test [--dev-limit N] [--test-limit N]
             [--skip-judge] [--skip-consistency]
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
REPORTS = os.path.join(ROOT, "reports")
STATE = os.path.join(HERE, "state")

VARIANTS = ["empty", "baseline", "ours"]


def sh(*args, **kw):
    return subprocess.run(args, cwd=os.path.dirname(__file__), check=True, **kw)


def run_judge(variant, split, tag="final", limit=0):
    cmd = [sys.executable, os.path.join(HERE, "judge.py"), variant, split]
    if limit:
        cmd.append(str(limit))
    cmd.append(tag)
    sh(*cmd)


def run_score(variant, split, tag="final"):
    run_dir = os.path.join(STATE, f"judge_{variant.replace('/', '_')}__{split}__{tag}")
    out = os.path.join(REPORTS, f"score_{variant}_{split}_{tag}.json")
    sh(sys.executable, os.path.join(HERE, "score.py"), run_dir,
       "--split", split, "--json", out)
    return json.load(open(out))


def main():
    tag = "final"
    splits = ["dev", "test"]
    dev_limit = 0
    test_limit = 0
    skip_judge = False
    skip_consistency = False
    for i, a in enumerate(sys.argv[1:]):
        if a == "--tag":
            tag = sys.argv[i + 2]
        elif a == "--splits":
            splits = sys.argv[i + 2].split()
        elif a.startswith("--dev-limit"):
            dev_limit = int(sys.argv[i + 2])
        elif a.startswith("--test-limit"):
            test_limit = int(sys.argv[i + 2])
        elif a == "--skip-judge":
            skip_judge = True
        elif a == "--skip-consistency":
            skip_consistency = True
    os.makedirs(REPORTS, exist_ok=True)

    if not skip_judge:
        for split in splits:
            limit = dev_limit if split == "dev" else test_limit
            for v in VARIANTS:
                print(f"=== judge {v} on {split} tag={tag} ===", flush=True)
                run_judge(v, split, tag, limit)

    results = {}
    for split in splits:
        results[split] = {}
        for v in VARIANTS:
            results[split][v] = run_score(v, split, tag)
            print(f"{v} {split}: acc={results[split][v].get('decision_accuracy')} "
                  f"recall={results[split][v].get('reject_class_recall')}", flush=True)

    if "dev" in splits and not skip_consistency:
        print("=== consistency C (dev) ===", flush=True)
        n_sample = int(os.environ.get("C_SAMPLE", "8"))
        n_runs = int(os.environ.get("C_RUNS", "5"))
        sh(sys.executable, os.path.join(HERE, "consistency.py"), "ours",
           str(n_sample), str(n_runs), "dev", tag)

    # paired bootstrap (deterministic) for each split
    for split in splits:
        sh(sys.executable, os.path.join(HERE, "bootstrap.py"),
           "--split", split, "--tag", tag)

    print("Done. score files + bootstrap.json under reports/. "
          "Assemble the report card with reports/make_final_card.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
