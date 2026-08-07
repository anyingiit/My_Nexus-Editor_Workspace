#!/usr/bin/env python3
"""Deterministic direct-push (non-PR) commit mining for weak-evidence rules
(AGENTS.md 2.2) and WEAK maintainer-identity evidence (AGENTS.md 1.1).

SOURCE OF TRUTH: the local git checkout of floatboatai/Nexus-Editor (branch
`main`), NOT the GitHub API. Local history is fully deterministic, offline and
reproducible (`git log` + `git show`).

For every main commit the mechanical filter (order fixed by AGENTS.md 2.2):
  1. drop merge commits (>=2 parents OR subject starts with 'Merge ...')
  2. drop bot commits (author login/name ends '[bot]', known bot logins/emails)
  3. drop PR-associated commits (message references an existing PR number, e.g.
     squash-style '(#NN)' trailer or a '#NN' mention of a real PR)
  4. drop version bumps / release chores (subject matches release|version|bump)
  5. drop lockfile / generated-file-only commits (changed files subset of
     lockfile/generated sets)
The SURVIVORS are direct-push commits to `main` -> a WEAK evidence class only
(AGENTS.md 2.2: direct-push weak evidence; never overrides higher-priority
evidence).

Additionally this script builds an ACTOR evidence table:
  weak_maintainer_evidence[actor] = list of surviving direct-push commits by
  that actor (plus counts). This is the ONLY legitimate evidence basis for
  claiming an actor is a (weakly-evidenced) maintainer; it is NEVER asserted to
  prove 'admin' manage permission (which is unprovable; see admin_audit.py).

Identity resolution (commit name/email -> GitHub login) is deterministic:
  - GitHub noreply emails of the form {id}+{login}@users.noreply.github.com
    resolve to {login} authoritatively.
  - A small curated remap table covers identities verified against GitHub user
    profiles (redreamality, 2391384896/zhenyuliu). Everything else falls back
    to the commit author name and is marked `login_confidence=low`.

Writes:
  - dataset/evidence/main_history.json   full per-commit classification + audit
  - dataset/evidence/direct_push.json    per-commit direct-push rows + actor table
No LLM. No GitHub API calls.
"""
import json
import os
import re
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
EVID = os.path.join(ROOT, "dataset", "evidence")
PR_LIST = os.path.join(ROOT, "dataset", "raw", "_pr_list.json")
REPO = os.path.normpath(os.path.join(ROOT, "..", "Nexus-Editor"))

# --- mechanical filter regexes / constants (AGENTS.md 2.2 order) ---
VERSION_RE = re.compile(
    r"^\s*(release|version|bump|chore\s*\(release\)|publish|chore:\s*release)"
    r"[\s:#(\-]", re.I)
# a release marker is a LEADING version token ("0.0.4"/"v0.0.12"), a tagged
# "(vX.Y.Z)" trailer, or a release/chore sentence that names a version number.
# A mid-sentence "upgrade @codemirror/view 6.36->6.41" is a dependency fix, NOT
# a release chore.
VERSION_SUB_RE = re.compile(
    r"^\s*v?\d+\.\d+(\.\d+)?\b|\(\s*v?\d+\.\d+|release[^\n]*\d+\.\d+|\d+\.\d+[^\n]*release",
    re.I)
PR_TRAILER_RE = re.compile(r"\(#(\d{1,5})\)\s*$")   # squash-style merge trailer
PR_ANY_RE = re.compile(r"#(\d{1,5})")               # any #NN mention
MERGE_PREFIX_RE = re.compile(r"^merge\s+(pull request|branch|remote-tracking)", re.I)
BOT_LOGINS = {
    "dependabot", "renovate", "github-actions", "cla-assistant", "greenkeeper",
    "codacy", "snyk", "pre-commit-ci", "goreleaserbot",
}
BOT_EMAIL_DOMAINS = {"dependabot[bot]", "renovate[bot]", "github-actions[bot]",
                     "cla-assistant[bot]", "bot@github.com", "noreply@github.com"}
LOCKGEN_PATTERNS = ("pnpm-lock.yaml", "package-lock.json", "yarn.lock",
                    "npm-shrinkwrap.json", "dist/", "dist-electron/", "release/",
                    ".turbo/", "coverage/")
GENERATED_RE = re.compile(r"\.(min\.(js|css)|map|d\.ts)$")

# curated author (name,email) -> GitHub login, verified against GitHub user
# profiles where the commit email is NOT the GitHub noreply address.
CURATED_LOGIN = {
    ("remy", "redreamality@gmail.com"): ("redreamality", "github-user-id 3147776, name 'Redreamality'"),
    ("振宇", "zhenyulius@163.com"): ("2391384896", "github user 2391384896 name 'zhenyuliu', bio zhenyulius@163.com"),
    ("zhenyuliu", "56373988+2391384896@users.noreply.github.com"): ("2391384896", "noreply id 56373988 -> login 2391384896"),
    ("aoe", "aoe@aoedeMac-mini.local"): ("aoe", "commit name only; weak/unverified identity"),
    ("麦当", "borisdunk@sina.com"): ("maidangzhu", "PR #68 author login maidangzhu"),
    ("Wangchangcheng", "1209974887@qq.com"): ("qq1209974887", "PR #50 author login qq1209974887"),
    ("liuzhx", "minimilkfish@outlook.com"): ("MiniMilkfish", "PR #29 author login MiniMilkfish"),
    ("ehe123456", "caine211345@gmail.com"): ("caine-21", "PR #21 author login caine-21"),
}
NOREPLY_RE = re.compile(r"^\d+\+(.+?)@users\.noreply\.github\.com$")


def resolve_login(name, email):
    """Deterministic identity resolution; returns (login, confidence, note)."""
    e = (email or "").strip().lower()
    n = (name or "").strip()
    m = NOREPLY_RE.match(e)
    if m:
        return m.group(1), "high", "noreply-email"
    key = (n, e)
    if key in CURATED_LOGIN:
        login, note = CURATED_LOGIN[key]
        return login, "medium", note
    # fall back to the commit author name if it is path-safe and not a Chinese/
    # personal name (explicit unknown marker); otherwise low-confidence login.
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", n) and n not in ("root", "unknown"):
        return n, "low", "author-name-fallback"
    return "unknown", "low", "cannot-resolve-identity"


def git(args, cwd=None):
    return subprocess.run(["git", "-C", REPO if cwd is None else cwd] + args,
                          capture_output=True, text=True, check=True).stdout


def commit_meta():
    """Yield (sha, parents, an, ae, ce, ad, cd, subject, message) for main."""
    out = git(["log", "main", "--format=%H%x1f%P%x1f%an%x1f%ae%x1f%ce%x1f%ad%x1f%cd%x1f%s%x1f%b%x1e",
               "--date=iso-strict"])
    for rec in out.split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        parts = rec.split("\x1f")
        if len(parts) < 9:
            continue
        (sha, parents, an, ae, ce, ad, cd, subject, body) = parts[:9]
        yield {"sha": sha, "parents": (parents.split() if parents.strip() else []),
               "an": an, "ae": ae, "ce": ce, "date": ad, "cd": cd,
               "subject": subject, "message": (subject + "\n" + body).strip()}


def commit_files(sha):
    """Changed file paths for a non-merge commit (deterministic, local)."""
    try:
        out = git(["show", "--name-only", "--format=", sha])
    except subprocess.CalledProcessError:
        return []
    return [l for l in out.splitlines() if l.strip()]


def classify(commits, pr_numbers):
    rows = {"merge": [], "bot": [], "pr_associated": [], "version_release": [],
            "lockfile_gen_only": [], "direct_push": []}
    for c in commits:
        n_parents = len(c["parents"])
        subj = c["subject"]
        login, conf, note = resolve_login(c["an"], c["ae"])
        entry = {
            "sha": c["sha"], "short_sha": c["sha"][:12], "date": c["date"],
            "author_name": c["an"], "author_email": c["ae"],
            "login": login, "login_confidence": conf, "login_note": note,
            "subject": subj[:200], "n_parents": n_parents,
        }
        # 1. merge
        if n_parents >= 2 or MERGE_PREFIX_RE.match(subj):
            rows["merge"].append(entry)
            continue
        # 2. bot
        lower_login = login.lower()
        if (login.lower().endswith("[bot]") or lower_login in BOT_LOGINS
                or (c["ae"] or "").lower() in BOT_EMAIL_DOMAINS):
            rows["bot"].append(entry)
            continue
        # 3. PR-associated: squash trailer '(#NN)' or '#NN' mention of a real PR
        m_trail = PR_TRAILER_RE.search(subj)
        if m_trail and int(m_trail.group(1)) in pr_numbers:
            rows["pr_associated"].append({**entry, "reason": "squash-trailer"})
            continue
        mentioned = {int(x) for x in PR_ANY_RE.findall(c["message"])}
        if mentioned & pr_numbers:
            rows["pr_associated"].append({**entry, "reason": "mentions-PR"})
            continue
        # 4. version / release chore
        if VERSION_RE.match(subj) or VERSION_SUB_RE.search(subj):
            rows["version_release"].append(entry)
            continue
        # 5. lockfile / generated-only
        fnames = commit_files(c["sha"])
        entry["files"] = fnames
        entry["n_files"] = len(fnames)
        if fnames and all(
                any(k in fn for k in LOCKGEN_PATTERNS) or bool(GENERATED_RE.search(fn))
                for fn in fnames):
            rows["lockfile_gen_only"].append(entry)
            continue
        if not fnames:
            rows["lockfile_gen_only"].append({**entry, "note": "no file delta"})
            continue
        rows["direct_push"].append(entry)
    return rows


def actor_table(rows):
    by_login = {}
    for e in rows["direct_push"]:
        k = e["login"]
        rec = by_login.setdefault(k, {"commits": [], "count": 0})
        rec["commits"].append({"sha": e["sha"][:12], "date": e["date"],
                               "subject": e["subject"]})
        rec["count"] += 1
    # sort by count desc then login
    out = {}
    for k in sorted(by_login, key=lambda x: -by_login[x]["count"]):
        out[k] = {"total_surviving_direct_pushes": by_login[k]["count"],
                  "commits": by_login[k]["commits"]}
    return out


def main():
    pr_numbers = {int(p["number"]) for p in json.load(open(PR_LIST))}
    commits = list(commit_meta())
    rows = classify(commits, pr_numbers)
    summary = {k: len(v) for k, v in rows.items() if isinstance(v, list)}
    summary["total_main_commits"] = len(commits)
    actors = actor_table(rows)
    result = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "local git checkout of floatboatai/Nexus-Editor branch main "
                  "(offline, deterministic)",
        "method": (
            "mechanical filter in AGENTS.md 2.2 order: merge(>=2 parents or "
            "'Merge ...' prefix) -> bot -> PR-associated('(#NN)' squash trailer "
            "or any '#NN' mention of an existing PR) -> version/release -> "
            "lockfile/generated-only; survivors are direct-push WEAK evidence."),
        "summary": summary,
        "rows": rows,
        "weak_maintainer_actor_evidence": actors,
    }
    os.makedirs(EVID, exist_ok=True)
    with open(os.path.join(EVID, "main_history.json"), "w") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)
    with open(os.path.join(EVID, "direct_push.json"), "w") as fh:
        json.dump({
            "generated_at_utc": result["generated_at_utc"],
            "source": result["source"],
            "method": result["method"],
            "summary": summary,
            "rows": rows,
            "weak_maintainer_actor_evidence": actors,
        }, fh, indent=2, ensure_ascii=False)

    print("total_main_commits:", summary["total_main_commits"])
    print("summary:", summary)
    print("\nDirect-push (weak evidence) by actor:")
    for login, a in actors.items():
        print(f"  {login}: {a['total_surviving_direct_pushes']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
