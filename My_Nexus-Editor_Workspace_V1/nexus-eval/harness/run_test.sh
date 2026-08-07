#!/bin/bash
# Formal TEST evaluation. Run EXACTLY ONCE after freezing AGENTS.md.
# Tag must equal the tag used for the freeze (default: final).
# Usage: run_test.sh [TAG]
cd "$(dirname "$0")"
TAG="${1:-final}"
for v in empty baseline ours; do
  echo "=== [test] $v (tag=$TAG) ==="
  python3 judge.py "$v" test 0 "$TAG"
done
echo "TEST DONE (tag=$TAG)"
