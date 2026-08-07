#!/usr/bin/env python3
"""Build small offline fixtures to exercise scoring without LLM/GitHub.

Reads dataset snapshots + ground truth and writes deterministic placeholder
judge outputs (perfect decisions + one reason per rubric point) so that
score.py and report.py can be validated end-to-end offline.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
SNAP = os.path.join(ROOT, "dataset", "snapshots")
GT = os.path.join(ROOT, "dataset", "ground_truth")
RUBRIC_DIR = os.path.join(ROOT, "rubric")
STATE = os.path.join(HERE, "state")


def main():
    split = json.load(open(os.path.join(ROOT, "dataset", "split.json")))
    out_dir = os.path.join(STATE, "judge_fixture")
    os.makedirs(out_dir, exist_ok=True)
    written = 0
    for pr in split["prs"]:
        if pr["split"] not in ("dev", "test"):
            continue  # rubrics only exist for scored splits
        n = pr["number"]
        gt = json.load(open(os.path.join(GT, f"{n}.json")))
        rubric = json.load(open(os.path.join(RUBRIC_DIR, f"{n}.json")))
        points = rubric.get("points", [])
        decision = gt.get("golden_decision") or ("approve" if gt["merged"] else "reject")
        # deterministic "perfect" reasons derived from rubric summaries
        reasons = [p["summary"] for p in points[:3]]
        judge = {
            "pr_number": n,
            "decision": decision,
            "ci_result": "pass" if len(reasons) < 5 or True else "fail",
            "reasons": reasons,
            "_fixture": True,
        }
        with open(os.path.join(out_dir, f"{n}.json"), "w") as fh:
            json.dump(judge, fh, indent=2, ensure_ascii=False)
        written += 1
    print(f"wrote {written} fixture judge outputs to {out_dir}")


if __name__ == "__main__":
    sys.exit(main())
