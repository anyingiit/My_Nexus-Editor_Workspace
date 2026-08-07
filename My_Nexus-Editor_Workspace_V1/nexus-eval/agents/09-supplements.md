# Third-Party Supplements (lowest-priority evidence)

Per the conflict priority in `AGENTS.md §9`, these references are consulted
ONLY where the Nexus-Editor repository's own hard rules are silent:

```
repo hard rules > test/dev-set validation results > PR rejection reasons
  > implicit norms from train history > direct-push weak evidence
  > third-party supplements
```

Any clause in these supplements that contradicts a Nexus-Editor hard rule is
NOT adopted (the repo rule wins). Fetched as source of record on
2026-08-05 (raw copies archived at `dataset/evidence/third_party/`):

| Source | URL | sha256 (short) | fetched lines |
|---|---|---|---|
| Apache Airflow | https://github.com/apache/airflow/blob/main/AGENTS.md | e6f3e82e78b708b1 | 519 |
| Pydantic AI | https://github.com/pydantic/pydantic-ai/blob/main/AGENTS.md | 91c82d7c7638c0ba | 146 |
| OpenAI Codex | https://github.com/openai/codex/blob/main/AGENTS.md | c3f80e8386eb170b | 322 |

## Adopted non-conflicting supplements (only where Nexus rules are silent)

1. **Rebase before submitting; resolve conflicts by rebasing** *(Airflow: "Before
   pushing, always rebase your branch onto the latest target branch (usually
   main)")* — consistent with the train-mined norm "ensure the PR is rebased on
   the latest main and does not conflict". Adopted.
2. **Keep a fix narrow: the minimal change plus one regression test; do not
   widen a bug fix to sibling cases on a hunch** *(Pydantic AI)* — consistent
   with Nexus scope rule / one-PR-one-concern. Adopted.
3. **Review as the first line of defense against low-quality contributions;
   guard the project's public API/abstractions** *(Pydantic AI)* — consistent
   with the judge's role in this guide. Adopted.
4. **Run the tests for the specific package/project that changed** *(Codex:
   "run the test for the specific project that was changed")* — consistent with
   the repo CI `verify` job. Adopted.
5. **Do not add spurious changelog/newsfragment entries for non-user-facing
   changes** *(Airflow)* — Nexus has no changeset system; the same "keep the PR
   clean" intent is adopted when a contributor asks whether an entry is needed.
6. **Prefer fixing forward over big refactors to fix one caller** *(Pydantic AI,
   Codex)* — consistent with Nexus single-concern scope. Adopted in spirit.

## Not adopted (conflicts with Nexus hard rules)

- **Airflow forbids Conventional Commits.** Nexus requires Conventional Commits
  (`<type>(<scope>): <subject>`, CONTRIBUTING §2) → repo rule wins; NOT adopted.
- **Broad AI-generated-code acceptance.** Nexus GOVERNANCE §6.2 rejects PRs whose
  functional code is primarily AI-generated → repo rule wins; NOT adopted.
- **CLI/rust-specific lint frameworks (Codex `just fmt`, Airflow prek hooks).**
  Nexus uses pnpm/TypeScript and its own `ci.yml` → NOT adopted (different
  toolchain; CI config is authoritative).
- **Airflow/"everyone can merge" community process.** Nexus has documented
  maintainer approval + CLA requirements → NOT adopted.

These supplements are weakest evidence: they are never used to override a repo
hard rule, a dev/test-set result, a PR rejection reason, or a train-mined norm.
