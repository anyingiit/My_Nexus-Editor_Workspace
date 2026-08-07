#!/usr/bin/env python3
"""Deterministic scorer + report card generator.

Metrics per AGENTS.md 3.6/3.7:
  - decision accuracy (approve vs reject per merged/closed ground truth)
  - reject-class recall (真实拒绝/关闭占比)
  - CI conclusion match
  - rubric coverage (recall) >= 50% by default for per-PR pass
  - precision (judge only counts non-fabricated reasons)
  - self-consistency C (optional, from repeated judge runs)

Usage: score.py <run_dir> [--min-coverage 0.5] [--split dev|test] [--json]
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
GT = os.path.join(ROOT, "dataset", "ground_truth")
RUBRIC_DIR = os.path.join(ROOT, "rubric")


def match_reason_to_rubric(reason, points):
    """Heuristic lexical match; returns matched rubric point ids."""
    rl = reason.lower()
    matched = []
    for p in points:
        pl = p["summary"].lower()
        # crude token overlap
        rtoks = {w for w in re.findall(r"[a-z0-9]+", rl) if len(w) > 2}
        ptoks = {w for w in re.findall(r"[a-z0-9]+", pl) if len(w) > 2}
        if not ptoks:
            continue
        inter = rtoks & ptoks
        if len(inter) >= max(1, (len(ptoks) + 1) // 3):
            matched.append(p["id"])
    return matched


def _llm_match(reason, points, n):
    """Optional LLM matcher for rubric coverage (flash, cached). Uses plain-text
    output (ids line) which avoids the model's json_object instability. Falls
    back to lexical matching on any failure."""
    import llm_client
    prompt = (
        f"Review reason for PR #{n}:\n>> {reason}\n\n"
        "Which of the following reviewer-concern points does this reason "
        "correspond to (semantic match, can overlap with 0..N)?\n"
    )
    for p in points:
        prompt += f"\n- id={p['id']}: {p['summary']}"
    prompt += "\nReply with ONLY a comma-separated list of matched ids, e.g. \"1,2\" (or \"\" if none)."
    import time as _time
    _time.sleep(1.0)  # gentle throttle to avoid provider burst instability
    try:
        text, _ = llm_client.complete(
            [{"role": "system", "content": "You map a review comment to concern IDs."},
             {"role": "user", "content": prompt}],
            max_tokens=600, tag=f"match-{n}")
    except Exception:
        _time.sleep(3.0)
        try:
            text, _ = llm_client.complete(
                [{"role": "system", "content": "You map a review comment to concern IDs."},
                 {"role": "user", "content": prompt}],
                max_tokens=600, tag=f"match-{n}-r")
        except Exception:
            return match_reason_to_rubric(reason, points)
    ids = [int(x) for x in re.findall(r"\d+", text)]
    return ids

USE_LLM_MATCH = os.environ.get("USE_LLM_MATCH", "0") == "1"


def evaluate_run(run_dir, split, min_coverage=0.5, verbose=True, return_rows=False):
    split_file = os.path.join(ROOT, "dataset", "split.json")
    split_data = json.load(open(split_file))
    prs = [p for p in split_data["prs"] if p["split"] == split]
    prs.sort(key=lambda p: p["number"])

    rows = []
    for pr in prs:
        n = pr["number"]
        gt = json.load(open(os.path.join(GT, f"{n}.json")))
        jp = os.path.join(run_dir, f"{n}.json")
        if not os.path.exists(jp):
            continue
        judge = json.load(open(jp))
        # Golden decision: deterministic, scorer-only. For finalized PRs it is
        # the final outcome; for open-with-admin-review PRs it is the earliest
        # admin decision-review state (see build_gt_decisions_v6.py). Fall back
        # to legacy merged-based inference only if a GT file lacks the field.
        golden = gt.get("golden_decision") or ("approve" if gt["merged"] else "reject")
        dec = (judge.get("decision") or "").lower()
        decision_ok = dec == golden
        ci = (judge.get("ci_result") or "").lower()
        # ground-truth CI: replayed checks; pass if all recorded green or unknown
        ci_ok = ci_truth(n) == ci

        rubric = json.load(open(os.path.join(RUBRIC_DIR, f"{n}.json")))
        points = rubric.get("points", [])
        reasons = judge.get("reasons", [])
        matched_ids = set()
        for r in reasons:
            if USE_LLM_MATCH and points:
                for i in _llm_match(r, points, n):
                    matched_ids.add(i)
            else:
                matched_ids.update(match_reason_to_rubric(r, points))
        n_matched = len(matched_ids)
        ne_points = max(len(points), 1)
        coverage = n_matched / ne_points if points else 1.0
        # precision
        if reasons:
            if USE_LLM_MATCH and points:
                total_matches = sum(1 for r in reasons if _llm_match(r, points, n))
            else:
                total_matches = sum(1 for r in reasons if match_reason_to_rubric(r, points))
            precision = total_matches / len(reasons)
        else:
            precision = 1.0 if not points else 0.0

        covered_pass = coverage >= min_coverage
        per_pr_pass = decision_ok and ci_ok and covered_pass

        rows.append({
            "pr": n, "golden": golden, "decision": dec, "decision_ok": decision_ok,
            "ci": ci, "ci_ok": ci_ok, "reasons": len(reasons),
            "rubric_points": len(points), "coverage": round(coverage, 3),
            "precision": round(precision, 3), "pass": per_pr_pass,
        })
        if verbose:
            print(f"PR {n}: golden={golden} judge={dec} dec_ok={decision_ok} "
                  f"ci_ok={ci_ok} coverage={coverage:.2f} precision={precision:.2f} "
                  f"pass={per_pr_pass}")
    summary = summarize(rows)
    if return_rows:
        return summary, rows
    return summary


def ci_truth(n):
    """Faithful replay conclusion from stored checks.

    Convention:
      - pass   : at least one completed success with nothing failing
      - fail   : any definitive failure conclusion
      - unknown: nothing conclusive yet (all pending/queued/skipped/absent)
    """
    snap = json.load(open(os.path.join(ROOT, "dataset", "snapshots", str(n), "first.json")))
    st = snap.get("status_first")
    cr = snap.get("check_runs_first")
    if not st and not cr:
        return "unknown"

    FAIL = {"failure", "cancelled", "timed_out", "action_required", "error", "failed"}
    OK = {"success"}

    had_fail = False
    had_ok = False
    for s in (st or {}).get("statuses") or []:
        state = s.get("state")
        if state in ("failure", "error"):
            had_fail = True
        if state == "success":
            had_ok = True
    combined = (st or {}).get("state")
    if combined == "success":
        had_ok = True
    if combined == "failure":
        had_fail = True
    for c in (cr or {}).get("check_runs") or []:
        concl = c.get("conclusion")
        if concl in FAIL:
            had_fail = True
        if concl in OK:
            had_ok = True
    if had_fail:
        return "fail"
    if had_ok:
        return "pass"
    return "unknown"


def default_run_dir(variant, split):
    return os.path.join(HERE, "state", f"judge_{variant.replace('/', '_')}", split)


def summarize(rows):
    n = len(rows)
    if not n:
        return None
    dec_ok = sum(1 for r in rows if r["decision_ok"])
    ci_ok = sum(1 for r in rows if r["ci_ok"])
    passes = sum(1 for r in rows if r["pass"])
    # reject-class recall
    rejects = [r for r in rows if r["golden"] == "reject"]
    neg = [r for r in rows if r["golden"] == "approve"]
    # both classes for a "reject-positive" framing: reject recall = among rejects, judge reject
    reject_recall = sum(1 for r in rejects if r["decision"] == "reject") / len(rejects) if rejects else None
    # also approve precision not used
    cov = [r["coverage"] for r in rows]
    prec = [r["precision"] for r in rows]
    return {
        "n": n,
        "decision_accuracy": round(dec_ok / n, 4),
        "ci_match": round(ci_ok / n, 4),
        "per_pr_pass_rate": round(passes / n, 4),
        "reject_class_recall": (round(reject_recall, 4) if reject_recall is not None else None),
        "n_rejects": len(rejects),
        "n_approves": len(neg),
        "coverage_mean": round(sum(cov) / n, 4),
        "precision_mean": round(sum(prec) / n, 4),
        "coverage_p50": round(sorted(cov)[n // 2], 4),
    }


def main():
    args = sys.argv[1:]
    run_dir = args[0] if args and not args[0].startswith("--") else None
    split = "dev"
    min_cov = 0.5
    out_json = None
    for a in args:
        if a.startswith("--min-coverage"):
            min_cov = float(args[args.index(a) + 1])
        elif a.startswith("--split"):
            split = args[args.index(a) + 1]
        elif a.startswith("--json"):
            out_json = args[args.index(a) + 1]
    if run_dir is None:
        # default: judge run dir state/judge_<variant>__<split>__<tag>
        variant = "ours"
        run_dir = os.path.join(HERE, "state", f"judge_{variant}__{split}__untagged")
    summary, rows = evaluate_run(run_dir, split, min_cov, verbose=True, return_rows=True)
    if summary:
        summary["rows"] = [
            {"pr": r["pr"], "decision": r["decision"], "golden": r["golden"],
             "decision_ok": r["decision_ok"], "ci_ok": r["ci_ok"], "pass": r["pass"],
             "coverage": r["coverage"], "precision": r["precision"]}
            for r in rows
        ]
    print("SUMMARY:", json.dumps(summary, indent=2))
    if out_json:
        os.makedirs(os.path.dirname(out_json) or ".", exist_ok=True)
        with open(out_json, "w") as fh:
            fh.write(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
