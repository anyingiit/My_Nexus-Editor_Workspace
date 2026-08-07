# Paired Agent-in-the-Loop Protocol

Protocol version: `3.0.0`

## 1. Purpose

This experiment tests the real target construct: whether coding agents given
the candidate instructions produce better Nexus-Editor contributions. It is
separate from the historical-PR proxy and may be small, but it is required for
any claim about contribution quality.

## 2. Task selection

Select issue-like tasks that were available by the frozen cutoff and can be
reconstructed at a fixed base commit without revealing a known solution. Group
near-duplicate tasks. Exclude tasks whose solution is already present in the
model-visible checkout or whose acceptance criteria require unavailable
services/secrets. Freeze task statements, base SHAs, deterministic tests,
resource limits, and blind-review rubric before any agent run.

Use enough tasks for the declared claim; otherwise label results exploratory.
Do not reuse the final offline test to tune this experiment.

## 3. Three-arm paired design

For every task run the same frozen coding model/deployment under:

1. `EMPTY` instructions;
2. cutoff-valid `REPO_BASELINE` instructions;
3. `CANDIDATE` instructions.

Hold model/provider, system prompt except arm artifact, tools, checkout, base
commit, token/cash/time limits, retry policy, sandbox image, network policy, and
test environment constant. Randomize arm order by task using a frozen seed.
Use independent clean workspaces and fresh model contexts. Prevent outputs from
one arm entering another arm's context.

If stochastic replication is funded, freeze replicate count before launch and
treat task as the inference cluster. Never selectively rerun a weak arm.

## 4. Safety

All code and repository configuration are untrusted. Agent work and validation
run in ephemeral rootless sandboxes with no network, secrets, write-capable host
mounts, Docker socket, SSH agent, or external write credentials. Source is
copied from a pinned snapshot; outputs leave through a dedicated artifact path.
Apply hard CPU, memory, PID, disk, output, and wall-clock limits. Package hooks,
linters, compilers, and tests run only inside this boundary.

## 5. Outcomes

Deterministic outcomes:

- required test/CI pass rate;
- task acceptance-criteria completion;
- forbidden-file/scope violations;
- changed-file and diff-size limits;
- reproducible build/test status.

Blind human or independent evaluator outcomes:

- count and severity of correctness issues;
- maintainability and repository-policy violations;
- completeness and unnecessary scope;
- approval/readiness decision using the frozen rubric.

Operational outcomes:

- wall time, tokens, incremental cash, provider credits, tool calls, retries,
  sandbox failures, and generated diff size.

Reviewers see anonymized, order-randomized diffs and deterministic results, not
arm names, cost, or generation traces. Record reviewer identity basis and
inter-rater agreement. A semantic evaluator used here has no access to offline
test truth.

## 6. Analysis and claims

Analyze outcomes paired by task. Predeclare the primary quality outcome, minimum
meaningful effect, safeguards, missing-run handling, and multiplicity policy.
Report task-level results and uncertainty; do not claim superiority from pooled
files or test cases as if independent.

The candidate may support a contribution-quality claim only if it improves the
preregistered primary outcome against both baselines without breaching quality,
scope, cost, or latency safeguards. If sample size is exploratory, state that
explicitly even when point estimates are favorable.

Failure here does not permit reopening an already consumed offline final test.
Further candidate work belongs to a new run and new untouched tasks.

