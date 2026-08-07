# Offline Proxy Evaluation Protocol

Protocol version: `3.0.0`

## 1. Construct and claims

This benchmark estimates whether a frozen instruction artifact helps a frozen
judge recover qualified historical maintainer decisions from the exact
pre-review snapshot. It is evidence about **review-policy extraction**. It is
not direct evidence that coding agents produce better contributions. Direct
contribution claims require `agent_in_loop.md`.

## 2. Arms and pairing

Evaluate all eligible samples in all three arms:

1. `EMPTY`: an empty instruction artifact;
2. `REPO_BASELINE`: only repository guidance/configuration available at the
   train knowledge cutoff, packed by the frozen baseline packer;
3. `CANDIDATE`: frozen core `AGENTS.md` plus only annexes selected by the frozen
   retrieval algorithm.

The three arms use identical sample snapshots, judge prompt template, output
schema, model/provider deployment, sampling parameters, context packer,
truncation policy, and retry policy. Randomize arm order per sample with a
frozen seed. Cache keys include every frozen input hash and arm identifier.

The judge receives no tools, network, repository identifiers, original review
text, labels, comments, reviewer identity, or post-event state. CI replay is
provided as deterministic evidence but is not requested as an output and does
not contribute to candidate effectiveness.

## 3. Preregistered outcomes

### Primary outcome

`decision_correct` is 1 when the judge's binary decision matches the qualified
review event (`APPROVE` or `REQUEST_CHANGES`) and 0 otherwise. Primary arm
effects are paired percentage-point differences in mean decision correctness:

- `CANDIDATE - EMPTY`;
- `CANDIDATE - REPO_BASELINE`.

The final claim requires, for both comparisons:

- observed delta at least `metrics.min_effect_pp`;
- simultaneous confidence-interval lower bound strictly above zero; and
- multiplicity-adjusted paired-test p-value below `statistics.alpha`.

Absolute safeguards also must pass:

- decision accuracy at least `metrics.decision_accuracy_floor`;
- reject recall at least `metrics.reject_recall_floor`;
- balanced accuracy at least `metrics.balanced_accuracy_floor`.

Report all metrics with intervals on the full decision cohort and the `CI_PASS`
subset. The latter is diagnostic unless preregistered as co-primary.

### Secondary outcomes

For rationale-eligible `REQUEST_CHANGES` samples, score normalized issue-point
recall against the protected observed-review rubric. Report macro and micro
recall. Report `observed_review_agreement_precision` for judge objections that
match observed rubric points. An unmatched objection is **unverified**, not a
hallucination. Only independently adjudicated false claims enter
`adjudicated_hallucination_rate`, with adjudicator agreement and sample size.

Cost, input/output tokens, latency, truncation, refusal, invalid-schema, and
cache-hit rates are reported per arm and never silently dropped.

## 4. Stability diagnostic

Before dev iteration, choose a frozen, stratified diagnostic subset disjoint
from final test and run each arm/judge combination `stability.repeats` times.
Report:

- per-sample complete agreement;
- mean pairwise decision agreement;
- Fleiss' kappa with interval;
- schema-valid response rate;
- variation in reason-point recall.

Call these stability/repeatability metrics, not accuracy ceilings. They do not
lower, cap, or waive an accuracy requirement. Low stability may trigger a
frozen-diagnostic prompt/packer comparison, deterministic sampling, or
predeclared vote aggregation before profile freeze. Any judge model/provider
change invalidates prior stability and all paired dev comparisons.

## 5. Power and effective sample size

The cheap census supplies class prevalence and a vertical slice supplies paired
discordance estimates. Before Scale Gate, compute the minimum number of test
groups needed to detect `min_effect_pp` at the configured family-wise alpha and
power for two candidate-vs-baseline comparisons. The calculation must account
for paired discordance, cluster/group splitting, class safeguards, and expected
attrition. Persist assumptions, method/version, and sensitivity bounds.

If the frozen eligible test groups or reject events are insufficient for the
preregistered design, do not run a claim-seeking final test merely to obtain a
binary result. End `INCONCLUSIVE` or explicitly downgrade to a descriptive
study before test access. A valid final test whose realized analyzable sample
falls below the frozen power requirement is `INCONCLUSIVE`, not success or
failure.

## 6. Inference

Default inference is a cluster-paired bootstrap over `split_group_id`, stratified
by true class when feasible, using the frozen seed and at least the configured
number of resamples. Construct family-wise simultaneous confidence intervals
for the two primary deltas. As a preregistered confirmatory check, use exact
McNemar tests on paired correctness and apply Holm correction across the two
baseline comparisons.

Do not choose tests after seeing test outcomes. Report raw paired contingency
tables, effect sizes, intervals, raw and adjusted p-values, class counts, and
all exclusions. No `delta OR p-value` success rule is permitted.

## 7. Dev iteration

Dev evaluation may return only aggregate values and controlled diagnostic
categories such as `REJECT_FALSE_NEGATIVE_RATE_HIGH`, `ANNEX_TRUNCATION_HIGH`,
or `SCHEMA_INVALID_RATE_HIGH`. It must not disclose raw comments, stable sample
IDs, or per-sample truth to candidate workers.

Each iteration records hypothesis, parent/candidate hashes, exact change,
metrics/delta, confidence bounds, cost, latency, and failure class. Select the
best candidate by the frozen dev objective subject to safeguards. Stop when
`max_dev_iterations`, `low_gain_patience`, budget watermark, or closeout state
fires. The latest candidate is never automatically the best.

## 8. Freeze and final test

Freeze and hash every field listed in `reproducibility.md`. The final evaluator
checks the profile hash before each shard. Test work may be sharded and resumed
idempotently, but each `(run_id, profile_hash, sample_id, arm)` prediction has
exactly one canonical result. Retry only failures allowed by the frozen retry
policy; retain every attempt in telemetry.

Terminal rules:

- all evidence and safeguards pass: `SUCCESS`;
- valid, sufficiently powered evidence fails a criterion: `VALID_TEST_FAILED`;
- valid but insufficient evidence: `INCONCLUSIVE`;
- unfinished due only to infrastructure: `INFRA_INCOMPLETE`, resumable under
  the exact profile;
- leakage, profile mutation, contract violation, or noncanonical rerun:
  `PROTOCOL_INVALID`.

Final-test output never returns to candidate repair. A new candidate requires a
new preregistered experiment, not a continuation of this test.

