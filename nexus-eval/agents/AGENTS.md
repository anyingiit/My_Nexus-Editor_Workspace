# AGENTS.md — Nexus-Editor Contribution Guide

This layered AGENTS.md governs how to contribute to the
[`floatboatai/Nexus-Editor`](https://github.com/floatboatai/Nexus-Editor)
repository and how to review its pull requests. It is written for the human
contributor, coding agents, and automated reviewers (judges) that work in this
repository.

It is a **condensation** of the authoritative repo rules in `CONTRIBUTING.md`,
`GOVERNANCE.md`, `CLAUDE.md`, `.github/PULL_REQUEST_TEMPLATE.md`, and
`.github/workflows/ci.yml`, refined with patterns mined from the project's PR
history. Where this file and those source files differ, **the repository's
hard rules win** (see "Conflict priority" below).

---
## 1. Repository posture

- Nexus-Editor is a **headless, AST-driven Markdown editor engine** (pnpm +
  TypeScript monorepo). It is not a desktop product and not an all-in-one app.
- Development follows **Conventional Commits** and **OpenSpec**-driven flow.
- `apps/electron-demo` demonstrates engine capabilities only; it is never a
  reference product.

---
## 2. Hard constraints (blockers — apply to EVERY decision)

These are non-negotiable. A PR violating any of them is rejected even if it is
technically good.

1. **No build artifacts / secrets / personal data.** Never commit `dist/`,
   `dist-electron/`, `release/`, compiled `.js` from `.ts`, `.env*`,
   credentials, vault data, or recordings. If a diff touches these, reject.
2. **AI-generated functional code must be disclosed and is not acceptable as
   the primary implementation.** PRs whose functional code is primarily
   AI-generated (unexplainable end-to-end) are rejected. AI-assistance notes
   must be in the description.
3. **New runtime dependencies** (any `dependencies` entry) require: an
   MIT-compatible license (rejected: GPL/AGPL/SSPL/BUSL/CC-BY-NC), and a
   rationale in the PR body (name, version, license, why, what we lose by
   writing it ourselves).
4. **Out-of-scope additions are rejected regardless of quality** (see §8):
   AI/LLM integrations, vendor SDKs, general-purpose UI component libraries,
   non-primitive product features (notebook management, cloud sync UI, account
   systems, purchases), and content linting/schema validation beyond the AST.
5. **One PR, one concern.** Never mix refactor with new features, and do not
   bundle multiple unrelated features/fixes into one PR.
6. **CI evidence is replayed and weighed, but NOT the sole decision driver**
   (mined from the train split's acceptance history). First-review check sets
   are often minimal for freshly-opened heads (frequently just a `license/cla`
   status, since the `Test and Build` workflow may not have produced a
   completed run for that head yet); many merged PRs were accepted with only
   `license/cla success` or even a still-failing first run recorded. So:
   - `license/cla success` or `Publish Packages: skipped` is NOT proof the code
     builds → build gate *unverified* (see §4b).
   - **An unverified / missing build gate is NOT by itself a rejection.** If the
     PR is otherwise in-scope, single-concern, well-described, complete and
     quality, an unverified gate must not flip the decision to reject.
    - A single recorded `Test and Build: failure` at first review signals WIP or
      a fixable problem (merged PRs exist with a first-review failure): inspect
      the patch and relevant tests before deciding. If the changed behavior is
      covered and no visible defect remains, an unexplained, transient, or
      unrelated failure is not a blocker; reject only for a relevant unresolved
      failure, defect, or another blocker, never on this label alone.
   **Important (CI label vs. gate evidence):** the `ci_result` label a judge
   reports must ALWAYS follow the faithful replay convention (§3.4) — `pass`
   whenever at least one recorded status or check run is `success` with nothing
   failing, `fail` whenever a recorded check/status has a definitive failure,
   `unknown` only when nothing conclusive is recorded. In particular a recorded,
   successful `license/cla` status IS a success status: it makes the replayed
   `ci_result` `pass` (with the build gate separately unverified per §4b). The
   label is a faithful report of the recorded checks; it is evidence to weigh,
   never the sole accept/reject driver.
7. **CLA required — but weigh a pending/absent `license/cla` status like CI**
   (§2.6): an unsigned CLA blocks *merge*, and the CLA is normally signed before
   merge. At first review, many accepted PRs had no completed `license/cla
   success` yet (the bot prompts first-time contributors after review), so a
   pending/absent CLA is a compliance flag to mention, not enough by itself to
   reject a content-good PR. A definitive anti-CLA failure (e.g. bot reports
   declined) is a blocker.

---
## 3. Review decision procedure (for judges)

When reviewing a PR as it stood at its **first review**:

1. **Reconstruct the state**: PR title, description, the commits that existed
   at first review, the files changed, and the CI/check results recorded for
   that head state.
2. **Apply the conflict priority** (below) to resolve anything ambiguous.
3. **Evaluate in this order**:
   a. **Hard constraints** (§2) — any violation → reject.
   b. **CI evidence** (see §4b) — report the label faithfully and weigh it as
      evidence, but do NOT elevate an unverified or first-review build status
      into a standalone rejection; decide on the totality of evidence.
   c. **Content-fidelity and completeness** (§5) — the PR must actually
      deliver what its title and description promise:
      - **Approve baseline (dev/train-calibrated):** a focused, in-scope,
        single-concern PR whose diff fully delivers its described concern with
        the tests CONTRIBUTING §3.3 requires for the paths it touches starts
        from APPROVE. First-review CI colour, an unverified build gate, a
        pending CLA, checklist hygiene, and style/type-safety nits are
        follow-up / merge-blocker notes, not content rejections.
      - *Deliverable mismatch* → reject: the diff does NOT implement/fix the
        described concern (scaffolding/harness/docs or an API stub only), or
        the PR's own added tests/corpus still document failing cases for the
        claimed fix.
      - *Compatibility regression* → reject: the change knowingly regresses
        documented behavior it touches (e.g. search/word-boundary semantics for
        ASCII/accented/CJK/user-supplied regexes) without addressing it; for
        `regexp + wholeWord`, no unconditional `u`/flag change without a
        compatibility regression test.
      - Focused-interaction nits are non-blocking: an absent first-review
        manual-demo note, a synthetic-DOM test, or a type-safety nit (a single
        `as any`) never rejects by itself — require a visible defect, missing
        coverage, or a missing required test/README sync/OpenSpec for a new
        capability.
      - Reject a correctness/regression claim only when the defect is directly
        shown by the patch/tests or contradicts a stated requirement; for table
        lock cleanup require a demonstrated lifecycle violation, never a
        speculative ownership concern or a different-testing-strategy complaint
        when the touched path has focused coverage.
   d. **Scope** (§8) and single-concern rule (§2.5) — violation → reject.
4. **Classify CI faithfully**: `fail` only when a recorded check/status is a
   definitive failure; `pass` only when at least one check is success and none
   fail; `unknown` when everything is pending/queued/skipped/absent. A
   successful `license/cla` alone labels the CI `pass` (it is a success status),
   with the build gate separately unverified per §4b.
4b. **Distinguish the CI *label* from the *build gate evidence*** (mined from
   the train split's PR history — see §2.6). The only recorded artifacts that
   prove `pnpm typecheck`, `pnpm test`, and `pnpm build` actually ran for the
   reviewed head are **build/test check runs** (workflow names such as
   `Test and Build` with a completed conclusion). A `license/cla` status (even
   `success`) and a `Publish Packages: skipped` run are compliance/publish
   bookkeeping — they are **NOT** evidence that the code compiles, tests, or
   builds. Concretely:
   - build/test check run recorded with `conclusion=success` and nothing
     failing ⇒ build gate **verified**, `ci_result=pass`.
   - any recorded definitive failure ⇒ `ci_result=fail`.
   - only `license/cla` status (or only `Publish` skipped, or nothing at all)
     with **no build/test check run** ⇒ the recorded `ci_result` label is
     still reported faithfully per item 4 (a successful `license/cla` alone
     labels `pass`; nothing conclusive labels `unknown`), and the build gate is
     **unverified**. **Unverified/missing build gate or a first-review failure
     is a signal to weigh, not a standalone rejection** (§2.6): if the PR is
     otherwise in-scope, single-concern, complete and well-described,
      approve; if the content itself is out-of-scope, mixed-concern, incomplete
      or low-quality, reject — with the content reasons. Do not restate an
      unverified or first-review failure as a content blocker.
5. **Decide `approve` or `reject`.** Give concrete, PR-specific reasons
   grounded in what you observed. Do not fabricate objections. A focused,
   content-complete, in-scope PR is approved even with a first-review CI
   failure, unverified gate, or pending CLA - cite those as follow-up notes,
   not as rejection reasons (see `agents/02-commit-completeness.md`).
6. **Do not leak ground truth.** You see only the first-review state; the
   final merged/closed outcome and reviewer opinions are not part of your input.

---
## 4. Branches & commits

- Branch naming: `<type>/<scope>-<short-desc>` (e.g. `feat/toolbar-list-toggle`).
- Commit/PR title: `<type>(<scope>): <subject>`.
  - type: `feat|fix|perf|refactor|test|docs|chore|ci|build`
  - **Format is a hard requirement**: the title must be
    `<type>(<scope>): <subject>` with a lowercase conventional type; malformed
    titles (`Feat/core..`, `Feat(...`, missing `:`, non-lowercase type, wrong
    separator) block the PR. Scope may be omitted when there is no single
    package scope (CONTRIBUTING.md §2: "must be one of the following or
    omitted"), so an otherwise-valid unscoped title (`fix: subject`) is NOT a
    blocker, but a malformed title is.
  - subject: imperative, English, ≤ 72 chars, no trailing period.
- Base every PR on the **current** `main`; stale branches that roll back
  merged functionality are rejected.

---
## 5. Commit-completeness checklist (checkable)

Verified when possible from the PR itself (title, description, diff, checks):

- [ ] Title + values follow Conventional Commits (§4).
- [x] Description explains **why**, not just **what** (Summary/Motivation/
      Changes/Testing/Compliance template).
- [ ] Tests added where the change type requires them:
  - `packages/core` rendering → vitest unit tests
  - `plugin-*` → vitest unit tests
  - React/Vue SDK → framework unit tests
  - live-preview / table / wikilinks → **regression test required**
- [ ] The diff actually delivers the promised change (§3c: no deliverable
      mismatch, no known correctness/compatibility regression, no own
      tests/corpus still listing failing cases).
- [ ] `pnpm typecheck` + `pnpm test` + affected packages `pnpm build` pass.
- [ ] Public-API changes update `packages/*/README.md` (and `README.zh.md`).
- [ ] New / changed capability or breaking change → **OpenSpec proposal linked**
      under `openspec/changes/<id>/`.
- [ ] Touched `packages/core/src/live-preview-table.ts` → the 12 Table Widget
      rules in `CLAUDE.md` were followed.
- [ ] AI disclosure present if AI assistance was used (§2.2).
- [ ] New runtime dependencies listed with license + rationale (§2.3).
- [ ] No build artifacts / secrets committed (§2.1).
- [ ] UI changes exercised in the electron-demo (manual check described).

---
## 6. Code style

- TypeScript **strict mode**; public exports have explicit types.
- No "what" comments and no PR/issue back-references in comments; comment only
  for a non-obvious **why**.
- Follow existing patterns: destroyed-guard on APIs, `sel.from/.to`
  (`SelectionRange`), normalized internals, no redundant re-implementation.
- Core logic needs automated tests; if jsdom lacks layout, use a
  layout-capable environment (e.g. Playwright).
- Details: `agents/01-code-style.md`.

---
## 7. Workflow & commands (authoritative)

- Install: `pnpm install --frozen-lockfile`
- Typecheck: `pnpm typecheck` (`pnpm -r exec tsc --noEmit`)
- Tests: `pnpm test` (`vitest run`)
- Build packages: `pnpm build`
- Demo build: `pnpm build:electron-demo`
- OpenSpec: `openspec validate <change-id> --strict`, `openspec list`,
  `openspec archive <change-id> --yes`
- Rejected-file guard: never `git add -f` anything matching `.gitignore`.

---
## 8. Scope

Nexus is a **headless, AST-driven Markdown editor engine**.

**In scope:** `packages/core` (CM6 state, AST pipeline, live preview, widget
API, events), `packages/preset-gfm`, `packages/plugin-*` (editor features),
`packages/react` / `packages/vue` (thin bindings, kept in lockstep), and
`apps/electron-demo` (demonstration only).

**Out of scope (reject even if well-built):**
1. AI / LLM integrations of any kind in packages or demo.
2. Bundled vendor SDKs (OpenAI, Anthropic, cloud storage, etc.).
3. General-purpose UI component libraries (toast, dialog, modal).
4. Non-primitive product features (notebook management, cloud sync UI,
   account systems, purchases).
5. Schema validation / content linting beyond what the AST exposes.
`apps/electron-demo` gains are demo-only; do not add product-level surface
(e.g. file-management UI, AI sidebars, settings systems) to it.
New plugins must match a `docs/ROADMAP.md` entry (otherwise file an issue
first); new public APIs in `core` need OpenSpec + maintainer approval.

---
## 9. Conflict priority

Applied in this order; where a tie cannot be resolved, the repository's own
hard rules (CONTRIBUTING.md / GOVERNANCE.md / CI config) win. All comparisons
are STRICT (">"); equal-priority conflicts are resolved by the repo hard rule.

```
repo hard rules > test/dev-set validation results > PR rejection reasons
  > implicit norms from train history > direct-push weak evidence
   > third-party supplements
```
**"test/dev-set validation results"** means ONLY signals measured on the dev
split (AGENTS iteration); test-set results are never used for iteration.
**"direct-push weak evidence"** = norms inferred from non-PR (direct) commits to
`main` by repository holders. This whole class is WEAK evidence. Before use,
each direct-push commit is mechanically filtered to drop: merge commits,
bot-authored commits, commits touching only lockfile/generated artifacts,
version bumps and release chores. After that filter, only the remaining
commits are considered, and only when the repo hard rules are silent (the
compliance filter runs after hard constraints are fixed, to avoid circular
definitions). Direct-push norms never override a repo hard rule, a dev-set
result, a rejection reason, or an implicit train norm.

"Third-party supplements" (only consulted when the repo rules are silent) are:
- https://github.com/apache/airflow/blob/main/AGENTS.md
- https://github.com/pydantic/pydantic-ai/blob/main/AGENTS.md
- https://github.com/openai/codex/blob/main/AGENTS.md

Sources, the non-conflicting supplements adopted from each, and the clauses
from them that were NOT adopted because they conflict with Nexus hard rules are
recorded in `agents/09-supplements.md` (archived sources + hashes under
`dataset/evidence/third_party/`).

---

## 10. Index / on-demand loading
| Concern | Sub-module |
|---|---|
| Full constraints incl. mined norms | `agents/00-constraints.md` |
| Code style detail | `agents/01-code-style.md` |
| Commit-completeness detail + test matrix | `agents/02-commit-completeness.md` |
| Scope detail | `agents/03-scope.md` |
| PR workflow and build commands | `agents/04-workflow.md` |
| Third-party supplements (lowest priority) | `agents/09-supplements.md` |

Load a sub-module only when the corresponding concern is in scope; the core
file above is enough for a quick decision gate. `00-constraints.md`'s mined
norms are weak evidence gated by the §9 conflict priority.
---

*Kept in sync with `Nexus-Editor/CONTRIBUTING.md`, `Nexus-Editor/GOVERNANCE.md`,
`Nexus-Editor/CLAUDE.md`, `.github/PULL_REQUEST_TEMPLATE.md`, and
`.github/workflows/ci.yml` as of the dataset cutoff.*
