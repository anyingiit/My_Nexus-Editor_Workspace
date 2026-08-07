#!/bin/bash
# Run judge for all variants x splits with a shared tag.
# Usage: run_matrix.sh [TAG]    (default: untagged)
cd "$(dirname "$0")"
TAG="${1:-untagged}"
for split in dev test; do
  for v in empty baseline ours; do
    echo "=== [$split] $v (tag=$TAG) ==="
    python3 judge.py "$v" "$split" 0 "$TAG"
  done
done
echo "ALL JUDGE RUNS DONE (tag=$TAG)"
