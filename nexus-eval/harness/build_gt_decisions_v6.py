#!/usr/bin/env python3
"""Enrich scorer-only ground_truth files with a deterministic golden decision.

For every candidate PR (finalized OR open-with-admin-review) we add, to the
scorer-only ground_truth/<pr>.json:
  - golden_decision : "approve" | "reject"   (the GT label the scorer reads)
  - gt_basis        : why (final outcome | earliest admin decision review)
  - admin_review    : the deciding admin review (user/state/submitted_at/
                      commit_id) when GT came from a review

The judge NEVER reads these files; only the scorer does. Nothing here is LLM or
network - it is a deterministic projection of the Round-6 audit.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
EVID = os.path.join(ROOT, "dataset", "evidence")
GTD = os.path.join(ROOT, "dataset", "ground_truth")


def main():
    universe = json.load(open(os.path.join(EVID, "review_universe_v7.json")))["universe"]
    from collections import Counter
    n_upd = 0
    n_open = 0
    for n_str, rec in universe.items():
        n = int(n_str)
        if not rec.get("include"):
            continue
        gtp = os.path.join(GTD, f"{n}.json")
        if not os.path.exists(gtp):
            print(f"no GT file for {n}")
            continue
        gt = json.load(open(gtp))
        golden = rec["golden"]
        if rec.get("open"):
            drev = rec.get("gt_admin_review") or {}
            gt["golden_decision"] = golden
            gt["gt_basis"] = "earliest admin decision review"
            gt["admin_review"] = {
                "user": drev.get("user"),
                "state": drev.get("state"),
                "submitted_at": drev.get("submitted_at"),
                "commit_id": drev.get("commit_id"),
                "author_association": drev.get("author_association"),
                "evidence": drev.get("evidence"),
            }
            n_open += 1
        else:
            gt["golden_decision"] = golden
            gt["gt_basis"] = "final outcome (merged->approve, closed->reject)"
        json.dump(gt, open(gtp, "w"), indent=2, ensure_ascii=False)
        n_upd += 1

    # sanity: every included PR now has a golden_decision
    missing = [k for k, r in universe.items() if r["include"]
               and not os.path.exists(os.path.join(GTD, f"{int(k)}.json"))]
    counts = Counter(r["golden"] for r in universe.values() if r["include"])
    print(f"updated {n_upd} GT files (open-with-admin-review: {n_open})")
    print("golden distribution:", dict(counts))
    print("missing GT:", missing)
    return 0


if __name__ == "__main__":
    sys.exit(main())
