#!/usr/bin/env python3
"""Time split (train 60 / dev 20 / test 20) + ground-truth isolation + stats.

Round 12 / v7 universe (root AGENTS.md 1.1, "handled by an actor with manage
permission"): every PR that was actually APPROVED / closed / merged by an
evidenced actor. Deterministic, no LLM.

Universe = A ∪ B
  A) FINALIZED PRs with a final actor carrying an evidence tier that legally
     includes them (weak_maintainer_evidence / final_actor_collaborator_
     association / distinct_actor_handled). Author-self-finalized WITHOUT
     direct-push weak-maintainer evidence are excluded (Round 5 rule kept).
  B) OPEN PRs that received at least one ADMIN DECISION FORMAL REVIEW by an
     admin-candidate-evidenced actor (the REVIEW ACTOR's own author_association
     is OWNER/MEMBER/COLLABORATOR OR the actor is a direct-push weak
     maintainer: ketd / 2391384896 / redreamality / aoe; any other commenter's
     association never substitutes for the review actor). GT = state of the
     EARLIEST such decision review (APPROVED -> approve; CHANGES_REQUESTED /
     DISMISSED -> reject). Open PRs WITHOUT such a review are NOT in the scored
     universe.

Exclusions (recorded, not silently dropped):
  - unrecoverable first-review snapshots (AGENTS.md 1.2): no formal review AND
    no code commit predating the first-review proxy time (from head_sha_report).
  - open PRs without an admin decision review (literality rule).
  - author-self-finalized without maintainer evidence (1.1).

Sort by authoritative created_at then 60/20/20 in time order. Writes
dataset/split.json (per-candidate golden_decision, snapshot_type, basis, tiers)
and dataset/leak_check.json (first.json must not carry ground truth).

Golden decisions for OPEN-with-review candidates come from the review state
(dataset/evidence/review_universe_v6.json + GT), not from a final-state guess.
"""
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
RAW = os.path.join(ROOT, "dataset", "raw")
SNAP = os.path.join(ROOT, "dataset", "snapshots")
EVID = os.path.join(ROOT, "dataset", "evidence")
PR_LIST = os.path.join(RAW, "_pr_list.json")
SENTINEL = "1970-01-01T00:00:00Z"


def iso_key(s):
    return s if s else SENTINEL


def main():
    # ---- authoritative PR list ----
    pr_list = {p["number"]: p for p in json.load(open(PR_LIST))}

    # ---- Round 12 / v7 universe (association-gated admin evidence) ----
    universe = json.load(open(os.path.join(EVID, "review_universe_v7.json")))["universe"]

    # ---- finalized first-review audit (basis / unrecoverable) ----
    sha_report = json.load(open(os.path.join(EVID, "head_sha_report.json"))).get("prs", {})
    admin_audit = json.load(open(os.path.join(EVID, "admin_audit.json"))).get("prs", {})

    cands = []
    open_excluded = []
    self_excluded = []
    for n_str, rec in sorted(universe.items(), key=lambda kv: int(kv[0])):
        n = int(n_str)
        pr = pr_list[n]
        if not rec.get("include"):
            if rec.get("open"):
                open_excluded.append({"number": n, "created_at": pr["created_at"],
                                      "state": pr["state"], "reason": rec.get("exclude_reason")})
            else:
                self_excluded.append({"number": n, "created_at": pr["created_at"],
                                      "state": pr["state"], "reason": rec.get("exclude_reason") or
                                      "final action by author without maintainer evidence (1.1)"})
            continue

        created_at = pr["created_at"]
        if not created_at or created_at == SENTINEL:
            raise SystemExit(f"FATAL: PR {n} has no authoritative created_at")

        merged = bool(pr.get("merged")) or bool(rec.get("merged"))
        is_open = rec.get("open", False)
        sr = sha_report.get(str(n)) or {}
        unrecoverable = bool(sr.get("excluded_reason")) or \
            sr.get("method") in ("unrecoverable_best_effort",)
        if is_open:
            basis = "formal_review"
            snapshot_type = "literal_first_review"
        else:
            basis = sr.get("first_review_basis") or "unknown"
            snapshot_type = ("literal_first_review" if basis == "formal_review"
                             else "first_activity_proxy")

        rec_out = {
            "number": n,
            "created_at": created_at,
            "merged": merged,
            "state": pr["state"],
            "closed_at": pr.get("closed_at"),
            "open": is_open,
            "golden_decision": rec["golden"],
            "gt_basis": rec.get("gt_basis"),
            "evidence_tier": rec.get("evidence_tier") or
                             (admin_audit.get(str(n)) or {}).get("evidence_tier", "open_admin_review"),
            "first_review_basis": basis,
            "snapshot_type": snapshot_type,
            "literal_first_review": snapshot_type == "literal_first_review",
            "unrecoverable": unrecoverable,
        }
        cands.append(rec_out)

    # ---- drop unrecoverable ----
    unrec = [c for c in cands if c["unrecoverable"]]
    cands = [c for c in cands if not c["unrecoverable"]]

    cands.sort(key=lambda p: (iso_key(p["created_at"]), p["number"]))
    n = len(cands)
    n_train = max(int(n * 0.60), 1)
    n_dev = max(int(n * 0.20), 1)
    n_test = n - n_train - n_dev
    if n_test < 1:
        n_test = 1
        n_dev = max(n_dev - 1, 0)
        n_train = n - n_test - n_dev

    for i, p in enumerate(cands):
        p["split"] = "train" if i < n_train else ("dev" if i < n_train + n_dev else "test")

    # ---- assertions: time boundaries ----
    def split_times(name):
        return [p["created_at"] for p in cands if p["split"] == name]
    t_tr, t_dev, t_te = split_times("train"), split_times("dev"), split_times("test")
    assert t_tr and t_dev and t_te, "every split must have >=1 member"
    assert t_tr[-1] <= t_dev[0], f"train end {t_tr[-1]} > dev start {t_dev[0]}"
    assert t_dev[-1] <= t_te[0], f"dev end {t_dev[-1]} > test start {t_te[0]}"
    assert all(p["created_at"] != SENTINEL for p in cands)
    assert n_train + n_dev + n_test == n

    result = {
        "total_collected": len(pr_list),
        "n_candidates": n,
        "n_open_excluded": len(open_excluded),
        "n_self_finalized_excluded": len(self_excluded),
        "n_unrecoverable_first_review_excluded": len(unrec),
        "n_train": n_train,
        "n_dev": n_dev,
        "n_test": n_test,
        "split_policy": ("time-ascending by created_at (authoritative _pr_list.json); "
                         "60/20/20; universe = A (finalized PRs with an evidence-gated "
                         "final actor, Round5 rule) UNION B (open PRs with an admin "
                         "decision formal review where the REVIEW ACTOR's own "
                         "author_association is OWNER/MEMBER/COLLABORATOR OR the actor "
                         "carries direct-push weak evidence; other commenters' "
                         "associations never substitute for the review actor); open PRs "
                         "without an admin decision review excluded; unrecoverable "
                         "first-review snapshots excluded (1.2)"),
        "admin_policy": (
            "Manage permission is NOT provable via REST (403, recorded; never "
            "claimed). Finalized: final-actor evidence tiers (weak direct-push / "
            "final-actor COLLABORATOR+ / distinct non-author actor); author-self-"
            "finalized WITHOUT weak maintainer evidence excluded. Open: admitted "
            "only via an admin DECISION formal review whose review actor is "
            "admin-candidate-evidenced (own association OWNER/MEMBER/COLLABORATOR "
            "OR direct-push weak); GT = earliest admin decision-review state; "
            "first-review snapshot pinned to that review's exact commit_id (1.2)."),
        "first_review_time_policy": (
            "Literal first-review snapshot = first formal admin review "
            "(submitted_at + exact commit_id) whenever such a review exists "
            "(incl. every open-with-admin-review candidate). Finalized PRs "
            "without a formal review keep the documented first-activity proxy "
            "(AGENTS.md 1.2, never disguised as literal)."),
        "dates": {
            "earliest": cands[0]["created_at"],
            "latest": cands[-1]["created_at"],
            "train_end": cands[n_train - 1]["created_at"],
            "dev_end": cands[n_train + n_dev - 1]["created_at"],
            "test_end": cands[-1]["created_at"],
        },
        "golden_distribution": dict(Counter(p["golden_decision"] for p in cands)),
        "open_included": sorted(p["number"] for p in cands if p["open"]),
        "open_excluded": open_excluded,
        "excluded_self_finalized": self_excluded,
        "excluded_unrecoverable_first_review": [
            {"number": c["number"], "created_at": c["created_at"],
             "reason": "first-review snapshot not recoverable to a faithful 1.2 state"}
            for c in unrec],
        "first_review_basis_counts": dict(Counter(p["first_review_basis"] for p in cands)),
        "literal_first_review_counts": dict(Counter(
            "literal_first_review" if p["literal_first_review"] else "first_activity_proxy"
            for p in cands)),
        "prs": cands,
    }
    with open(os.path.join(ROOT, "dataset", "split.json"), "w") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)

    # stats
    for s in ("train", "dev", "test"):
        row = [p for p in cands if p["split"] == s]
        gc = Counter(p["golden_decision"] for p in row)
        print(f"{s}: n={len(row)} golden={dict(gc)} "
              f"[{row[0]['created_at']} .. {row[-1]['created_at']}] "
              f"literal={sum(1 for p in row if p['literal_first_review'])} "
              f"open_in_split={[p['number'] for p in row if p['open']]}")

    # leak check
    leaks = []
    for d in sorted(os.listdir(SNAP)):
        f = os.path.join(SNAP, d, "first.json")
        if not os.path.exists(f):
            continue
        data = json.load(open(f))
        if any(k in data for k in ("reviews", "review_comments", "issue_comments",
                                   "merged_by", "closed_by", "merged", "state_final",
                                   "decision", "closure", "golden_decision")):
            leaks.append(f"{d}/first.json")
    with open(os.path.join(ROOT, "dataset", "leak_check.json"), "w") as fh:
        json.dump({"leaks": leaks}, fh, indent=2)
    print("leaks:", leaks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
