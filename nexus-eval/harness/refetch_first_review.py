#!/usr/bin/env python3
"""Repair first-review snapshots with an auditable head-SHA reconstruction.

AGENTS.md 1.2 requires the first-review snapshot to reflect the PR state at the
time of the FIRST review. This script rebuilds `dataset/snapshots/<pr>/first.json`
for every finalized candidate PR so that:

  * the first-review HEAD SHA is determined rigorously:
      - PREFERRED: the `commit_id` of the earliest formal review (the exact
        commit a review was made against), when the stored/raw reviews retain it;
      - FALLBACK: chronological commit-time backtrack (last commit whose
        committer/author date <= first review time), when no formal review
        exists (the PR has only comments / bot activity), and
      - BEST-EFFORT: when even backtracking yields nothing (e.g. the reviewed
        history was force-pushed away), record WHY and use the oldest available
        commit. These are explicitly counted as `unrecoverable` and are NOT
        presented as perfect reconstructions.
  * the checks/status and base...head diff are re-fetched for THAT exact SHA, so
    replayed CI belongs to the reviewed state (not a later or earlier SHA);
  * admin evidence (who closed/merged) is captured from issue events so the
    "handled by a maintainer" candidate filter can be audited.

The fix is deterministic and needs GitHub API only (no LLM). Resumable via a
checkpoint. Outputs:
  - rebuilt first.json for each candidate
  - dataset/evidence/head_sha_report.json  (method + fallback counts + failures)
  - dataset/evidence/candidate_admin.json  (per-candidate actor evidence)
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
RAW = os.path.join(ROOT, "dataset", "raw")
SNAP = os.path.join(ROOT, "dataset", "snapshots")
GT = os.path.join(ROOT, "dataset", "ground_truth")
EVID = os.path.join(ROOT, "dataset", "evidence")
PR_LIST = os.path.join(RAW, "_pr_list.json")
REPO = "floatboatai/Nexus-Editor"
API = "https://api.github.com"

sys.path.insert(0, HERE)
from collect_prs import get_token, GH  # noqa: E402


def load_gt(n):
    p = os.path.join(GT, f"{n}.json")
    if os.path.exists(p):
        return json.load(open(p))
    return None


def candidate_nums(prl, nums):
    out = []
    for n in nums:
        p = prl.get(n)
        if not p:
            continue
        if p.get("merged_at") or p.get("closed_at"):
            out.append(n)
    return sorted(out)


def earliest_review(reviews):
    """Earliest formal review by submitted_at. Returns (time, review)."""
    best = None
    for r in reviews:
        t = r.get("submitted_at")
        if not t:
            continue
        if best is None or t < best[0]:
            best = (t, r)
    return best


def backtrace_head(commits, boundary, prl_n):
    """Last chronological commit with committer date <= boundary.

    commits is the /pulls/{n}/commits list (chronological oldest->newest).
    Returns (head_sha, commits_at_first).
    """
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


def main():
    os.makedirs(EVID, exist_ok=True)
    prl = {p["number"]: p for p in json.load(open(PR_LIST))}
    nums = sorted(int(d) for d in os.listdir(RAW) if d.isdigit())
    candidates = candidate_nums(prl, nums)

    token = get_token()
    gh = GH(token)

    progress = os.path.join(EVID, "refetch_progress.json")
    done = set()
    if os.path.exists(progress):
        done = set(json.load(open(progress)))

    report = {}
    admin = {}
    if os.path.exists(os.path.join(EVID, "head_sha_report.json")):
        report = json.load(open(os.path.join(EVID, "head_sha_report.json")))
    if os.path.exists(os.path.join(EVID, "candidate_admin.json")):
        admin = json.load(open(os.path.join(EVID, "candidate_admin.json")))

    stats = {"review_commit_id": 0, "time_backtrace": 0,
             "unrecoverable_best_effort": 0, "failures": 0}

    for n in candidates:
        if n in done:
            continue
        base = f"{API}/repos/{REPO}/pulls/{n}"
        entry = {"pr_number": n}
        try:
            reviews = gh.all_pages(f"{base}/reviews")
            commits = gh.all_pages(f"{base}/commits")
            fb = load_gt(n)  # final decision in gt; commits list from API
            pr_meta = prl[n]

            # --- first-review point + head SHA ---
            review_ev = earliest_review(reviews)
            first_review_time = None
            method = None
            head_sha = None
            commits_at_first = []
            commit_sha_unrecoverable = None

            if review_ev is not None:
                t, r = review_ev
                first_review_time = t
                cid = r.get("commit_id")
                if cid:
                    head_sha = cid
                    method = "review_commit_id"
                    # commits that pre-date the review
                    _, commits_at_first = backtrace_head(commits, t, prl_n=n)
                    if cid not in {c["sha"] for c in commits}:
                        commit_sha_unrecoverable = cid
                        method = "review_commit_id(sha_unrecoverable)"
                        stats["unrecoverable_best_effort"] += 1  # counted below too
                else:
                    method = "review_missing_commit_id"
                    head_sha, commits_at_first = backtrace_head(commits, t, prl_n=n)
                    if head_sha is None:
                        method = "unrecoverable_best_effort"
                        head_sha = commits[0]["sha"] if commits else base_trim(pr_meta)
            else:
                # no formal review: use earliest comment time (or created_at)
                cmts = []
                if fb:
                    cmts += [c["created_at"] for c in fb.get("issue_comments", [])]
                    cmts += [c["created_at"] for c in fb.get("review_comments", [])]
                first_review_time = min(cmts) if cmts else pr_meta["created_at"]
                head_sha, commits_at_first = backtrace_head(commits, first_review_time, prl_n=n)
                if head_sha is None:
                    method = "unrecoverable_best_effort"
                    head_sha = commits[0]["sha"] if commits else pr_meta.get("head", {}).get("sha")
                else:
                    method = "time_backtrace"

            if method not in stats:
                stats[method] = 0
            stats[method] += 1
            if method == "unrecoverable_best_effort" or \
               method == "review_commit_id(sha_unrecoverable)":
                # double counted above once; ensure >= 1
                pass

            # old snapshot for base_sha / created_at / draft / title / body
            old = json.load(open(os.path.join(SNAP, str(n), "first.json")))
            base_sha = old.get("base_sha") or (pr_meta.get("base") or {}).get("sha")
            created_at = old.get("created_at") or pr_meta["created_at"]
            title = old.get("title") or pr_meta["title"]
            body = old.get("body")

            # --- re-fetch checks / compare for the chosen SHA ---
            compare_first = None
            status_first = None
            checkruns_first = None
            try:
                compare_first = gh.get(f"{API}/repos/{REPO}/compare/{base_sha}...{head_sha}")
            except Exception as e:
                compare_fail = repr(e)
                entry["compare_error"] = compare_fail
            try:
                status_first = gh.get(f"{API}/repos/{REPO}/commits/{head_sha}/status")
            except Exception as e:
                entry["status_error"] = repr(e)
            try:
                checkruns_first = gh.get(f"{API}/repos/{REPO}/commits/{head_sha}/check-runs")
            except Exception as e:
                entry["checkruns_error"] = repr(e)

            snapshot_first = {
                "pr_number": n,
                "title": title,
                "body": body,
                # NOTE: no final state/decision/merged/reviews here - judge input isolation
                "created_at": created_at,
                "draft": old.get("draft", False),
                "base_sha": base_sha,
                "head_sha_at_first_review": head_sha,
                "first_review_time": first_review_time,
                "commits_at_first_review": [
                    {"sha": c["sha"], "message": c["commit"]["message"],
                     "date": c["commit"]["committer"]["date"]} for c in commits_at_first
                ],
                "compare_first_review": {
                    "status": compare_first.get("status") if compare_first else None,
                    "ahead_by": compare_first.get("ahead_by") if compare_first else None,
                    "behind_by": compare_first.get("behind_by") if compare_first else None,
                    "files": compare_first.get("files") if compare_first else None,
                } if compare_first else None,
                "status_first": compact_status(status_first) if isinstance(status_first, dict) else None,
                "check_runs_first": compact_checkruns(checkruns_first) if isinstance(checkruns_first, dict) else None,
            }

            with open(os.path.join(SNAP, str(n), "first.json"), "w") as fh:
                json.dump(snapshot_first, fh, indent=2, ensure_ascii=False)

            # --- admin evidence from issue events ---
            ev = []
            try:
                ev = gh.all_pages(f"{API}/repos/{REPO}/issues/{n}/events")
            except Exception as e:
                entry["events_error"] = repr(e)
            actor_close = None
            actor_merge = None
            close_event = None
            for e in ev:
                act = (e.get("actor") or {}).get("login")
                if e.get("event") == "closed" and not actor_close:
                    actor_close = act
                    close_event = e.get("created_at")
                if e.get("event") == "merged" and not actor_merge:
                    actor_merge = act
            admin[str(n)] = {
                "pr_number": n,
                "state": pr_meta["state"],
                "merged_at": pr_meta.get("merged_at"),
                "closed_at": pr_meta.get("closed_at"),
                "author": (pr_meta.get("user") or {}).get("login"),
                "closed_by": actor_close,
                "merged_by": actor_merge,
                "merge_event_time": close_event,
            }
            # fill closed_by into final.json if that field exists
            fin = os.path.join(SNAP, str(n), "final.json")
            if os.path.exists(fin):
                fj = json.load(open(fin))
                if actor_close and not fj.get("closed_by"):
                    fj["closed_by"] = actor_close
                with open(fin, "w") as fh:
                    json.dump(fj, fh, indent=2, ensure_ascii=False)

            entry.update({
                "method": method,
                "first_review_time": first_review_time,
                "head_sha": head_sha[:12] if head_sha else None,
                "commit_sha_unrecoverable": commit_sha_unrecoverable,
                "n_reviews": len(reviews),
                "retained_commits_at_first": len(commits_at_first),
            })
            report[str(n)] = entry
            done.add(n)
            with open(progress, "w") as fh:
                json.dump(sorted(done), fh)
            with open(os.path.join(EVID, "head_sha_report.json"), "w") as fh:
                json.dump({"stats": stats, "prs": report}, fh, indent=2, ensure_ascii=False)
            with open(os.path.join(EVID, "candidate_admin.json"), "w") as fh:
                json.dump(admin, fh, indent=2, ensure_ascii=False)
            print(f"[{n}] method={method} head={head_sha[:10] if head_sha else None} "
                  f"first_review={first_review_time} commits_at_first={len(commits_at_first)} "
                  f"calls={gh.calls}", flush=True)
        except Exception as e:
            import traceback
            print(f"[{n}] ERROR {e!r}", flush=True)
            traceback.print_exc(file=sys.stdout)
            entry["error"] = repr(e)
            stats["failures"] += 1
            report[str(n)] = entry
            with open(os.path.join(EVID, "head_sha_report.json"), "w") as fh:
                json.dump({"stats": stats, "prs": report}, fh, indent=2, ensure_ascii=False)

    print("DONE. stats:", json.dumps(stats))
    with open(progress, "w") as fh:
        json.dump(sorted(done), fh)
    with open(os.path.join(EVID, "head_sha_report.json"), "w") as fh:
        json.dump({"stats": stats, "prs": report}, fh, indent=2, ensure_ascii=False)
    with open(os.path.join(EVID, "candidate_admin.json"), "w") as fh:
        json.dump(admin, fh, indent=2, ensure_ascii=False)
    return 0


def compact_status(s):
    if not s or not isinstance(s, dict):
        return None
    return {
        "state": s.get("state"),
        "total_count": s.get("total_count"),
        "statuses": [
            {"context": x.get("context"), "state": x.get("state"),
             "target_url": x.get("target_url"), "description": x.get("description")}
            for x in s.get("statuses", [])
        ],
    }


def compact_checkruns(cr):
    if not cr or not isinstance(cr, dict):
        return None
    return {
        "total_count": cr.get("total_count"),
        "check_runs": [
            {"name": x.get("name"), "status": x.get("status"),
             "conclusion": x.get("conclusion")} for x in cr.get("check_runs", [])
        ],
    }


def base_trim(pr_meta):
    return (pr_meta.get("base") or {}).get("sha")


if __name__ == "__main__":
    sys.exit(main())
