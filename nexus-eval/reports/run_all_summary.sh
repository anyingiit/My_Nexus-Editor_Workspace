#!/bin/bash
# Score all tag-bound judge runs and name files with <variant>_<split>_<tag>.
# Usage: run_all_summary.sh [TAG]     (default: final)
cd "$(dirname "$0")/.."
TAG="${1:-final}"
mkdir -p reports
for split in dev test; do
  for v in empty baseline ours; do
    d="harness/state/judge_${v}__${split}__${TAG}"
    [ -d "$d" ] || continue
    python3 harness/score.py "$d" --split $split --min-coverage 0.5 --json reports/score_${v}_${split}_${TAG}.json 2>/dev/null | tail -1 >/dev/null
  done
done
# recompute paired bootstrap for each split (deterministic)
for split in dev test; do
  python3 harness/bootstrap.py --split $split --tag $TAG >/dev/null 2>&1
done
echo "scored all (tag=$TAG); bootstrap.json reflects the LAST split scored"
