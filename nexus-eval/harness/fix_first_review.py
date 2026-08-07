#!/usr/bin/env python3
"""Audit & repair first-review snapshots (AGENTS.md 1.2 audit; requirements B+C).

Deterministic, GitHub API only (no LLM). Two repair actions:

1) RECOVER: for candidates whose earliest formal review had a `commit_id` that
   was force-pushed away, verify the reviewed commit is still resolvable in the
   object database (GET /commits/{sha}), re-fetch its commit message and the
   status/check-runs for that exact SHA, and (re)classify them as STRICT
   first-review snapshots (method=review_commit_id, first_review_basis=formal_review).

2) EXCLUDE: for candidates with NO formal review whose first-review proxy time
   (earliest recorded comment, else created_at) precedes ANY code commit, there
   is no reviewable code state at first review -> they do NOT satisfy 1.2 and
   are moved to an explicit `excluded_unrecoverable_first_review` list (with a
   documented reason). They are NOT placed into train/dev/test for scoring.

Also records, for EVERY finalized candidate:
   - first_review_basis: "formal_review" | "comment" | "created_at"
   - first_review_time and how it was derived
   - strict: whether the snapshot satisfies 1.2 as a faithful first-review state

Writes:
   - dataset/evidence/protocol.json        (protocol + evidence level)
   - dataset/evidence/recovery.json        (per-PR repair audit)
   - dataset/evidence/head_sha_report.json (updated stats)
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect_prs import get_token, GH  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
SNAP = os.path.join(ROOT, "dataset", "snapshots")
GT = os.path.join(ROOT, "dataset", "ground_truth")
EVID = os.path.join(ROOT, "dataset", "evidence")
PR_LIST = os.path.join(ROOT, "dataset", "raw", "_pr_list.json")
REPO = "floatboatai/Nexus-Editor"
API = "https://api.github.com"


def load_gt(n):
    p = os.path.join(GT, f"{n}.json")
    return json.load(open(p)) if os.path.exists(p) else None


def first_review_basis(gt, report_entry):
    """Classify the basis used to timestamp 'first review'.

    formal_review  -> an actual pull-request review event with submitted_at
    comment       -> earliest issue/review comment timestamp (proxy)
    created_at    -> no recorded activity at all (fallback proxy)
    """
    reviews = gt.get("reviews") or []
    formal = [r for r in reviews if r.get("submitted_at")]
    if formal:
        return "formal_review"
    ic = gt.get("issue_comments") or []
    rc = gt.get("review_comments") or []
    if any(c.get("created_at") for c in ic) or any(c.get("created_at") for c in rc):
        return "comment"
    return "created_at"


def backtrace_head(commits, boundary):
    chosen = None
    at_first = []
    for c in commits:
        ct = (c.get("commit", {}).get("committer") or {}).get("date") or \
             (c.get("commit", {}).get("author") or {}).get("date")
        if ct is None:
            continue
        if ct <= boundary:
            chosen = c["sha"]
            at_first.append(c)
        else:
            break
    return chosen, at_first


def recover_candidate(gh, n, entry, snap):
    """Repair a review-commit_id snapshot so the exact reviewed SHA and the
    commits present at first review are pinned authoritatively. Returns True."""
    sha = snap.get("head_sha_at_first_review")
    if not sha:
        return False
    first_review_time = snap.get("first_review_time")
    try:
        cm = gh.get(f"{API}/repos/{REPO}/commits/{sha}")
        if not isinstance(cm, dict) or cm.get("sha") is None:
            raise RuntimeError(f"review commit {sha[:8]} not resolvable")
    except Exception:
        # truly not resolvable - leave as-is and mark non-strict
        entry["strict"] = False
        return False
    msg = ((cm.get("commit") or {}).get("message") or "")[:200]
    # commits present at first review: all commits with committer date <= review time
    commits = []
    try:
        commits = gh.all_pages(f"{API}/repos/{REPO}/pulls/{n}/commits")
    except Exception:
        commits = []
    _, commits_at_first = backtrace_head(commits, first_review_time) if first_review_time else ([], [])
    if commits_at_first:
        # the reviewed commit is normally the last one in the backtrace
        commits_at_first = [
            {"sha": c["sha"], "message": (c.get("commit") or {}).get("message", ""),
             "date": (c.get("commit") or {}).get("committer", {}).get("date") or (c.get("commit") or {}).get("author", {}).get("date")}
            for c in commits_at_first
        ]
    else:
        # force-pushed away: at minimum record the reviewed commit itself
        commits_at_first = [
            {"sha": sha, "message": msg,
             "date": (cm.get("commit") or {}).get("committer", {}).get("date") or (cm.get("commit") or {}).get("author", {}).get("date")}
        ]
    snap["commits_at_first_review"] = commits_at_first
    snap["head_sha_at_first_review"] = sha
    snap["first_review_basis"] = "formal_review"
    snap["strict_first_review"] = True
    # re-fetch status/check-runs + diff for the exact reviewed SHA (authoritative)
    status_first = gh.get(f"{API}/repos/{REPO}/commits/{sha}/status")
    checkruns_first = gh.get(f"{API}/repos/{REPO}/commits/{sha}/check-runs")
    base_sha = snap.get("base_sha")
    compare_first = None
    if base_sha:
        compare_first = gh.get(f"{API}/repos/{REPO}/compare/{base_sha}...{sha}")
    if compare_first and isinstance(compare_first, dict):
        snap["compare_first_review"] = {
            "status": compare_first.get("status"),
            "ahead_by": compare_first.get("ahead_by"),
            "behind_by": compare_first.get("behind_by"),
            "files": compare_first.get("files"),
        }
    if isinstance(status_first, dict):
        snap["status_first"] = compact_status(status_first)
    if isinstance(checkruns_first, dict):
        snap["check_runs_first"] = compact_checkruns(checkruns_first)
    with open(os.path.join(SNAP, str(n), "first.json"), "w") as fh:
        json.dump(snap, fh, indent=2, ensure_ascii=False)
    entry["method"] = "review_commit_id"
    entry["commit_sha_unrecoverable"] = None
    entry["recovered"] = True
    entry["first_review_basis"] = "formal_review"
    entry["strict"] = True
    return True


def compact_status(s):
    return {
        "state": s.get("state"),
        "total_count": s.get("total_count"),
        "statuses": [
            {"context": x.get("context"), "state": x.get("state"),
             "target_url": x.get("target_url"), "description": x.get("description")}
            for x in s.get("statuses", [])
        ],
    } if s and isinstance(s, dict) else None


def compact_checkruns(cr):
    return {
        "total_count": cr.get("total_count"),
        "check_runs": [
            {"name": x.get("name"), "status": x.get("status"),
             "conclusion": x.get("conclusion")} for x in cr.get("check_runs", [])
        ],
    } if cr and isinstance(cr, dict) else None


def load_report():
    p = os.path.join(EVID, "head_sha_report.json")
    if os.path.exists(p):
        return json.load(open(p))
    return {"stats": {}, "prs": {}}


def main():
    os.makedirs(EVID, exist_ok=True)
    prl = {p["number"]: p for p in json.load(open(PR_LIST))}
    report = load_report()
    prs_map = report.get("prs", {})

    token = get_token()
    gh = GH(token)

    protocol = {
        "first_review_time_definition": (
            "AGENTS.md 1.2 requires the state at the FIRST review. When a PR has "
            "a formal Pull Request review, that review's submitted_at is used and "
            "the review's commit_id (when resolvable) pins the exact reviewed SHA. "
            "For candidates WITHOUT a formal review, the first-review timestamp is "
            "a documented PROXY: the earliest recorded issue/review comment "
            "timestamp, else created_at (deviation from the literal 'first formal "
            "review' definition is explicit; see first_review_basis counts)."),
        "first_review_basis_counts": {},
        "admin_permission_evidence": {
            "method": (
                "GitHub REST /repos/{repo}/collaborators/{user}/permission and "
                "/collaborators were attempted with the contributing GitHub App. "
                "Both return HTTP 403 'Resource not accessible by integration'. "
                "The App token therefore cannot prove any actor's manage "
                "permission. 'Handled by a maintainer' is operationalized ONLY as "
                "'the merged/closed PR records a final closing/merging actor "
                "(merged_by / closed_by from issue events)'. The actor's actual "
                "permission level is UNVERIFIED -> a recorded limitation. The final "
                "actor is NOT asserted to hold admin permission."),
            "permission_endpoint_accessible": False,
            "evidence_level": "unverified_permission_actor_recorded",
            "n_candidates_with_final_actor": 0,
            "n_candidates_with_verified_admin_permission": 0,
        },
        "candidate_filter_rules": [
            "Candidates = FINALIZED PRs only (merged OR closed_at present).",
            "A PR with a documented final closing/merging actor is eligible; the "
            "actor's permission is unverifiable via the integration token "
            "(403), so no PR is asserted to be 'admin-handled' - only "
            "'finalized by an actor'.",
            "Candidates whose first-review snapshot is NOT a faithful 1.2 state "
            "(no formal review AND no code commit predating the first-review "
            "proxy time) are EXCLUDED from train/dev/test into "
            "'excluded_unrecoverable_first_review'.",
        ],
        "recovery": {
            "recovered_review_commit_id": [],
            "excluded_unrecoverable": [],
        },
    }

    # finalized candidates
    nums = []
    for n_str in sorted(prl.keys()):
        p = prl[n_str]
        if p.get("merged_at") or p.get("closed_at"):
            nums.append(int(n_str))

    basis_counts = {}
    recovered = []
    excluded = []
    for n in sorted(nums):
        n_s = str(n)
        gt = load_gt(n)
        entry = dict(prs_map.get(n_s) or {})
        entry.setdefault("pr_number", n)
        basis = first_review_basis(gt, entry)
        basis_counts[basis] = basis_counts.get(basis, 0) + 1
        entry["first_review_basis"] = basis

        # snapshot path
        snap_path = os.path.join(SNAP, n_s, "first.json")
        if not os.path.exists(snap_path):
            continue
        snap = json.load(open(snap_path))
        method = entry.get("method", "")

        if method == "review_commit_id(sha_unrecoverable)" or method == "review_commit_id":
            force_pushed = (method == "review_commit_id(sha_unrecoverable)")
            # recover: review commit id present but was force-pushed away
            try:
                ok = recover_candidate(gh, n, entry, snap)
                if ok:
                    recovered.append(n)
                    entry["strict"] = True
                    entry["force_pushed_recovered"] = force_pushed
            except Exception as e:
                entry["recovery_error"] = repr(e)
                entry.setdefault("strict", False)
        elif method == "unrecoverable_best_effort":
            # pure best-effort: no formal review, no code at proxy time
            entry["strict"] = False
            entry["recovered"] = False
            entry["excluded_reason"] = (
                "no formal review; earliest-recorded-comment/created_at proxy "
                "precedes any code commit -> no reviewable code state at the "
                "first-review proxy time; does not satisfy AGENTS.md 1.2")
            excluded.append({"pr_number": n, "reason": entry["excluded_reason"],
                             "first_review_time": entry.get("first_review_time"),
                             "head_sha_first": snap.get("head_sha_at_first_review")})
        else:
            entry["strict"] = True  # review_commit_id or time_backtrace
            # annotate recovery status for the earlier review_commit_id methods
            entry["recovered"] = False

        entry["first_review_basis"] = basis
        prs_map[n_s] = entry

    # update admin evidence counts
    admin = {}
    if os.path.exists(os.path.join(EVID, "candidate_admin.json")):
        admin = json.load(open(os.path.join(EVID, "candidate_admin.json")))
    n_actor = sum(1 for a in admin.values() if a.get("merged_by") or a.get("closed_by"))
    protocol["admin_permission_evidence"]["n_candidates_with_final_actor"] = n_actor
    protocol["admin_permission_evidence"]["n_candidates_with_verified_admin_permission"] = 0
    protocol["recovery"]["recovered_review_commit_id"] = recovered
    protocol["recovery"]["excluded_unrecoverable"] = excluded
    protocol["first_review_basis_counts"] = basis_counts

    # rebuild stats from entries (consistent with repair)
    stats = {"review_commit_id": 0, "time_backtrace": 0,
             "unrecoverable_best_effort": 0, "failures": 0}
    for e in prs_map.values():
        m = e.get("method")
        if m in stats:
            stats[m] += 1
        else:
            stats.setdefault(m, 0)
            stats[m] += 1
    stats["excluded_unrecoverable"] = len(excluded)
    stats["recovered_from_force_pushed_review_commit_ids"] = len(recovered) if False else sum(
        1 for e in prs_map.values()
        if e.get("recovered") and e.get("method") == "review_commit_id"
        and e.get("force_pushed_recovered"))

    with open(os.path.join(EVID, "head_sha_report.json"), "w") as fh:
        json.dump({"stats": stats, "prs": prs_map}, fh, indent=2, ensure_ascii=False)
    with open(os.path.join(EVID, "protocol.json"), "w") as fh:
        json.dump(protocol, fh, indent=2, ensure_ascii=False)
    with open(os.path.join(EVID, "recovery.json"), "w") as fh:
        json.dump({
            "recovered_review_commit_id": recovered,
            "excluded_unrecoverable": excluded,
            "basis_counts": basis_counts,
        }, fh, indent=2, ensure_ascii=False)

    print("basis counts:", basis_counts)
    print("recovered:", recovered)
    print("excluded:", [e["pr_number"] for e in excluded])
    print("stats:", stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
