#!/bin/bash
# Dev evaluation (tag-bound so results are never mixed with stale rounds).
# Usage: run_dev.sh [TAG]
cd "$(dirname "$0")"
TAG="${1:-dev_iteration}"
for v in empty baseline ours; do
  echo "=== [dev] $v (tag=$TAG) ==="
  python3 judge.py "$v" dev 0 "$TAG"
done
echo "DEV DONE (tag=$TAG)"
