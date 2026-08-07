#!/usr/bin/env python3
"""Per-PR rubric generation.

For each PR, decompose the gold review comments (reviews + review comments +
admin issue comments) into a discrete list of issue points (the rubric).
Stored at rubric/<pr>.json. LLM-based, cached. Uses default flash model.

The rubric is scorer-only reference; judge never sees it.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm_client import complete_json  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
GT = os.path.join(ROOT, "dataset", "ground_truth")
RUBRIC_DIR = os.path.join(ROOT, "rubric")


SYSTEM = (
    "You are a meticulous code-review analyst. Given a pull request's original "
    "reviewer comments, decompose them into a list of discrete, self-contained "
    "issue points. Each point captures ONE concrete concern, requirement, "
    "question, or requested change raised by the original reviewers. "
    "Do NOT fabricate concerns that reviewers did not raise. "
    "Return ONLY a JSON object with key \"points\" being an array of objects "
    "{\"id\": int, \"summary\": str} (2-15 words each). "
    "Include at least one point if there is any substantive review feedback."
)


def build_prompt(pr):
    parts = [f"# PR #{pr['pr_number']}: {pr['title']}"]
    reviews = pr.get("reviews", [])
    rcomments = pr.get("review_comments", [])
    icomments = pr.get("issue_comments", [])
    if reviews:
        parts.append("\n## Formal reviews")
        for r in reviews:
            if r.get("body"):
                parts.append(f"- [{r['user']}] {r['state']}: {r['body']}")
    if rcomments:
        parts.append("\n## Inline review comments")
        for c in rcomments:
            parts.append(f"- {c['user']} on {c.get('path','')}: {c['body']}")
    if icomments:
        parts.append("\n## Issue comments")
        for c in icomments:
            parts.append(f"- {c['user']}: {c['body']}")
    parts.append("\nDecompose the above feedback into discrete issue points.")
    return "\n".join(parts)


def main(force=False, splits=("dev", "test")):
    os.makedirs(RUBRIC_DIR, exist_ok=True)
    split_data = json.load(open(os.path.join(ROOT, "dataset", "split.json")))
    wanted = {int(p["number"]): p["split"] for p in split_data["prs"]}
    compiled = []
    for f in sorted(os.listdir(GT)):
        if not f.endswith(".json"):
            continue
        n = int(f[:-5])
        if n not in wanted or wanted[n] not in splits:
            continue
        out_path = os.path.join(RUBRIC_DIR, f"{n}.json")
        if os.path.exists(out_path) and not force:
            compiled.append(json.load(open(out_path)))
            continue
        pr = json.load(open(os.path.join(GT, f)))
        # only make rubric for PRs that have reviews/comments
        has_feedback = bool(pr["reviews"] or pr["review_comments"] or pr["issue_comments"])
        if not has_feedback:
            doc = {"pr_number": pr["pr_number"], "points": []}
            json.dump(doc, open(out_path, "w"), indent=2)
            compiled.append(doc)
            continue
        try:
            obj, _ = complete_json(
                [{"role": "system", "content": SYSTEM},
                 {"role": "user", "content": build_prompt(pr)}],
                max_tokens=2000, tag=f"rubric-{n}", extra_json_fix=True)
        except Exception as e:
            print(f"[rubric {n}] error: {e}")
            doc = {"pr_number": pr["pr_number"], "points": [], "error": repr(e)}
            json.dump(doc, open(out_path, "w"), indent=2)
            compiled.append(doc)
            continue
        points = obj.get("points", []) if isinstance(obj, dict) else []
        doc = {"pr_number": pr["pr_number"], "points": points}
        json.dump(doc, open(out_path, "w"), indent=2, ensure_ascii=False)
        compiled.append(doc)
        print(f"[rubric {n}] {len(points)} points")
    total_points = sum(len(d.get("points", [])) for d in compiled)
    print(f"rubrics done: {len(compiled)}, total points: {total_points}")
    return 0


if __name__ == "__main__":
    import sys as _sys
    args = _sys.argv[1:]
    splits = tuple(args) if args else ("dev", "test")
    _sys.exit(main(splits=splits))
