# Nexus-Editor PR Dataset

Collected deterministically from `floatboatai/Nexus-Editor` via the GitHub REST
API (no LLM involved in collection). Each PR is stored as two snapshots plus an
isolated ground-truth file.

## Collection

Run: `python3 harness/collect_prs.py` (resumable; skips completed PRs).
- Source: `https://api.github.com/repos/floatboatai/Nexus-Editor`
- Auth: GitHub App installation token via `scripts/myanyagent-credential-helper.cjs`
  (or `GITHUB_TOKEN` env var).
- All 213 PRs (`state=all`) collected; 110 finalized (merged or closed-without-merge).

## Layout

```
dataset/
  raw/<pr>/done.json         # collection marker (merged/closed/has_reviews)
  snapshots/<pr>/first.json  # FIRST-REVIEW SNAPSHOT (judge input, leak-free)
  snapshots/<pr>/final.json  # FINAL SNAPSHOT (rule-mining reference only)
  ground_truth/<pr>.json     # WITHHELD from judge; scorer-only gold
  evidence/                  # audit evidence (see below)
  split.json                 # train/dev/test time split (60/20/20)
  leak_check.json            # leak-check pass output (must stay empty)
  AGENTS_freeze.json         # frozen AGENTS+split hash for the formal test run
```

## Review data integrity (AGENTS.md 1.1 — data audit, Round 12 / v7)

`harness/audit_reviews_v6.py` (v7 evidence) re-fetched / re-used the **complete
formal-review payload of all 213 PRs** and saved **every review object with its
`author_association`, `user`, `state`, `submitted_at`, `commit_id`** to
`dataset/evidence/reviews_full_v7.json` (34 review objects across 28 PRs; PRs
with no review have an empty list).

A formal review counts as **admin-candidate evidence** iff its REVIEW ACTOR's
own `author_association` is `OWNER`/`MEMBER`/`COLLABORATOR` (the pre-recorded
evidence level on the review object) **OR** the review actor is a direct-push
weak maintainer. Any other commenter's association can NEVER substitute for the
review/final actor.

Observed associations (per review object):
- `redreamality` = `COLLABORATOR` — admin-candidate by BOTH association and
  direct-push weak evidence.
- `ketd` = `CONTRIBUTOR` — NOT admin by association; admitted only through the
  mechanically-filtered direct-push weak-maintainer channel (95 surviving
  direct pushes), recorded, never called proof of admin.
- `copilot-pull-request-reviewer[bot]` = `NONE` — never an admin channel.

The permission endpoints (`/collaborators`, `/collaborators/{user}/permission`)
still return HTTP 403 for the contributing App token — **manage permission is
NOT provable, so neither the association nor the weak-push evidence is ever
called "strong admin"** (AGENTS.md 1.1 is a documented MISS on its permission
dimension; evidence is recorded per candidate in
`evidence/admin_audit.json` + `evidence/protocol.json`).

Open PRs are admitted to the scored universe only when they have an **admin
DECISION formal review**; GT = state of the EARLIEST such review
(`APPROVED`→approve, `CHANGES_REQUESTED`/`DISMISSED`→reject). Their first
snapshot is pinned to that review's **exact `submitted_at` + `commit_id`**; a
snapshot that cannot be restored to that state is excluded (1.2). PR 15 was
re-pinned from a COMMENTED review head to the earliest admin DECISION review
commit. All exclusion reasons are recorded in
`evidence/review_universe_v7.json`.

## Admin-universe filter ("handled by a maintainer", AGENTS.md 1.1)

The GitHub REST endpoint
`/repos/{repo}/collaborators/{user}/permission` (and `/collaborators`) returns
HTTP 403 ("Resource not accessible by integration") for the contributing App
token. Therefore:
- **Author-self-finalized PRs are EXCLUDED** unless the author carries
  direct-push weak-maintainer evidence (Round 5 rule): a plain PR author's
  self-close/merge is NOT evidence of an admin handling it. 33 of the 110
  finalized candidates are excluded on this basis.
- For the remaining candidates the final actor's own association (from their
  own reviews/comments) and/or direct-push weak evidence is recorded; the
  strongest association of any OTHER commenter is recorded separately and never
  attributed to the final actor.
- No actor is provably OWNER/MEMBER via permission API (403). The best observed
  review-level association is COLLABORATOR (redreamality). Evidence tiers are
  recorded per candidate and never inflated to "admin".

## Direct-push (non-PR) commits

`evidence/direct_push.json` records the mechanical direct-push mining:
139 main commits -> 4 merge, 0 bot, 16 PR-associated, 6 version/release,
1 lockfile/generated-only removed -> **112 surviving direct-push commits** (weak
evidence). Actor counts: ketd 95, 2391384896 14, redreamality 2, aoe 1.

## Dual snapshots (per AGENTS.md 1.2)

**first.json** — the PR state at its FIRST review point:
- head SHA pinned per the audit above; commits `commits_at_first_review`,
  diff `compare_first_review` (base...head), and CI/checks (`status_first`,
  `check_runs_first`) fetched for that exact SHA.
- Stripped of all decision-bearing fields: no final `state`, `merged`,
  `closed_at`, reviews, or comments. This is the **only** allowed input to the
  judge.

**final.json** — the PR's closed/merged state (final head SHA, `merged`,
`merged_by`, final files/checks). Used only for rule mining; never fed to the
judge.

## Ground-truth isolation (per AGENTS.md 1.3)

`ground_truth/<pr>.json` contains the reviewer decisions and comments. The
judge and the harness's sub-agent never read this directory; only the scorer
does (via `score.py`). The leak check (`leak_check.json`) asserts `first.json`
carries no decision fields; it must pass (`leaks: []`) before any evaluation.
`score.py` is the only consumer of `ground_truth/`.

## Time split (per AGENTS.md 1.4)

`dataset/split.json` assigns each candidate (finalized PRs with an
evidence-gated final actor, plus open PRs with an admin decision review) to
`train` / `dev` / `test` by `created_at` order (60 / 20 / 20). Rule mining uses
train only; AGENTS.md iteration uses dev only; the test set runs exactly once
after the AGENTS.md is frozen.

Round 12 / v7 scored universe: **79 candidates** (train 47 / dev 15 / test 17),
golden distribution 12 approve / 67 reject; open-in-split = [15] train,
[84, 87] dev, [112, 116, 117, 119] test. The v7 association-gated audit
confirms the SAME candidate membership as Round 6/11 (only ketd and redreamality
hold decision reviews, and both were already recognised as admin-candidate
evidence); the change is the recorded evidence (per-review `author_association`)
and the re-pinned PR 15 snapshot.

| split | n | approve | reject | open (admin-review) | literal first-review |
|---|---|---|---|---|---|
| train | 47 | 5 | 42 | [15] | 17 |
| dev | 15 | 1 | 14 | [84, 87] | 2 |
| test | 17 | 5 | 12 | [112, 116, 117, 119] | 7 |
| excluded (open, no admin decision review) | 96 | — | — | — | — |
| excluded (self-finalized, 1.1) | 33 | — | — | — | — |
| excluded (unrecoverable 1st-review, 1.2) | 5 | — | — | — | — |

## Dataset freeze

The split, snapshots, and AGENTS.md are frozen for each formal evaluation
(see `AGENTS_freeze.json`). Do not modify `dataset/` after freezing; re-running
`split.py` must reproduce the same assignment.
