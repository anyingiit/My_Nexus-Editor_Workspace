# Nexus-Editor Contribution AGENTS.md + Evaluation (nexus-eval)

This workspace builds and evaluates a **layered, for-our-use-only AGENTS.md**
for contributing to the [Nexus-Editor](https://github.com/floatboatai/Nexus-Editor)
repository, following the goal and criteria in the root `AGENTS.md`.

## Deliverables

| Item | Path |
|---|---|
| Layered AGENTS.md (core) | `agents/AGENTS.md` (100–300 lines, strict `>` priorities) |
| Sub-modules (on-demand) | `agents/00-constraints.md` … `04-workflow.md`, `09-supplements.md` |
| PR dataset (dual snapshot + GT isolation + first-review audit + admin/identity evidence) | `dataset/` |
| Time split (72 valid candidates: 43/14/15; evidence-gated 1.1 + unrecoverable-1.2 exclusions) | `dataset/split.json` |
| Protocol / audit evidence | `dataset/evidence/protocol.json`, `admin_audit.json`, `main_history.json`, `direct_push.json`, `actor_identity.json`, `head_sha_report.json`, `candidate_admin.json` |
| Per-PR rubric (judge-reasons ground truth) | `rubric/<pr>.json` |
| Test harness (collect/fix/admin/rule/rubric/judge/score/bootstrap/consistency/freeze) | `harness/` |
| Report card | `reports/report_card.md` (+ `.json`) |
| Cost ledger | `reports/cost_ledger.jsonl` (+ provider/downgrade events) |

## Layers of the generated AGENTS.md

- `agents/AGENTS.md` — core index + hard constraints + decision procedure
  (100–300 lines, standalone).
- `agents/00-constraints.md` — repo hard rules + **mined norms** from the
  train set's rejection history (weak evidence, gated by the §9 conflict
  priority; `_[pr]_` / `_[implicit]_` markers).
- `agents/01-code-style.md` — CI/lint + repo style + testing conventions.
- `agents/02-commit-completeness.md` — checkable checklist.
- `agents/03-scope.md` — in-scope vs rejected features.
- `agents/04-workflow.md` — PR workflow, commit/OpenSpec mechanics, commands.
- `agents/09-supplements.md` — third-party supplements (Airflow / pydantic-ai /
  Codex): sources, adopted non-conflicting items, and items NOT adopted because
  they conflict with Nexus hard rules.

Conflict priority (strict `>`): `repo hard rules > test/dev results > PR
rejection reasons > implicit norms > direct-push weak evidence > third-party
supplements`.

## Evaluation protocol summary

1. For each PR, the judge receives ONLY the **first-review snapshot** (pinned
   head SHA, commits + files + replayed CI at the first-review point) plus an
   AGENTS.md variant. Final outcome, reviews and comments are never injected.
2. The judge classifies CI faithfully and decides `approve`/`reject` with
   concrete reasons.
3. The scorer checks decision agreement, CI agreement, and rubric coverage
   (per AGENTS.md 3.4; per-PR pass = CI ∧ decision ∧ coverage ≥ 50%).
4. Three variants are compared on the same harness and sets:
   `empty` / `baseline` (repo CONTRIBUTING+GOVERNANCE+ci.yml) / `ours`, with
   paired bootstrap p-values recomputed deterministically from per-PR scores
   (`harness/bootstrap.py`).
5. Consistency ceiling C is measured by running the judge 5× on a dev sample
   with a fixed model (tag-bound state).

## Quick start

```bash
# 1) Collect (GitHub token) or use the frozen dataset in dataset/
python3 harness/collect_prs.py          # resumable; requires GitHub token
python3 harness/fix_first_review.py     # first-review SHA audit + recovery (API)
python3 harness/split.py                # assign train/dev/test + leak check

# 2) Prepare rubric + rules (LLM, cached; default deepseek flash)
python3 harness/rubric.py dev test
python3 harness/rules.py --force-mine   # mine train rejections -> agents/

# 3) Evaluate dev (tag-bound; resumable)
python3 harness/judge.py empty dev 0 dev_iteration
python3 harness/judge.py baseline dev 0 dev_iteration
python3 harness/judge.py ours dev 0 dev_iteration
python3 harness/score.py harness/state/judge_ours__dev__dev_iteration --split dev --json reports/score_ours_dev_dev_iteration.json

# 4) Freeze + one-shot formal test
python3 harness/consistency.py ours 5 5 dev final
python3 harness/freeze.py
python3 harness/run_test.sh final        # test judged ONCE post-freeze
python3 harness/bootstrap.py --split test --tag final
# dev must carry its own tag (e.g. nr) so round-4 dev scores are not confused
# with older tag=final ones:
python3 reports/make_final_card.py --tag final --dev-tag nr && python3 harness/report.py
```

## Environment & credentials

- GitHub token: GitHub App helper (`../scripts/myanyagent-credential-helper.cjs`)
  or env `GITHUB_TOKEN`. The App token is read-only and does NOT expose
  collaborator permission endpoints (HTTP 403) -> admin permission is
  unverified, recorded as a limitation (`dataset/evidence/admin_audit.json`);
  author-self-finalized PRs are excluded from the scored universe.
- LLM: keys from `~/.local/share/opencode/auth.json` (`opencode-go`,
  `openrouter`) or env (`OPENCODE_API_KEY` / `OPENROUTER_API_KEY`).
- Default model: `deepseek-v4-flash-0731` family — provider priority
  `opencode-go` (`deepseek-v4-flash`) -> `openrouter`
  (`deepseek/deepseek-v4-flash-0731`); `LLM_PROVIDER=...` overrides. Judge
  reasoning (`JUDGE_REASONING`) is fixed across a comparison round.
- Judge prompt injection: full layered AGENTS.md (core + sub-modules) is
  injected (cap 60k chars).

## Evaluation protocol notes

- **State isolation**: judge outputs are stored under
  `state/judge_<variant>__<split>__<tag>/`, bound to variant+split+tag, to the
  AGENTS content AND to the judge model+provider via a manifest (stale outputs
  are cleared). Nothing is silently reused across rounds or splits.
- **Admin-universe filter (1.1, evidence-gated)**: the final actor must carry
  per-actor maintainer evidence (weak maintainer evidence via mechanically
  filtered direct pushes on main — ketd/2391384896/redreamality — or the actor's
  OWN COLLABORATOR+ association) or be a distinct non-author closer/merger
  (permission unverified). Author-self-finalized PRs are **included when the
  author is an evidence-gated maintainer** and **excluded otherwise**
  (`split.json.excluded_self_finalized`). `admin_audit.py` attributes
  author_association to the **final actor only**; other commenters' associations
  are marked unrelated. Manage permission itself is unprovable (App token 403)
  and is recorded, never claimed.
- **Literal first review (1.2)**: PRs with a formal review have
  `snapshot_type=literal_first_review` (19/72 = 26.4% coverage); unrecoverable
  first-review snapshots (5 PRs: 137/23/65/83/97) are excluded; the rest use an
  explicit `first_activity_proxy` snapshot that is NOT disguised as a literal
  first review.
- **Direct-push weak evidence (2.2)**: `harness/mine_direct_push.py` classifies
  the local `main` history deterministically: 139 commits -> 4 merge, 0 bot,
  16 PR-associated, 6 version/release, 1 lockfile/generated -> **112 surviving
  direct pushes**; per-actor weak-maintainer evidence in
  `dataset/evidence/direct_push.json`.
- **Freeze**: after dev-only iteration, record content hashes + split + judge
  model/provider (`freeze.py`) and run the test set exactly once
  (post-freeze, tag=v5); do not re-run test after that.
- **Time split isolation**: rule mining uses train only; iteration uses dev
  only; the test set is never read during iteration.
- **Bootstrap binding**: `bootstrap.py` writes split/tag-bound
  `reports/bootstrap_<split>_<tag>.json` so a dev bootstrap cannot clobber the
  formal test bootstrap.

See `harness/README.md` for the detailed pipeline, and `harness/OFFLINE.md` for
credential-free fixture/offline modes.
