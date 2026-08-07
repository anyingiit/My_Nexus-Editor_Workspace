# Commit Completeness Checklist (CHECKABLE items)

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

## First-review CI / CLA triage (do not make it an auto-verdict either way)
Trains/merged history includes PRs accepted when the first-review snapshot had
only `license/cla` recorded, a pending CLA, or even a failing first `Test and
Build` run; dev PR 88 (approved) exhibits the same shape. So, as a CHECKABLE
list, decide on the content + visible evidence, NOT the CI colour:

1. A pending/absent `license/cla` status is a MERGE blocker to note, and for a
   first-time contributor it normally resolves after review - it is never by
   itself a content rejection of an otherwise-complete PR.
2. An unverified build gate (only `license/cla` and/or `Publish Packages:
   skipped` recorded, no `Test and Build` run) is not proof the code builds;
   it is a *weigh* signal, not a standalone rejection.
3. A single recorded `Test and Build: failure` at first review signals WIP or a
   fixable problem; merged PRs exist with such a failure. Inspect the patch
   and relevant tests: if the changed behaviour is regression-covered and no
   visible defect remains, APPROVE and note "rerun CI before merge"; reject
   ONLY for a relevant, unresolved failure/defect or another hard blocker.
4. Conversely, do NOT auto-approve just because a green `license/cla` label or
   an otherwise-green snapshot - a content defect, deliverable mismatch,
   missing required tests/README/OpenSpec, out-of-scope or mixed-concern change
   still forces reject regardless of the CI label.
5. Always verify content fidelity: the diff must deliver exactly what the
   title/description promise (no deliverable mismatch, no own-tests/corpus that
   still list failing cases for the claimed feature).

## Decision boundary: content-completeness vs. non-blocking nits
The deciding question at FIRST review is whether the PR's *content* is complete
and in-scope, not its CI colour or checklist hygiene. This is calibrated
against dev PR 88 and the train approves (PRs 3/5/10/11/15 were merged despite
a recorded first-review `Test and Build` failure, an unverified gate, a pending
CLA, or no CI recorded at all) and against the dev/train rejects, which all
carry a content-level defect visible in the first-review state.

**APPROVE** when ALL of these hold:
- the change is in-scope and single-concern (or a coordinated multi-package
  change whose description has a per-package section - CONTRIBUTING §2);
- the diff fully delivers the title/description concern (no deliverable
  mismatch, and no added tests/corpus that still list failing cases for the
  claimed feature);
- every touched path that CONTRIBUTING §3.3 requires tests for has them
  (live-preview / table / wikilinks -> regression test);
- no hard blocker (core AGENTS.md §2) and none of the reject conditions below
  is present.

When those hold, the following are NON-BLOCKING - mention them as follow-up /
merge-blocker notes but do NOT make any of them a rejection reason: a recorded
first-review `Test and Build` failure; an unverified/missing build gate; a
pending/absent `license/cla` status (a merge blocker, not a content blocker); an
absent manual electron-demo note; an unrung `openspec validate --strict`; a
missing or un-thorough description section; a style or type-safety nit such as
a single `as any`; or a small amount of related cleanup bundled without changing
the deliverable's concern.

**REJECT** only when at least one of these is visible in the first-review state,
each resting on a repo hard rule (CONTRIBUTING / GOVERNANCE / CLAUDE / PR
template / CI config):
- build artifacts / secrets / personal data committed (§2.1 / §5);
- out-of-scope addition (core AGENTS.md §8: AI/LLM surface, vendor SDKs,
  product features, content linting beyond the AST, etc.);
- functional code primarily AI-generated without disclosure (§2.2);
- new runtime dependency without MIT-compatible license + rationale (§2.3);
- malformed Conventional-Commits title (core AGENTS.md §4);
- definitive anti-CLA failure;
- new public API without the relevant `packages/*/README.md` (+ `README.zh.md`)
  update (CONTRIBUTING §3.2);
- new capability (new plugin / new public API / breaking change / cross-package
  work) with no linked OpenSpec proposal (CONTRIBUTING §3.1; bug fixes,
  internal refactors, dependency bumps and test/doc additions are exempt);
- touched live-preview/table/wikilinks path with no regression test
  (CONTRIBUTING §3.3) - an external eval harness (`eval/`, python/js corpus) is
  NOT a substitute for the required in-package vitest regression test;
- deliverable mismatch: the diff does not implement/fix what the title and
  description promise, including a PR whose own added corpus/eval cases still
  document failing cases for the described behavior (e.g. a table-drag harness
  whose corpus records produced row/cell corruption and whose runner exits
  nonzero) while the PR claims the check passes;
- a demonstrated correctness/compatibility regression in the patch/tests;
- a genuine multi-concern bundle (refactor mixed with feature work, or several
  unrelated features/fixes in one PR), as opposed to a coherent single concern.

Do not invent objections: a reject reason must name one of the above (or a core
AGENTS.md §2 hard constraint) and be grounded in the first-review diff/checks;
nit-level issues on an otherwise-complete focused PR are non-blocking comments
only. The non-blocking items listed above apply ONLY when none of the reject
conditions is present - they never excuse a documented failing case, missing
required tests, or a deliverable mismatch.

### Calibration: three recurring nit categories (dev/train-calibrated)
Do not let these three categories reject a complete, focused PR:
- *mixed-concern*: reject ONLY when the PR bundles several INDEPENDENT
  features/fixes or mixes refactor into feature work (CONTRIBUTING §2). A
  resubmission/continuation that keeps one coherent concern - with tightly
  related cleanup or a small amount of related persistence work - is NOT mixed;
  it is the natural shape of a resubmitted, previously-reviewed PR.
- *type-safety*: a single `as any` or a loose type on an internal (non-public)
  symbol is a style nit; reject only for an untyped public-API contract or a
  demonstrated runtime hazard in the patch.
- *persistence / state bundling*: persisting state or holding a widget/lock
  flag inside the same focused fix is normal and acceptable (dev PR 88 keeps
  the table-editing lock in its copy fix) when the fix is otherwise
  single-concern and covered; reject only for a lifecycle bug demonstrated in
  the patch/tests.

### Worked calibration examples (train/dev-visible; WEAK/dev calibration only)
These two examples are drawn ONLY from the train/dev first-review history this
AGENTS was mined from. They exist to make the priority in core AGENTS.md §3/§9
concrete. They are NOT rules about the specific PR under review, never override
a repo hard rule, and must never be used to infer the final outcome or any
hidden reviewer comment for the PR being judged. Decide from THIS input only.
Each example is described from its first-review state (title, files, tests,
replayed checks) - exactly what is visible below.

- **Approve-side (content > CI label/CLA)**: the dev table-cell copy fix and the
  train merges 3/5/10/11/15 are content-complete, focused, single-concern fixes
  with the regression test their touched path requires (a `live-preview`/
  `table`/`wikilinks` path -> a focused vitest regression test), each accepted
  DESPITE a first-review snapshot that recorded only `license/cla` pending + a
  first `Test and Build: failure` (or only `Publish skipped`, or no CI at all)
  at first review, with the merge following after. Interpretation: a focused,
  in-scope, content-complete PR that fully delivers its described concern with
  its required tests is APPROVED; a pending CLA (merge blocker), an unverified
  build gate, and a single first-review red run are follow-up / merge notes,
  never standalone rejection reasons (see the non-blocking list above).
- **Reject-side (deliverable mismatch / missing required test)**: the dev
  image-paste PR is shaped like this: the title and description promise an
  image-paste plugin, but every commit and changed file implement unrelated
  fuzzy-search behaviour (deliverable mismatch), the touched
  `live-preview-table.ts` path has NO required regression test covering the
  changed rendering/selection, and the new capability has no OpenSpec/README
  linkage. Interpretation: a REJECT even when CI is green or pending - the
  reject reasons enumerate the contradiction between the promise and the diff
  and the missing required regression test. The same logic rejects an external
  `eval/` harness whose own corpus still records failing cases for the claimed
  behaviour (external eval is not a substitute for the required in-package
  vitest regression test).

## Compliance / governance (see GOVERNANCE.md §6)
- [ ] CLA signed (bot prompts first-time contributors)
- [ ] AI-generated code disclosed with details (functional code must NOT be primarily AI-generated)
- [ ] New runtime dependencies listed: name, version, license, why; MIT-compatible only
- [ ] No build artifacts / secrets / `.env` / personal vault data committed
- [ ] No `git push --force` to main
