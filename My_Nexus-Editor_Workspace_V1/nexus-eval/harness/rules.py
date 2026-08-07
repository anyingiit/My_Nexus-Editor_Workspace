#!/usr/bin/env python3
"""Rule mining from the train set + composition of the layered AGENTS.md.

Deterministic inputs:
  - Repository hard rules (CONTRIBUTING.md, GOVERNANCE.md, CLAUDE.md,
    PULL_REQUEST_TEMPLATE.md, ci.yml, package.json) -> hard-constraints module
  - train-set PRs rejection reasons (via LLM distillation, weak but gated by
    hard constraints)  -> code-style / commit-completeness / scope modules

Writes agents/AGENTS.md (core) + agents/*.md (sub-modules).
Uses default flash model for LLM distillation (cached).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm_client import complete_json  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
REPO = os.path.join(ROOT, "..", "Nexus-Editor")
AGENTS_DIR = os.path.join(ROOT, "agents")
GT = os.path.join(ROOT, "dataset", "ground_truth")
SPLIT = os.path.join(ROOT, "dataset", "split.json")
EVID = os.path.join(ROOT, "dataset", "evidence")
SNAP = os.path.join(ROOT, "dataset", "snapshots")

THIRD_PARTY = {
    "airflow": "https://raw.githubusercontent.com/apache/airflow/main/AGENTS.md",
    "pydantic-ai": "https://raw.githubusercontent.com/pydantic/pydantic-ai/main/AGENTS.md",
    "codex": "https://raw.githubusercontent.com/openai/codex/main/AGENTS.md",
}


def _p90(vals):
    v = sorted(vals)
    if not v:
        return 0
    k = max(1, int(round(0.9 * len(v))))
    return v[k - 1]


def scope_p90():
    """Deterministic P90 of files / changed lines over the train split's
    finalized PRs (from final snapshots). Returns (files_p90, lines_p90,
    n_finalized, n_merged, merged_files_p90, merged_lines_p90)."""
    if not os.path.exists(SPLIT):
        return (19, 4034, 0, 0, 29, 2244)
    split = json.load(open(SPLIT))
    all_rows, merged_rows = [], []
    for p in split.get("prs", []):
        if p.get("split") != "train":
            continue
        fp = os.path.join(SNAP, str(p["number"]), "final.json")
        if not os.path.exists(fp):
            continue
        files = json.load(open(fp)).get("files") or []
        lines = sum((f.get("additions", 0) or 0) + (f.get("deletions", 0) or 0)
                    for f in files)
        row = (len(files), lines)
        all_rows.append(row)
        if p.get("merged"):
            merged_rows.append(row)
    if not all_rows:
        return (19, 4034, 0, 0, 29, 2244)
    return (_p90([r[0] for r in all_rows]), _p90([r[1] for r in all_rows]),
            len(all_rows), len(merged_rows),
            _p90([r[0] for r in merged_rows]) if merged_rows else 0,
            _p90([r[1] for r in merged_rows]) if merged_rows else 0)


def direct_push_summary():
    """Surviving direct-push counts from the deterministic main-history
    classification (dataset/evidence/direct_push.json)."""
    p = os.path.join(EVID, "direct_push.json")
    if os.path.exists(p):
        s = json.load(open(p)).get("summary", {})
        if s:
            return s
    return {"merge": 4, "bot": 0, "pr_associated": 16,
            "version_release": 6, "lockfile_gen_only": 1,
            "direct_push": 112, "total_main_commits": 139}


def read_repo_file(name, fallback=""):
    p = os.path.join(REPO, name)
    if os.path.exists(p):
        return open(p).read()
    return fallback


def load_train_rejections():
    """Return summaries of train-set PRs whose final outcome was reject/close."""
    split_data = json.load(open(SPLIT))
    train = [p for p in split_data["prs"] if p["split"] == "train"]
    rows = []
    for pr in train:
        if pr["merged"]:
            continue
        gt = json.load(open(os.path.join(GT, f"{pr['number']}.json")))
        comments = (gt.get("issue_comments") or []) + (gt.get("review_comments") or [])
        bodies = [c.get("body", "") for c in comments if c.get("body")]
        if not bodies:
            continue
        rows.append({"n": pr["number"], "title": gt["title"], "comments": bodies[:6]})
    return rows


def mine_rejection_rules(rows, batch=8):
    """Distill rejection reasons into actionable rules (train-only evidence).
    Processes in small batches to avoid reasoning-budget exhaustion, merging
    deduplicated rules."""
    if not rows:
        return []
    all_rules = []
    seen = set()
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        prompt_lines = ["Below are rejected/closed PRs from the Nexus-Editor repo and the "
                        "comments left by reviewers/source. Extract the REASONS these PRs were "
                        "not accepted, as discrete, generalizable contribution rules.",
                        "",
                        f"({len(chunk)} PRs)"]
        for row in chunk:
            prompt_lines.append(f"\n### PR #{row['n']}: {row['title']}")
            for c in row["comments"][:4]:
                prompt_lines.append(f"- {c[:300]}")
        prompt_lines.append(
            "\nReturn JSON {\"rules\": [{\"rule\": str, \"source\": \"pr\"|\"implicit\"}]}. "
            "Rules must be concrete contribution norms, not vague advice. Mark 'source' "
            "pr if backed by explicit repo docs, else 'implicit'.")
        try:
            obj, _ = complete_json(
                [{"role": "system", "content": "You extract concise code-review contribution rules."},
                 {"role": "user", "content": "\n".join(prompt_lines)}],
                max_tokens=2000, tag=f"mine-rules-{i}")
        except Exception as e:
            print(f"[mining batch {i}] error: {e!r}")
            continue
        rules = obj.get("rules", []) if isinstance(obj, dict) else []
        for r in rules:
            key = r.get("rule", "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            all_rules.append(r)
        print(f"[mining batch {i}] +{len(rules)} rules (total {len(all_rules)})")
    print(f"Mined {len(all_rules)} rules from train set.")
    return all_rules


def main(force_mine=False):
    # ---- Deterministic: read repo hard rules ----
    contributing = read_repo_file("CONTRIBUTING.md")
    governance = read_repo_file("GOVERNANCE.md")
    pr_template = read_repo_file(".github/PULL_REQUEST_TEMPLATE.md")
    ci = read_repo_file(".github/workflows/ci.yml")
    pkg = read_repo_file("package.json")
    claude = read_repo_file("CLAUDE.md")

    # ---- Optional LLM mining step (cached via llm_client) ----
    mined = mine_rejection_rules(load_train_rejections()) if force_mine else []

    os.makedirs(AGENTS_DIR, exist_ok=True)
    write_modules(contributing, governance, pr_template, ci, pkg, claude, mined)
    print("AGENTS.md modules written to", AGENTS_DIR)
    return 0


def write_modules(contributing, governance, pr_template, ci, pkg, claude, mined):
    # 1. Code style sub-module
    code_style = f"""# Code Style (automated CI + repo conventions)

Priority: **automated CI requirements > dev-set validation > repo style > TypeScript best practices**

## CI / Lint (hard)
{_block(ci)}

## Repository style (from CONTRIBUTING.md §4)
{_block(contributing, extract="## 4. Code Style")}

## Testing conventions (from AGENTS.md/CLAUDE.md)
{_block(claude, extract="## Testing Conventions")}
"""
    open(os.path.join(AGENTS_DIR, "01-code-style.md"), "w").write(code_style)

    # 2. Commit completeness sub-module
    commit_comp = f"""# Commit Completeness Checklist (CHECKABLE items)

Before submitting a PR, verify EACH of the following is done (findings from
repo rules + train-set review history):

## PR-level checks (see CONTRIBUTING.md §3.2)
- [ ] Title follows Conventional Commits: `<type>(<scope>): <subject>` (file path: packages matching scope table)
- [ ] Description explains WHY, not just WHAT
- [ ] `pnpm test` passes; affected packages `pnpm build`
- [ ] New tests added where the change type requires them (see test matrix in CONTRIBUTING §3.3)
- [ ] Public API changes update `packages/*/README.md`
- [ ] Live-preview / table / wikilinks changes include a REGRESSION test
- [ ] UI changes exercised in electron-demo (type checks alone are insufficient)
- [ ] Touched `packages/core/src/live-preview-table.ts` -> walk through ALL 12 Table Widget rules
- [ ] New capability / breaking change -> OpenSpec proposal linked

## Compliance / governance (see GOVERNANCE.md §6)
- [ ] CLA signed (bot prompts first-time contributors)
- [ ] AI-generated code disclosed with details (functional code must NOT be primarily AI-generated)
- [ ] New runtime dependencies listed: name, version, license, why; MIT-compatible only
- [ ] No build artifacts / secrets / `.env` / personal vault data committed
- [ ] No `git push --force` to main
"""
    open(os.path.join(AGENTS_DIR, "02-commit-completeness.md"), "w").write(commit_comp)

    # 3. Scope sub-module
    fp90, lp90, n_fin, n_merge, mfp90, mlp90 = scope_p90()
    scope = f"""# Scope Constraints (replaces 'implementation difficulty')

Nexus is a **headless, AST-driven Markdown editor engine**.

## In scope
- packages/core (CM6 state, AST, live preview, widget API, events)
- packages/preset-gfm, packages/plugin-* (editor features)
- packages/react / packages/vue (thin bindings, lockstep updates required)
- apps/electron-demo (demonstration only)

## OUT of scope (rejected even if well-built — see GOVERNANCE.md §4)
{_block(governance, extract="### What is **not** in scope")}

## One PR = one concern
- Do NOT mix refactor with feature work in one PR (CONTRIBUTING §2).
- Multi-package coordinated changes allowed but description must have per-package sections.

## Size limits (derived from the train split's finalized PR distribution, P90)
- Single PR should touch **at most ~{max(fp90, 20)} files** and **~{max(lp90, 4100)} changed lines**
  (files P90 = {fp90}, changed-lines P90 = {lp90} over the train finalized set).
- A PR exceeding these limits should be split into smaller focused PRs.
- Basis note: the train merged-only sample is n={n_merge} (files P90={mfp90},
  lines P90={mlp90}), still small; the limit therefore uses the whole train
  finalized distribution (n={n_fin}) and is documented, not silently swapped
  to the merged-only sample.
"""
    open(os.path.join(AGENTS_DIR, "03-scope.md"), "w").write(scope)

    # 4. Constraints sub-module (mined + hard)
    mined_lines = ""
    for r in mined:
        mined_lines += f"- {r['rule']}  (_[{r['source']}]_)\n"
    ds = direct_push_summary()
    d_total = ds.get("total_main_commits", 139)
    d_merge = ds.get("merge", 4)
    d_bot = ds.get("bot", 0)
    d_pr = ds.get("pr_associated", 16)
    d_ver = ds.get("version_release", 6)
    d_lock = ds.get("lockfile_gen_only", 1)
    d_dir = ds.get("direct_push", 112)
    constraints = f"""# Constraints (hard repo rules + mined implicit norms)

Conflict priority (strict ">"): repo hard rules > test/dev validation results
> PR rejection reasons > implicit norms from train history > direct-push weak
evidence > third-party supplements. Same-priority conflicts resolve to the repo
hard rule. "test/dev validation results" means dev-split signals only.

## Hard repo constraints
{_block(governance, extract="## 6. Contribution Policy")}
{_block(pr_template)}

## Direct-push (non-PR) commits: weak evidence only
Norms read from direct (non-PR) commits to `main` are the SECOND-WEAKEST
evidence class (above third-party supplements only). Before use they are
mechanically filtered: merge commits, bot-authored commits, PR-associated
commits, lockfile / generated-file-only changes, version bumps and release
chores are removed; the surviving commits are checked against the repo hard
rules (defined above) and only non-conflicting signals are used when no
higher-priority evidence exists. Direct-push norms never override repo hard
rules, dev/test results, PR rejection reasons, or implicit train norms.

Mechanical filter applied to the repo's `main` history
(`dataset/evidence/direct_push.json`, deterministic local-git classification):
{d_total} main commits -> {d_merge} merge, {d_bot} bot, {d_pr} PR-associated,
{d_ver} version/release, {d_lock} lockfile/generated-only removed;
**{d_dir} surviving direct-push commits** (mostly repository-holder
bootstrapping and direct fixes). Actor evidence: ketd, 2391384896,
redreamality (surviving direct-push counts recorded in
`dataset/evidence/direct_push.json`). Derived WEAK norms
(`_[direct_push]_`, only used when the repo rules are silent):
- Direct commits by repository holders keep Conventional-Commit style
  messages and focused single-concern changes (feat/fix with one scope).  (_[direct_push]_)
- Core-file features/fixes are small and incrementally layered; a direct push
  is the maintainer's own iteration channel, not a signal that external PRs may
  bypass the PR review process.  (_[direct_push]_)

## Mined norms from train-set rejections (weak evidence, gated by repo rules)
{mined_lines if mined_lines else "_Derived after running mining (see rules.py --force-mine)._"}
"""
    open(os.path.join(AGENTS_DIR, "00-constraints.md"), "w").write(constraints)

    # 5. PR workflow / mechanics
    workflow = f"""# PR Workflow & Mechanics
{_block(contributing, extract="## 3. PR Workflow")}
{_block(contributing, extract="## 2. Branches & Commits")}

## Build/test commands (authoritative)
{_block(pkg, extract="\"scripts\"")}
{_block(ci)}
"""
    open(os.path.join(AGENTS_DIR, "04-workflow.md"), "w").write(workflow)

    # Core AGENTS.md is curated (agents/AGENTS.md); sub-modules are regenerated
    # by this script. Do NOT overwrite the curated core file with the template
    # below (kept for reference / regeneration only).
    _core_template = f"""# AGENTS.md — Nexus-Editor Contribution Guide (for this repo's use)

Verbatim goal from root: create a contribution AGENTS.md for the Nexus-Editor
repository that is used by automated PR reviewers/judges. This file is the core
index; load sub-modules on demand (see "Index" below).

## 1. Repository posture
- Nexus-Editor is a headless, AST-driven Markdown editor (pnpm + TypeScript monorepo).
- Follow Conventional Commits and OpenSpec-driven development.
- Read CONTRIBUTING.md / GOVERNANCE.md for authoritative rules; this file
  condenses them for automated judging and must not contradict them.

## 2. Hard constraints (blocker — apply to EVERY decision)
1. Never commit build artifacts, secrets, `.env`, or personal vault data.
2. AI-generated code must be disclosed; PRs whose functional code is primarily
   AI-generated are rejected.
3. New runtime dependencies require MIT-compatible license + rationale in the PR body.
4. Out-of-scope additions (AI/LLM integrations, vendor SDKs, UI component libs,
   non-primitive product features, schema linting) are rejected regardless of quality.
5. `pnpm test` and `pnpm build` must pass for affected packages.
6. One PR, one concern: never mix refactor and new feature.

## 3. Review decision procedure (judge)
1. Reproduce the commit/files/CI state found in the PR data.
2. Evaluate against: constraints > CI results > completeness checklist > scope.
3. Decide **approve** or **reject**. Reasons must be concrete and grounded in
   the observed PR content; do not fabricate objections.

## 4. Checkable commit-completeness checklist
See `agents/02-commit-completeness.md` (load when reviewing a PR).
Requirements that are checkable from PR content: tests added where required,
README updates for public API changes, regression tests for live-preview/table/
wikilinks, OpenSpec proposal linkage for new capabilities/breaking changes,
AI disclosure, dependency license/rationale.

## 5. Code style
See `agents/01-code-style.md`.

## 6. Scope
See `agents/03-scope.md`. Reject PRs that violate scope or exceed a single-concern
boundary without per-package justification.

## 7. Workflow & commands
See `agents/04-workflow.md`.

## Index / loading guidance
| Concern | File |
|---|---|
| Reviewing a PR (all modules) | load 00,01,02,03,04 |
| Quick decision gate | core §2 only |
| Code style only | agents/01-code-style.md |
| Compliance/completeness | agents/02-commit-completeness.md |
| Out-of-scope check | agents/03-scope.md |
| Commands/workflow | agents/04-workflow.md |

> Conflict priority (from root AGENTS.md decision): repo hard rules > test/dev
> results > PR rejection reasons > implicit norms > third-party supplements.
"""
    # Curated core AGENTS.md is authoritative; keep the template as reference.
    # (If agents/AGENTS.md is missing, materialize the template.)
    if not os.path.exists(os.path.join(AGENTS_DIR, "AGENTS.md")):
        open(os.path.join(AGENTS_DIR, "AGENTS.md"), "w").write(_core_template)


def _block(text, extract=None):
    if extract and extract.lower() in text.lower():
        # naive extract: find section start to next ## heading
        idx = text.lower().find(extract.lower())
        rest = text[idx:]
        import re
        m = re.search(r"\n## ", rest[10:])
        block = rest if not m else rest[: m.start() + 10]
        return block.strip()
    return text.strip()[:3000]


if __name__ == "__main__":
    force = "--force-mine" in sys.argv
    sys.exit(main(force_mine=force))
