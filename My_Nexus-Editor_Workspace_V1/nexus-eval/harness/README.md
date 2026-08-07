# Nexus-Editor Contribution AGENTS.md — Evaluation Harness

Goal (per root `AGENTS.md`): build a layered `AGENTS.md` for contributing to the
Nexus-Editor repository, evaluated by a time-split (train 60 / dev 20 / test 20)
set of PRs on metrics: decision accuracy >= 85%, reject-class recall >= 70%,
reason coverage >= 50% (per-PR pass), and significantly better than 2 baselines.

## Layout

```
nexus-eval/
  harness/
    collect_prs.py        # deterministic GitHub PR collection (dual snapshots)
    fix_first_review.py   # first-review SHA recovery + 1.2 audit (API, no LLM)
    admin_audit.py        # 1.1 admin-permission audit + self-finalized filter (no LLM)
    mine_direct_push.py   # direct-push (non-PR) commit mining for weak evidence (no LLM)
    split.py              # time split + isolation + leak check (excludes self-finalized + unrecoverable)
    rubric.py             # per-PR rubric generation (LLM, cached)
    rules.py              # rule mining from train set (LLM, cached) -> agents/
    freeze.py             # freeze AGENTS + split + judge model/provider/reasoning (hash manifest)
    judge.py              # judge evaluation driver (LLM, cached)
    score.py              # deterministic scorer (no LLM; emits per-PR rows)
    bootstrap.py          # deterministic paired bootstrap (no LLM)
    consistency.py        # self-consistency ceiling C (LLM, tag-bound state)
    llm_client.py         # LLM client (opencode-go primary, openrouter fallback)
    state/                # tag-bound judge/consistency state
    llmcache/             # disk cache for LLM prompts (never committed)
  dataset/
    raw/  snapshots/  ground_truth/  evidence/  split.json  leak_check.json
  rubric/<pr>.json
  agents/                 # layered AGENTS.md + sub-docs (incl. 09-supplements)
  reports/                # report card + cost ledger + score files
```

## State isolation (reproducibility)

Judge output lives under `state/judge_<variant>__<split>__<tag>/`; consistency
under `state/consistency_<variant>_<split>__<tag>/`. The path is bound to
`(variant, split, tag)` and each dir carries a `_manifest.json` with the exact
AGENTS sha. Stale outputs (a different AGENTS sha) are automatically removed so
results are never silently reused across old rounds or between split sets.

## Pipeline order

1. `collect_prs.py` — fetch all PRs + reviews + comments + commits + files +
   check runs, build first-review & final snapshots (deterministic, resumable).
2. `fix_first_review.py` — audit every finalized candidate's first-review SHA:
   recover force-pushed review `commit_id`s (still resolvable in the object
   DB), mark genuinely unrecoverable snapshots (no code at the first-review
   proxy time) as `unrecoverable`, and record per-PR `first_review_basis`
   (formal_review | comment | created_at) + admin-permission evidence into
   `dataset/evidence/protocol.json`. No LLM involved.
3. `admin_audit.py` — 1.1 audit (evidence-gated): tag every finalized candidate
   with its FINAL ACTOR (merged_by/closed_by), the final actor's OWN
   author_association (their own reviews/comments only; other commenters'
   associations are marked unrelated), and an evidence tier:
   `weak_maintainer_evidence` (final actor has surviving mechanically-filtered
   direct pushes, from `mine_direct_push.py`) /
   `final_actor_collaborator_association` /
   `distinct_actor_handled` / `author_self_finalized_unverified` (excluded);
   re-probe the permission endpoints (403 recorded); write
   `dataset/evidence/admin_audit.json`. No LLM.
3b. `mine_direct_push.py` — MUST run before `admin_audit.py`. Deterministically
   classifies the local `Nexus-Editor` `main` git history (merge -> bot ->
   PR-associated -> version/release -> lockfile/generated -> survivors) and
   writes `dataset/evidence/main_history.json` + `direct_push.json` with the
   per-actor weak-maintainer evidence table. No LLM, no GitHub API.
4. `split.py` — assign train/dev/test by time (60/20/20) over the VALID
   evidence-gated finalized candidates (author-self-finalized WITHOUT maintainer
   evidence are excluded into `split.json.excluded_self_finalized`; author
   self-finalized BY an evidence-gated maintainer are INCLUDED); candidates
   with an unrecoverable first-review snapshot go into
   `excluded_unrecoverable_first_review`; every PR records `snapshot_type`
   (literal_first_review vs first_activity_proxy); write leak check.
   Deterministic.
5. `rubric.py dev test` — decompose per-PR gold review comments into discrete
   points (scorer-only).
6. `rules.py --force-mine` — mine contribution rules from train set (LLM,
   cached) -> sub-modules in `agents/` (weak evidence, gated by repo rules);
   `mine_direct_push.py` supplies the direct-push weak-evidence section.
7. `judge.py <variant> <split> [limit] <tag>` + `score.py <judge_dir> --split
   <s>` — run judge over the split for a variant; produce metrics + per-PR rows.
8. `bootstrap.py --split <s> --tag <t>` — deterministic paired bootstrap from
   the per-PR score files (no LLM; reproducible p-values).
9. `consistency.py ours <sample> <runs> dev <tag>` — measure self-consistency C
   (fresh calls, cache=False) with the SAME judge model+provider.
10. `freeze.py` — record SHA-256 of every `agents/*.md`, the combined injected
    `ours` text, the split hash, and the judge model+provider into
    `dataset/AGENTS_freeze.json`.
11. Run `judge.py` + `score.py` for the TEST split ONCE (post-freeze, tag=final).
12. `reports/make_final_card.py --tag final` + `reports/report.py` — assemble
    the report card (provenance incl. model+provider, C, variant table,
    baseline deltas + p-values, clause-level status, limitations, cost).

## Dev-only iteration → freeze → one-shot test

- Rule mining may only use **train**. AGENTS.md iteration may only use **dev**
  results (per AGENTS.md 1.4 / 3.8). The test set must never be read during
  iteration.
- After dev converges: `python3 harness/freeze.py`, then run the test split
  exactly once (`run_test.sh final`). The AGENTS freeze hash binds the report
  card to the frozen content.
- `evidence/protocol.json` records: first-review-time proxy definition + basis
  counts, admin-permission evidence level (unverified — the contributing GitHub
  App token gets HTTP 403 on `/collaborators/*/permission`), and the candidate
  filter rules (finalized PRs; unrecoverable first-review snapshots excluded).

## Environment & credentials

- GitHub: App token via `scripts/myanyagent-credential-helper.cjs` or
  `GITHUB_TOKEN` (read-only; does NOT expose permission endpoints -> admin
  permission is a recorded limitation; see `dataset/evidence/admin_audit.json`).
- LLM: keys from `~/.local/share/opencode/auth.json` (`opencode-go` /
  `openrouter`) or env. Provider priority (root AGENTS.md 4.2):
  **opencode-go -> openrouter**; the default provider is opencode-go
  (model `deepseek-v4-flash`), overridable with `LLM_PROVIDER=openrouter`
  (model `deepseek/deepseek-v4-flash-0731`). An evaluation round must keep ONE
  provider+model for all variants and dev/test, and consistency C is measured
  with that same judge model+provider. Any opencode-go -> openrouter downgrade
  is logged to `reports/cost_ledger.jsonl` with its reason.
- Offline: `harness/make_fixtures.py` + `harness/score.py` exercise the scorer
  with deterministic placeholder judge outputs (no LLM, no GitHub).
