#!/usr/bin/env python3
"""Deterministic audit of ALL PRs' reviews / issue events (Round 12 / v7 evidence).

Root AGENTS.md 1.1 scope = every PR actually handled by an actor with manage
permission (approve / close / merge). We cannot prove permission (403, recorded
in admin_audit). TWO independent WEAK-to-STRONG channels are used to recognise
an "admin" formal review (data-integrity fix for Round 11 / v6 audit):

  1. The review object's OWN ``author_association`` (OWNER / MEMBER /
     COLLABORATOR) — the GitHub-issued per-author association recorded ON THE
     REVIEW itself (the "pre-recorded evidence level").
  2. The established WEAK maintainer evidence (surviving, mechanically-filtered
     direct-push commits: ketd 95, 2391384896 14, redreamality 2, aoe 1).

A review counts as ADMIN CANDIDATE EVIDENCE iff (1) or (2) holds for the REVIEW
ACTOR (the user who submitted the review). The `author_association` of any
OTHER commenter can never substitute for the review/final actor — a PR comment
by a COLLABORATOR does not make a different reviewer "admin" (AGENTS.md 1.1).

Observed (Round 12, v7): redreamality = COLLABORATOR on every review
(association channel); ketd = CONTRIBUTOR on every review (NOT admin by
association → admitted only via the direct-push weak-maintainer channel, which
is recorded, never called strong proof). copilot-pull-request-reviewer[bot] =
NONE → never an admin decision channel.

Outputs (deterministic, no LLM):
  - dataset/evidence/reviews_full_v7.json    : per-PR review payloads WITH
    author_association (user, state, submitted_at, commit_id, body_prefix) —
    saved, fetched with `--fetch`.
  - dataset/evidence/review_universe_v7.json : per-candidate universe + GT +
    exclusion reasons (EVERY excluded/open-excluded PR carries a reason).
  - dataset/evidence/v7_universe_summary.json: counts / split by class.

GT decision rules (pre-fixed — unchanged from Round 6 semantics, now on the
association-gated admin evidence):
  * Finalized PR (merged/closed): merged -> approve; closed-without-merge ->
    reject (final outcome).
  * OPEN PR with at least one ADMIN DECISION formal review (APPROVED /
    CHANGES_REQUESTED / DISMISSED by an admin-candidate-evidenced actor): GT =
    state of the EARLIEST such decision-state admin review -> APPROVED=approve,
    CHANGES_REQUESTED/DISMISSED=reject. COMMENT-only reviews are activity, not
    a decision.
  * OPEN PR without any admin decision review: NOT in the scored universe
    ("开放且没有 admin decision formal review 不纳入 literal 集") — excluded
    with reason.
  * Author-self-finalized: only included when the author holds direct-push weak
    maintainer evidence (Round 5 rule, kept).

First-review snapshot basis (AGENTS.md 1.2, literal):
  * For any candidate whose PR has an admin formal review, the first-review
    snapshot MUST be anchored at the FIRST ADMIN DECISION review submitted_at +
    that review's commit_id (exact reviewed SHA). Candidates whose exact
    SHA/diff/checks cannot be restored are excluded and recorded (1.2).
  * Finalized PRs WITHOUT any formal review keep the documented
    first-activity-proxy (head_sha_report) — never disguised as literal.
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
EVID = os.path.join(ROOT, "dataset", "evidence")
GTD = os.path.join(ROOT, "dataset", "ground_truth")
SNAP = os.path.join(ROOT, "dataset", "snapshots")
PR_LIST = os.path.join(ROOT, "dataset", "raw", "_pr_list.json")

REPO = "floatboatai/Nexus-Editor"
API = "https://api.github.com"
DECISION_STATES = ("APPROVED", "CHANGES_REQUESTED", "DISMISSED")
ALL_REVIEW_STATES = DECISION_STATES + ("COMMENTED", "COMMENT", "PENDING")
ADMIN_ASSOC = ("OWNER", "MEMBER", "COLLABORATOR")

REVIEWS_FULL = os.path.join(EVID, "reviews_full_v7.json")
UNIVERSE_OUT = os.path.join(EVID, "review_universe_v7.json")
SUMMARY_OUT = os.path.join(EVID, "v7_universe_summary.json")


def load_weak_actors():
    p = os.path.join(EVID, "direct_push.json")
    d = json.load(open(p))
    ev = d.get("weak_maintainer_actor_evidence", {}) or {}
    actors = {}
    for k, v in ev.items():
        actors[k] = int(v.get("total_surviving_direct_pushes", 0))
    return actors


def fetch_reviews_v7(gh):
    """Network step: fetch full review payloads (with author_association) for
    every PR and persist them to reviews_full_v7.json. Deterministic save;
    the audit itself reads the saved file (offline)."""
    prs = json.load(open(PR_LIST))
    out = {}
    errors = []
    for p in sorted(prs, key=lambda x: x["number"]):
        n = p["number"]
        try:
            revs = gh.all_pages(f"{API}/repos/{REPO}/pulls/{n}/reviews")
        except Exception as e:
            errors.append([n, repr(e)])
            out[str(n)] = []
            continue
        keep = []
        for r in revs:
            u = r.get("user") or {}
            keep.append({
                "id": r.get("id"),
                "user": u.get("login"),
                "author_association": r.get("author_association"),
                "state": r.get("state"),
                "submitted_at": r.get("submitted_at"),
                "commit_id": r.get("commit_id"),
                "body_prefix": (r.get("body") or "")[:80],
            })
        out[str(n)] = keep
        print(f"[reviews {n}] {len(keep)} review objects", flush=True)
    doc = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_prs": len(prs),
        "reviews": out,
        "errors": errors,
    }
    with open(REVIEWS_FULL, "w") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
    total = sum(len(v) for v in out.values())
    print(f"saved {REVIEWS_FULL}: {total} review objects; "
          f"{sum(1 for v in out.values() if v)} PRs with any review")
    return doc


def admin_evidence_basis(review, weak_actors):
    """Return None if the review's actor is NOT an admin-candidate evidenced
    actor, else a dict describing the evidence (association / direct-push
    weak / both). Other commenters' associations are never considered here."""
    user = review.get("user")
    if not user:
        return None
    assoc = review.get("author_association")
    assoc_ok = assoc in ADMIN_ASSOC
    weak_ok = user in weak_actors
    if not (assoc_ok or weak_ok):
        return None
    return {
        "user": user,
        "author_association": assoc,
        "admin_by_association": assoc_ok,
        "admin_by_direct_push_weak": weak_ok,
        "direct_push_count": weak_actors.get(user, 0),
        "basis": ("both" if (assoc_ok and weak_ok)
                  else ("association" if assoc_ok else "direct_push_weak")),
    }


def main(fetch=False):
    weak_actors = load_weak_actors()
    print("weak maintainer actors:", weak_actors)

    if fetch or not os.path.exists(REVIEWS_FULL):
        sys.path.insert(0, HERE)
        from collect_prs import get_token, GH  # noqa
        gh = GH(get_token() or os.environ.get("GITHUB_TOKEN"))
        fetch_reviews_v7(gh)

    reviews_full = json.load(open(REVIEWS_FULL))
    reviews_by_pr = {str(k): v for k, v in reviews_full["reviews"].items()}

    prs_list = {p["number"]: p for p in json.load(open(PR_LIST))}

    # ---- build the universe ----
    universe = {}
    for n, p in sorted(prs_list.items()):
        gtp = os.path.join(GTD, f"{n}.json")
        if not os.path.exists(gtp):
            continue
        gt = json.load(open(gtp))
        author = gt.get("user")
        state = p.get("state")
        merged = bool(gt.get("merged"))

        revs = reviews_by_pr.get(str(n), [])
        # admin candidate evidence: association channel OR direct-push weak
        admin_evidence = []
        for r in revs:
            eb = admin_evidence_basis(r, weak_actors)
            if eb is not None:
                admin_evidence.append({**r, "evidence": eb})
        decision_revs = [r for r in admin_evidence if r.get("state") in DECISION_STATES]
        decision_revs.sort(key=lambda r: (r.get("submitted_at") or "", r.get("commit_id") or ""))
        first_admin_rev = None
        if admin_evidence:
            sorted_admin = sorted(admin_evidence,
                                  key=lambda r: (r.get("submitted_at") or "", r.get("commit_id") or ""))
            first_admin_rev = sorted_admin[0]
        first_decision = decision_revs[0] if decision_revs else None

        rec = {
            "pr_number": n,
            "state": state,
            "author": author,
            "merged": merged,
            "n_reviews_total": len(revs),
            "n_admin_evidence_reviews": len(admin_evidence),
            "n_admin_decision_reviews": len(decision_revs),
            "admin_review_actors": sorted({r["user"] for r in admin_evidence}),
            "first_admin_review": first_admin_rev,
            "first_admin_decision_review": first_decision,
        }

        if not (p.get("merged") or p.get("closed_at")):  # OPEN
            if first_decision is None:
                rec.update({
                    "open": True, "include": False,
                    "exclude_reason": (
                        "open PR without an admin decision formal review (no "
                        "APPROVED/CHANGES_REQUESTED/DISMISSED review by an "
                        "admin-candidate-evidenced actor: association "
                        "OWNER/MEMBER/COLLABORATOR or direct-push weak) - not in "
                        "the literal set"),
                })
            else:
                golden = "approve" if first_decision["state"] == "APPROVED" else "reject"
                rec.update({
                    "open": True, "include": True,
                    "basis": "admin_formal_review",
                    "gt_basis": "earliest admin decision review",
                    "golden": golden,
                    "gt_admin_review": {
                        "user": first_decision["user"],
                        "author_association": first_decision["author_association"],
                        "state": first_decision["state"],
                        "submitted_at": first_decision["submitted_at"],
                        "commit_id": first_decision["commit_id"],
                        "evidence": first_decision["evidence"],
                    },
                    "exclude_reason": None,
                })
        else:  # FINALIZED
            golden = "approve" if merged else "reject"
            rec.update({
                "open": False, "include": True,
                "basis": "final_actor_evidence",
                "gt_basis": "final outcome",
                "golden": golden,
                "exclude_reason": None,
            })
        rec["weak_actor"] = author in weak_actors
        rec["weak_actor_direct_pushes"] = weak_actors.get(author, 0)
        universe[str(n)] = rec

    # ---- finalized: author-self-finalized + evidence-tier exclusion (1.1) ----
    admin_audit_path = os.path.join(EVID, "admin_audit.json")
    admin_audit = {}
    if os.path.exists(admin_audit_path):
        admin_audit = json.load(open(admin_audit_path)).get("prs", {})
    for n, rec in universe.items():
        if rec.get("open"):
            continue
        tier = (admin_audit.get(str(n)) or {}).get("evidence_tier", "unknown")
        rec["evidence_tier"] = tier
        if tier == "author_self_finalized_unverified":
            rec["include"] = False
            rec["exclude_reason"] = (
                "author self-finalized without direct-push weak maintainer "
                "evidence (1.1: a plain author's self-close/merge is not "
                "evidence of an admin handling it)")
        elif tier in ("no_final_actor", "unknown"):
            rec["include"] = False
            rec["exclude_reason"] = f"finalized PR with evidence tier '{tier}'"

    # ---- 1.2: first-review snapshot recoverability for every ADMIN
    # formal-review candidate (open + finalized-with-admin-review) ----
    evid = json.load(open(os.path.join(EVID, "head_sha_report.json"))) \
        .get("prs", {}) if os.path.exists(os.path.join(EVID, "head_sha_report.json")) else {}
    for n, rec in universe.items():
        if not rec.get("include") or not rec.get("open"):
            continue
        first_dec = rec.get("first_admin_decision_review")
        first_snap = os.path.join(SNAP, str(rec["pr_number"]), "first.json")
        if not os.path.exists(first_snap):
            rec["include"] = False
            rec["exclude_reason"] = ("1.2: open-admin candidate has no first "
                                     "snapshot (unrecoverable)")
            continue
        head = json.load(open(first_snap)).get("head_sha_at_first_review")
        if first_dec and head != first_dec.get("commit_id"):
            rec["include"] = False
            rec["exclude_reason"] = (
                "1.2: first snapshot could not be restored to the exact "
                f"earliest admin decision review commit {first_dec.get('commit_id')} "
                f"(stored head {head}) - unrecoverable")
        else:
            rec["snapshot_literal"] = True

    included = {n: r for n, r in universe.items() if r["include"]}
    excluded = {n: r for n, r in universe.items() if not r["include"]}
    open_included = {n: r for n, r in included.items() if r.get("open")}
    open_excluded = {n: r for n, r in excluded.items() if r.get("open")}

    def gt_counts(d):
        return {"approve": sum(1 for r in d.values() if r["golden"] == "approve"),
                "reject": sum(1 for r in d.values() if r["golden"] == "reject")}

    summary = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "round": "r12-v7",
        "n_total_prs": len(prs_list),
        "n_included": len(included),
        "n_excluded": len(excluded),
        "included_gt_counts": gt_counts(included),
        "n_open_included": len(open_included),
        "n_open_excluded": len(open_excluded),
        "open_included_list": sorted(int(k) for k in open_included),
        "open_excluded_list": sorted(int(k) for k in open_excluded),
        "open_included_gt": gt_counts(open_included),
        "weak_maintainer_actors": weak_actors,
        "review_actor_association_observed": {
            "redreamality": "COLLABORATOR (association channel -> admin candidate evidence)",
            "ketd": "CONTRIBUTOR (NOT admin by association; admitted only via direct-push weak channel)",
            "copilot-pull-request-reviewer[bot]": "NONE (never an admin decision channel)",
        },
        "admin_evidence_policy": (
            "A formal review is ADMIN CANDIDATE EVIDENCE iff its REVIEW ACTOR's own "
            "author_association is OWNER/MEMBER/COLLABORATOR OR the review actor is a "
            "direct-push weak maintainer. Any other commenter's author_association can "
            "never substitute for the review/final actor. Permission API is 403 -> this "
            "is evidence, never a claim of proven admin."),
        "note": ("Universe = finalized PRs handled by an evidence-gated final "
                 "actor (Round5 rule) UNION open PRs with an admin decision "
                 "formal review (GT = earliest admin decision-review state; "
                 "1.2 snapshot pinned to that review's exact commit_id). Open "
                 "PRs without an admin decision review are excluded with reason. "
                 "Weak maintainer evidence / association evidence are recorded, "
                 "never inflated to proven admin."),
    }
    print(json.dumps(summary, indent=2))

    with open(UNIVERSE_OUT, "w") as fh:
        json.dump({"summary": summary, "universe": universe}, fh,
                  indent=2, ensure_ascii=False)
    with open(SUMMARY_OUT, "w") as fh:
        json.dump(summary, fh, indent=2, ensure_ascii=False)
    print("wrote", UNIVERSE_OUT)
    return 0


if __name__ == "__main__":
    fetch = "--fetch" in sys.argv
    raise SystemExit(main(fetch=fetch))
