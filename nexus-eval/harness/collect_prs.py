#!/usr/bin/env python3
"""Deterministic PR collector for floatboatai/Nexus-Editor.

Fetches (no LLM):
  - all PRs (pagination)
  - per PR: commits, reviews, issue comments, review comments, files,
    status + check-runs for the first-review head SHA and the final head SHA,
    and the base...sha diff for both snapshots.

Builds dual snapshots per AGENTS.md:
  - first-review snapshot: head SHA at the earliest review submitted_at
  - final snapshot: merged/closed head + final checks

Ground-truth (reviews + comments + decision) is written ONLY to
dataset/ground_truth/<pr>/ so the judge input never sees it.

Resumable: skips PRs already saved under dataset/raw/<pr>/done.json.
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
RAW = os.path.join(ROOT, "dataset", "raw")
GT = os.path.join(ROOT, "dataset", "ground_truth")
SNAP = os.path.join(ROOT, "dataset", "snapshots")
PR_LIST = os.path.join(RAW, "_pr_list.json")

REPO = "floatboatai/Nexus-Editor"
API = "https://api.github.com"
TOKEN_SCRIPT = os.path.join(ROOT, "..", "scripts", "myanyagent-credential-helper.cjs")


def get_token():
    import subprocess
    env_token = os.environ.get("GITHUB_TOKEN")
    if env_token:
        return env_token.strip()
    inp = "protocol=https\nhost=github.com\npath=anyingiit/My_Nexus-Editor_Workspace.git\n\n"
    out = subprocess.run(["node", TOKEN_SCRIPT, "get"], input=inp, capture_output=True,
                         text=True, check=True).stdout
    for line in out.splitlines():
        k, _, v = line.partition("=")
        if k == "password":
            return v.strip()
    raise RuntimeError("token not found from helper")


class GH:
    def __init__(self, token):
        self.token = token
        self.calls = 0

    def _request(self, url, page=None):
        if page:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}page={page}&per_page=100"
        self.calls += 1
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        for attempt in range(6):
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    headers = dict(resp.headers)
                    return json.loads(resp.read().decode("utf-8")), headers
            except urllib.error.HTTPError as e:
                if e.code == 403:
                    remaining = int(e.headers.get("X-RateLimit-Remaining", "0") or 0)
                    reset = int(e.headers.get("X-RateLimit-Reset", "0") or 0)
                    wait = max(reset - time.time(), 1)
                    print(f"[rate] 403, waiting {wait:.0f}s ...", flush=True)
                    time.sleep(min(wait, 300))
                    continue
                if e.code in (502, 503, 504):
                    time.sleep(5 * (attempt + 1))
                    continue
                # 204 / 409 etc.
                return json.loads(e.read().decode("utf-8", "replace")), dict(e.headers)
        raise RuntimeError(url)

    def get(self, url, page=None):
        data, _ = self._request(url, page=page)
        return data

    def get_raw(self, url, page=None):
        """Return (http_status, body_text) without raising on HTTP errors."""
        if page:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}page={page}&per_page=100"
        self.calls += 1
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")[:2000]
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")[:2000]
        except Exception as e:
            return "error", repr(e)[:2000]

    def all_pages(self, url):
        out, headers = self._request(url, page=1)
        if not isinstance(out, list):
            return out
        all_items = list(out)
        page = 1
        link = headers.get("Link") or headers.get("link") or ""
        while 'rel="next"' in link:
            page += 1
            try:
                data, headers = self._request(url, page=page)
            except RuntimeError:
                break
            if not data:
                break
            all_items.extend(data)
            link = headers.get("Link") or headers.get("link") or ""
        return all_items


def save(obj, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)


def iso_lt(a, b):
    return a < b


def main():
    token = get_token()
    gh = GH(token)

    os.makedirs(RAW, exist_ok=True)
    if os.path.exists(PR_LIST):
        prs = json.load(open(PR_LIST))
        print(f"Loaded {len(prs)} PRs from cached list.")
    else:
        prs = gh.all_pages(f"{API}/repos/{REPO}/pulls?state=all")
        save(prs, PR_LIST)
        print(f"Fetched {len(prs)} PRs.")

    # Process in chronological order (oldest first) for stable splitting.
    prs = sorted(prs, key=lambda p: (p["created_at"], p["number"]))
    print(f"Total PRs: {len(prs)} | closed: ", sum(1 for p in prs if p["state"] == "closed"))

    done = 0
    for pr in prs:
        n = pr["number"]
        done_marker = os.path.join(RAW, str(n), "done.json")
        if os.path.exists(done_marker):
            done += 1
            continue
        number = pr["number"]
        base = f"{API}/repos/{REPO}/pulls/{number}"
        try:
            detail = gh.get(base)
            if detail.get("number") is None:
                print(f"[skip {n}] not a PR issue (state={pr['state']})")
                save({"skipped": True, "pr": pr, "detail": detail}, done_marker)
                continue
            commits = gh.all_pages(f"{base}/commits")
            reviews = gh.all_pages(f"{base}/reviews")
            issue_comments = gh.all_pages(f"{API}/repos/{REPO}/issues/{number}/comments")
            review_comments = gh.all_pages(f"{base}/comments")
            files = gh.all_pages(f"{base}/files")

            # First-review point
            review_times = [r["submitted_at"] for r in reviews if r.get("submitted_at")]
            # also count review comments and issue comments submitted times as activity
            first_review_time = min(review_times) if review_times else None
            # try to compute a review-point from comments if no formal review
            if first_review_time is None:
                c_times = [c["created_at"] for c in review_comments] + \
                          [c["created_at"] for c in issue_comments]
                first_review_time = min(c_times) if c_times else detail["created_at"]

            # Find head SHA at first-review time from commit timestamps
            base_sha = detail["base"]["sha"]
            head_sha_final = detail["head"]["sha"]
            first_sha = None
            commits_at_first = []
            if commits:
                fc = list(commits)
                first_sha = fc[0]["sha"]  # merge-base head is first commit; safe fallback
                # pick last commit with committed date <= first_review_time
                for c in fc:
                    ctime = c["commit"]["committer"]["date"] or c["commit"]["author"]["date"]
                    if ctime <= first_review_time:
                        first_sha = c["sha"]
                        commits_at_first.append(c)
                    else:
                        break
            if first_sha is None:
                first_sha = head_sha_final

            # Diff / files changed at first-review point
            compare_first = None
            try:
                compare_first = gh.get(f"{API}/repos/{REPO}/compare/{base_sha}...{first_sha}")
            except Exception as e:
                print(f"[compare-first {n}] failed: {e}")

            # Checks at first-review SHA
            status_first = first_sha and gh.get(f"{API}/repos/{REPO}/commits/{first_sha}/status")
            checkruns_first = first_sha and gh.get(f"{API}/repos/{REPO}/commits/{first_sha}/check-runs")

            # Checks at final head SHA
            status_final = head_sha_final and gh.get(f"{API}/repos/{REPO}/commits/{head_sha_final}/status")
            checkruns_final = head_sha_final and gh.get(f"{API}/repos/{REPO}/commits/{head_sha_final}/check-runs")

            # final diff (only if merged or head differs)
            compare_final = None
            if head_sha_final != first_sha:
                try:
                    compare_final = gh.get(f"{API}/repos/{REPO}/compare/{base_sha}...{head_sha_final}")
                except Exception as e:
                    print(f"[compare-final {n}] failed: {e}")

            snapshot_first = {
                "pr_number": n,
                "title": detail["title"],
                "body": detail["body"],
                # NOTE: no final state/decision/merged/reviews here - judge input isolation
                "created_at": detail["created_at"],
                "draft": detail.get("draft", False),
                "base_sha": base_sha,
                "head_sha_at_first_review": first_sha,
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
                "status_first": _compact_status(status_first),
                "check_runs_first": _compact_checkruns(checkruns_first),
            }

            snapshot_final = {
                "pr_number": n,
                "title": detail["title"],
                "state": detail["state"],
                "merged": detail["merged"],
                "merged_at": detail["merged_at"],
                "closed_at": detail["closed_at"],
                "merged_by": (detail.get("merged_by") or {}).get("login"),
                "closed_by": None,  # filled for closed PRs via issue events if cheap; else omit
                "head_sha_final": head_sha_final,
                "merge_commit_sha": detail["merge_commit_sha"],
                "base_sha": base_sha,
                "commits": [
                    {"sha": c["sha"], "message": c["commit"]["message"],
                     "date": c["commit"]["committer"]["date"]} for c in commits
                ],
                "files": _compact_files(files),
                "compare_final": {
                    "status": compare_final.get("status") if compare_final else None,
                    "ahead_by": compare_final.get("ahead_by") if compare_final else None,
                    "behind_by": compare_final.get("behind_by") if compare_final else None,
                    "files": compare_final.get("files") if compare_final else None,
                } if compare_final else None,
                "status_final": _compact_status(status_final),
                "check_runs_final": _compact_checkruns(checkruns_final),
            }

            # Ground truth - ONLY in ground_truth dir (scorer read-only)
            ground_truth = {
                "pr_number": n,
                "title": detail["title"],
                "merged": detail["merged"],
                "state": detail["state"],
                "closed_at": detail["closed_at"],
                "merged_at": detail["merged_at"],
                "merged_by": (detail.get("merged_by") or {}).get("login"),
                "user": detail["user"]["login"],
                "reviews": [
                    {"user": r["user"]["login"], "state": r["state"],
                     "submitted_at": r["submitted_at"], "body": r["body"]} for r in reviews
                ],
                "review_comments": [
                    {"user": c["user"]["login"], "created_at": c["created_at"],
                     "body": c["body"], "path": c.get("path")} for c in review_comments
                ],
                "issue_comments": [
                    {"user": c["user"]["login"], "created_at": c["created_at"],
                     "body": c["body"]} for c in issue_comments
                ],
            }

            save(snapshot_first, os.path.join(SNAP, str(n), "first.json"))
            save(snapshot_final, os.path.join(SNAP, str(n), "final.json"))
            # NOTE: created_at MUST be written so split.py can do a real time
            # split even without _pr_list.json (historical bug: field was omitted
            # and every PR fell back to the 1970 sentinel).
            save({"merged": detail["merged"], "state": detail["state"],
                  "closed_at": detail.get("closed_at"), "has_reviews": bool(reviews),
                  "created_at": detail["created_at"]},
                 done_marker)
            save(ground_truth, os.path.join(GT, f"{n}.json"))
            done += 1
            print(f"[{n}] done (calls_total={gh.calls})", flush=True)
        except Exception as e:
            import traceback
            print(f"[{n}] ERROR: {e!r}", flush=True)
            traceback.print_exc(file=sys.stdout)
            save({"error": repr(e), "traceback": traceback.format_exc()},
                 os.path.join(RAW, str(n), "error.json"))
            continue

    print(f"Processing complete. done={done}, api_calls={gh.calls}")
    # write progress report (used by master to gauge activity)
    with open(os.path.join(RAW, "_progress.json"), "w") as fh:
        json.dump({"done": done, "total": len(prs), "api_calls": gh.calls,
                   "pids": os.getpid()}, fh)


def _compact_status(s):
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


def _compact_checkruns(cr):
    if not cr or not isinstance(cr, dict):
        return None
    return {
        "total_count": cr.get("total_count"),
        "check_runs": [
            {"name": x.get("name"), "status": x.get("status"),
             "conclusion": x.get("conclusion")} for x in cr.get("check_runs", [])
        ],
    }


def _compact_files(files):
    return [
        {"filename": f.get("filename"), "status": f.get("status"),
         "additions": f.get("additions"), "deletions": f.get("deletions"),
         "changes": f.get("changes")} for f in files
    ]


if __name__ == "__main__":
    main()
