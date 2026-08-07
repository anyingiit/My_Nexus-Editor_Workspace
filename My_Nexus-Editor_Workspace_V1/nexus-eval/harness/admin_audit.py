#!/usr/bin/env python3
"""Deterministic admin-permission / maintainer-evidence audit (AGENTS.md 1.1).

REQUIREMENT: the collection universe must be PRs "handled by a maintainer with
manage permission (approve/close/merge)" (AGENTS.md 1.1). Strict manage
permission CANNOT be proven via the GitHub REST API: the contributing GitHub
App token receives HTTP 403 from /repos/{repo}/collaborators and
/collaborators/{user}/permission. That fact is recorded as unverified, never
silently claimed.

This audit attributes EVIDENCE to the FINAL ACTOR ONLY (the person who
performed the last merge/close), fixing the earlier bug that took the strongest
author_association from ANY commenter in the PR. The per-actor evidence
hierarchy (weakest usable evidence that may legally include a candidate):

  A. proven_admin_permission          - permission API returned 200 (none in
                                        this repo: all 403).
  B. weak_maintainer_evidence         - the final actor has >=1 SURVIVING
                                        direct-push (non-PR) commit on `main`,
                                        mechanically filtered by
                                        harness/mine_direct_push.py. This is a
                                        WEAK maintainer signal (AGENTS.md 2.2
                                        'direct-push weak evidence') and is
                                        NEVER called strong proof of admin.
  C. final_actor_collaborator_association - the FINAL ACTOR's OWN
                                        reviews/comments carry GitHub
                                        author_association >= COLLABORATOR
                                        (OWNER/MEMBER/COLLABORATOR). Associations
                                        of ANY OTHER commenter are recorded
                                        separately as unrelated evidence and are
                                        NOT attributed to the final actor.
  D. distinct_actor_handled           - final actor != author but has neither B
                                        nor C. Closer/merger is a different user,
                                        which on GitHub implies some repo
                                        privilege, but their manage permission is
                                        UNVERIFIED (weak behavioural evidence).
  E. author_self_finalized_unverified - final actor == author with neither B nor
                                        C. Author closing/merging their own PR is
                                        NOT evidence of a maintainer handling it
                                        ("cannot count the author self-close as
                                        admin") -> excluded from the scored
                                        universe.

INCLUSION policy for the scored universe (used by split.py):
  include iff tier in {B, C, D}. Tier E and unbounded/unknown final actors are
  excluded with a documented per-candidate reason. This RESTORES author
  self-merge/self-close by actors with weak maintainer evidence (e.g. a
  repository-holder self-merging), while still excluding a plain PR author's
  self-close.

Writes dataset/evidence/admin_audit.json (per-candidate tiers + inclusion) and
updates dataset/evidence/protocol.json. No LLM. Offline-safe: the permission
probe is wrapped so the audit completes without network.
"""
import json
import os
import sys
import time

here = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(here, ".."))
EVID = os.path.join(ROOT, "dataset", "evidence")

REPO = "floatboatai/Nexus-Editor"
API = "https://api.github.com"
ASSOC_RANK = {"OWNER": 5, "MEMBER": 4, "COLLABORATOR": 3, "CONTRIBUTOR": 2,
              "FIRST_TIME_CONTRIBUTOR": 1, "NONE": 0}

INCLUDE_TIERS = {"weak_maintainer_evidence",
                 "final_actor_collaborator_association",
                 "distinct_actor_handled"}
ADMIN_ASSOC = ("OWNER", "MEMBER", "COLLABORATOR")


def probe_permission_api(gh):
    """Best-effort probe of the permission endpoints (records the 403)."""
    out = {"probed_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        code, _ = gh.get_raw(f"{API}/repos/{REPO}/collaborators")
        out["collaborators_endpoint_http"] = code
    except Exception as e:
        out["collaborators_endpoint_http"] = "error"
        out["collaborators_endpoint_error"] = repr(e)[:200]
    for user in ("ketd", "redreamality", "2391384896", "aoe"):
        try:
            code, body = gh.get_raw(
                f"{API}/repos/{REPO}/collaborators/{user}/permission")
            out[f"permission_{user}_http"] = code
            if code != 200:
                out[f"permission_{user}_body"] = body[:200]
        except Exception as e:
            out[f"permission_{user}_http"] = "error"
            out[f"permission_{user}_error"] = repr(e)[:200]
    codes = [out.get(k) for k in out if k.endswith("_http")]
    out["permission_provable"] = (
        any(isinstance(c, int) and c == 200 for c in codes))
    return out


def actor_own_association(rec, actor):
    """Highest author_association among the FINAL ACTOR's OWN reviews / issue
    comments. Other commenters' associations are ignored (they are irrelevant
    to the final actor's role)."""
    best = "NONE"
    for r in rec.get("reviews", []) or []:
        if (r.get("user") or r.get("login")) == actor:
            a = r.get("author_association")
            if ASSOC_RANK.get(a, 0) > ASSOC_RANK.get(best, 0):
                best = a
    for c in rec.get("issue_comments", []) or []:
        if (c.get("user") or c.get("login")) == actor:
            a = c.get("author_association")
            if ASSOC_RANK.get(a, 0) > ASSOC_RANK.get(best, 0):
                best = a
    return best


def max_any_association(rec):
    """Strongest association of ANY commenter (unrelated evidence only)."""
    best = "NONE"
    for r in rec.get("reviews", []) or []:
        a = r.get("author_association")
        if ASSOC_RANK.get(a, 0) > ASSOC_RANK.get(best, 0):
            best = a
    for c in rec.get("issue_comments", []) or []:
        a = c.get("author_association")
        if ASSOC_RANK.get(a, 0) > ASSOC_RANK.get(best, 0):
            best = a
    return best


def load_weak_evidence():
    """Weak maintainer actors from mechanically-filtered direct pushes."""
    p = os.path.join(EVID, "direct_push.json")
    if not os.path.exists(p):
        return {}
    d = json.load(open(p))
    return d.get("weak_maintainer_actor_evidence", {}) or {}


def load_reviews_full():
    """Complete per-PR review payloads WITH author_association (v7 audit)."""
    p = os.path.join(EVID, "reviews_full_v7.json")
    if not os.path.exists(p):
        return {}
    return json.load(open(p)).get("reviews", {})


def main():
    cand = json.load(open(os.path.join(EVID, "candidate_admin.json")))
    prog_path = os.path.join(EVID, "admin_evidence_progress.json")
    prog = json.load(open(prog_path))["prs"] if os.path.exists(prog_path) else {}
    weak = load_weak_evidence()
    weak_actors = set(weak.keys())
    weak_count = {k: int(v.get("total_surviving_direct_pushes", 0))
                  for k, v in weak.items()}
    reviews_full = load_reviews_full()

    probe = None
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from collect_prs import get_token, GH  # noqa
        gh = GH(get_token())
        probe = probe_permission_api(gh)
    except Exception as e:
        probe = {"probe_error": repr(e)[:200], "permission_provable": False}

    audit = {}
    for n_str, rec in cand.items():
        author = rec.get("author") or rec.get("guessed_author")
        fa = rec.get("merged_by") or rec.get("closed_by")
        if not fa:
            audit[n_str] = {
                "pr_number": rec["pr_number"], "state": rec.get("state"),
                "merged": rec.get("merged_at") is not None, "author": author,
                "final_actor": None, "evidence_tier": "no_final_actor",
                "include": False,
                "exclude_reason": "no final merge/close actor on record"}
            continue
        self_finalized = bool(author) and fa == author
        oa = actor_own_association(prog.get(n_str) or {}, fa)
        ua = max_any_association(prog.get(n_str) or {})
        own_collab_or_higher = ASSOC_RANK.get(oa, 0) >= ASSOC_RANK["COLLABORATOR"]
        has_weak = fa in weak_actors

        if has_weak:
            tier, include, reason = ("weak_maintainer_evidence", True,
                                     f"final actor '{fa}' has {weak_count.get(fa, 0)} "
                                     "surviving direct-push commit(s) -> weak "
                                     "maintainer evidence (not proof of admin)")
        elif own_collab_or_higher:
            tier, include, reason = (
                "final_actor_collaborator_association", True,
                f"final actor '{fa}' own author_association = {oa} "
                "(>= COLLABORATOR)")
        elif self_finalized:
            tier, include, reason = (
                "author_self_finalized_unverified", False,
                f"final action by the PR author '{fa}' with no maintainer "
                "evidence (no direct-push weak evidence, own association "
                f"{oa}); author self-close/merge does not prove an admin "
                "handling it (AGENTS.md 1.1)")
        else:
            tier, include, reason = (
                "distinct_actor_handled", True,
                f"final action by distinct actor '{fa}' (author '{author}'); "
                "the closer/merging actor differs from the author, but their "
                "manage permission is UNVERIFIED (no weak maintainer evidence, "
                f"own association {oa}) - weak behavioural evidence only")

        audit[n_str] = {
            "pr_number": rec["pr_number"],
            "state": rec.get("state"),
            "merged": rec.get("merged_at") is not None,
            "author": author,
            "final_actor": fa,
            "self_finalized": self_finalized,
            "final_actor_own_association": oa,
            "any_commenter_max_association": ua,
            "any_commenter_association_is_unrelated": (
                "unrelated: strongest association in the PR belongs to a "
                "commenter who is NOT the final actor; not attributed to the "
                "final actor" if ua != oa and oa in ("NONE", None) else ""),
            "weak_maintainer_evidence": has_weak,
            "weak_maintainer_direct_push_count": weak_count.get(fa, 0),
            "evidence_tier": tier,
            "include": include,
            "exclude_reason": reason,
            # Round 12 / v7: review-object-level admin-candidate evidence. A
            # formal review counts as admin evidence iff the REVIEW ACTOR's own
            # author_association is OWNER/MEMBER/COLLABORATOR OR they are a
            # direct-push weak maintainer. Any other commenter's association can
            # never substitute for the review/final actor.
            "review_admin_evidence": [
                {"user": r["user"], "state": r["state"],
                 "author_association": r.get("author_association"),
                 "submitted_at": r.get("submitted_at")}
                for r in (reviews_full.get(str(rec["pr_number"])) or [])
                if r.get("user") in weak_actors
                or r.get("author_association") in ADMIN_ASSOC
            ],
        }

    n_total = len(audit)
    n_inc = sum(1 for a in audit.values() if a["include"])
    n_exc = n_total - n_inc
    tier_counts = {}
    for a in audit.values():
        tier_counts[a["evidence_tier"]] = tier_counts.get(a["evidence_tier"], 0) + 1

    summary = {
        "n_finalized_candidates": n_total,
        "n_included": n_inc,
        "n_excluded": n_exc,
        "evidence_tier_counts": tier_counts,
        "excluded_prs": sorted(int(k) for k, v in audit.items() if not v["include"]),
        "weak_maintainer_evidence_actors": dict(sorted(weak_count.items(),
                                                       key=lambda x: -x[1])),
        "review_actor_association_observed": (
            "Round 12 / v7 - from dataset/evidence/reviews_full_v7.json: "
            "redreamality=COLLABORATOR (association channel), "
            "ketd=CONTRIBUTOR (association channel NOT >= COLLABORATOR; admitted "
            "only via the direct-push weak-maintainer channel), "
            "copilot-pull-request-reviewer[bot]=NONE (never an admin channel). "
            "Review-level association is recorded per candidate."),
        "admin_evidence_policy_v7": (
            "A formal review is ADMIN CANDIDATE EVIDENCE iff its REVIEW ACTOR's "
            "own author_association is OWNER/MEMBER/COLLABORATOR (the pre-recorded "
            "evidence level on the review object) OR the review actor is a "
            "direct-push weak maintainer. The author_association of any OTHER "
            "commenter can never substitute for the review/final actor. The "
            "permission API returns 403, so neither channel is a proof of manage "
            "permission - recorded, never called strong admin."),
        "permission_provable": bool(probe and probe.get("permission_provable")),
        "note": (
            "Manage permission is NOT provable via REST (App token 403) - "
            "recorded, never claimed. Evidence tiers attribute maintainer "
            "evidence to the FINAL ACTOR ONLY: weak_maintainer_evidence "
            "(direct-push weak evidence), final_actor_collaborator_association "
            "(the final actor's OWN association >= COLLABORATOR), or "
            "distinct_actor_handled (a non-author closer/merger, permission "
            "unverified). Author-self-finalized PRs are INCLUDED when the final "
            "actor has weak maintainer evidence (they are an evidenced "
            "maintainer self-merging/closing), and EXCLUDED when they are a "
            "plain PR author with no maintainer evidence. Weak evidence is "
            "reported as weak evidence, never as proof of admin permission."),
    }

    with open(os.path.join(EVID, "admin_audit.json"), "w") as fh:
        json.dump({"summary": summary, "probe": probe, "prs": audit},
                  fh, indent=2, ensure_ascii=False)

    # update protocol.json
    proto_path = os.path.join(EVID, "protocol.json")
    proto = json.load(open(proto_path)) if os.path.exists(proto_path) else {}
    proto["admin_permission_evidence"] = {
        "method": (
            "GitHub REST /repos/{repo}/collaborators/{user}/permission and "
            "/collaborators were re-attempted with the contributing GitHub App. "
            "They return HTTP 403 'Resource not accessible by integration' (also "
            "for ketd, redreamality, 2391384896, aoe). No actor's manage "
            "permission can be proven."),
        "permission_endpoint_accessible": bool(
            probe and isinstance(probe.get("permission_endpoint_http"), int)
            and probe["permission_endpoint_http"] == 200) if probe else False,
        "probe": probe,
        "evidence_level": (
            "per-actor evidence tiers (weak maintainer evidence / final-actor "
            "association / distinct-actor) - all non-author-zero unverified for "
            "manage permission; author-self-finalized WITHOUT maintainer "
            "evidence excluded"),
        "n_finalized_candidates": n_total,
        "n_included": n_inc,
        "n_excluded": n_exc,
        "n_with_proven_admin_permission": 0,
        "include_tiers": sorted(INCLUDE_TIERS),
        "policy": (
            "Scored universe = finalized PRs whose final actor carries "
            "maintainer evidence (weak direct-push evidence / final-actor "
            "COLLABORATOR+ association) OR is a distinct (non-author) actor "
            "(permission unverified). Author-self-finalized PRs are included "
            "when the author is an evidenced maintainer, and excluded otherwise. "
            "Manage permission remains unprovable (403); evidence tiers are "
            "recorded per candidate and never inflated to 'admin'."),
        "review_association_evidence_v7": (
            "Round 12: every formal review's own author_association is recorded "
            "(dataset/evidence/reviews_full_v7.json). A review is admin-"
            "candidate-evidence iff its REVIEW ACTOR's association is "
            "OWNER/MEMBER/COLLABORATOR OR they are a direct-push weak maintainer. "
            "Any other commenter's association never substitutes for the "
            "review/final actor. Observed: redreamality=COLLABORATOR, ketd="
            "CONTRIBUTOR (admin only via direct-push weak channel), "
            "copilot-pull-request-reviewer[bot]=NONE. Permission API 403 -> "
            "neither channel is a proven manage permission; never called strong "
            "admin (AGENTS.md 1.1)."),
    }
    with open(proto_path, "w") as fh:
        json.dump(proto, fh, indent=2, ensure_ascii=False)

    print("permission probe:", json.dumps(probe, indent=2))
    print("included:", n_inc, "excluded:", n_exc)
    print("tiers:", tier_counts)
    print("excluded prs:", summary["excluded_prs"])
    print("weak_maintainer_evidence actors:",
          {k: v for k, v in sorted(weak_count.items(), key=lambda x: -x[1])})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
