# Reproducibility and Immutable Profile

Protocol version: `3.0.0`

## 1. Freeze manifest

Before final-test truth is mounted, serialize, canonicalize, and SHA-256 hash an
evaluation profile that includes:

- run/protocol/schema versions and preregistration hash;
- candidate core bytes/hash plus complete annex manifest;
- both baseline bytes/hashes;
- frozen knowledge cutoff and source manifest (commit SHAs/checksums/dates);
- dataset, split-group, eligibility, snapshot, and protected rubric manifest
  hashes, without embedding protected contents;
- judge provider, deployment/model ID and version where exposed;
- full system/user prompt templates and output JSON Schema hashes;
- temperature, top-p, seed, max tokens, stop rules, and vote/repeat policy;
- context packer implementation/version, ordering, limits, and truncation policy;
- annex retrieval corpus hash, algorithm/version, features, tie-breaking, and
  per-sample selection manifest or deterministic derivation;
- arm randomization algorithm and seed;
- retry/backoff policy and canonical-result rule;
- cache-key algorithm/version and cache namespace;
- metric/statistical implementation version, alpha, power, bootstrap count,
  seeds, multiplicity, floors, and minimum effect;
- harness source commit/tree hash, dependency lock hash, runtime version, and
  rootless sandbox/container digest;
- static capability-manifest, runtime-capability-report, and run-config hashes.

The canonical profile validates against
`schemas/evaluation_profile.schema.json`. Profile freeze is an atomic event in
the ledger; later modification is a protocol violation.

Before that event, the controller resolves every non-derived profile SHA field
to actual read-only bytes. The resolution covers candidate and baselines,
protocol/schema/preregistration, knowledge and dataset manifests, prompts,
packer/retriever/scorer implementations, harness tree, and truth-vault version.
Missing, extra, symlinked or hash-mismatched evidence fails closed. Config,
dependency lock, capability policy and runtime report are independently
recomputed and cross-bound rather than accepted as caller-supplied digests.

## 2. Canonical serialization and cache keys

Use UTF-8, normalized line endings, sorted JSON object keys, no insignificant
whitespace, and explicit null handling. Hash raw candidate/baseline files before
packing and packed judge inputs after packing.

A prediction cache key contains at least:

`profile_hash + sample_snapshot_hash + arm + packed_input_hash + repeat_index`

Caches from a different provider/model, prompt, schema, candidate, annex set,
packer, cutoff, dataset, or sampling parameter are invalid even if a human
expects equivalent behavior.

## 3. Provider/model changes

No automatic judge failover is allowed. Transient failures follow only the
frozen retry policy on the same deployment. If the operator permanently changes
provider or model before test, create a new evaluation profile and invalidate
affected dev comparisons, all baselines, stability measures, and prediction
caches; rerun them together. After test truth is opened, a provider/model change
ends the profile `INFRA_INCOMPLETE` unless the exact deployment can be restored;
it does not authorize an incomparable continuation.

Non-evaluation tasks may use other providers when the run config permits, but
their artifacts record provider/model and provenance and still obey budgets.

## 4. Resume and exactly-once semantics

The final test may be physically interrupted and resumed. On resume verify
run ID, profile hash, event chain, static capability manifest, runtime
capability report, truth-vault version,
dataset manifests, and container digest. Completed canonical predictions are
immutable. Dispatch only missing keys; reconcile unknown provider request IDs
before retry. Duplicate successful responses are retained as telemetry but one
frozen rule selects the canonical response without looking at labels.

If any required identity/hash differs, stop `PROTOCOL_INVALID` or preserve the
run as `INFRA_INCOMPLETE`; never merge results across profiles.

## 5. Report provenance

Every report includes the profile hash, run ID, terminal state, exact sample and
group counts, exclusions, code/runtime versions, resource usage, failures,
resumptions, and unresolved contamination risks. Claims must name the tested
construct and distinguish proxy results from agent-in-the-loop results.
