# Run Lifecycle, Budget, and State Protocol

Protocol version: `3.0.0`

## 1. Bootstrap and confirmation

`run_config` is authoritative only after schema validation and explicit user
confirmation. Templates and examples are never implicitly runnable. Bootstrap
fails closed if a required value is null/zero/negative, a deadline is expired,
a provider cap exceeds total cap, reserves exceed totals, stage allocations do
not fit, or `approval.confirmed` is false.

The controller records the exact config bytes/hash and approval timestamp. Any
material change creates a new run manifest and approval; it cannot mutate an
active final-test profile.

## 2. Accounted resources

Track independently:

- wall-clock time and absolute deadline;
- total and per-stage LLM tokens;
- incremental cash charged by the run;
- provider cash/credit or subscription quota consumption;
- external API/tool calls and rate-limit windows;
- worker/evaluator execution slots.

One scalar “cost” is insufficient. Unknown price or usage is not zero; block
the call or reserve the configured worst case.

## 3. Atomic reservation

Before dispatch, a single transactional budget authority reserves worst-case
tokens, incremental cash, provider quota, tool calls, and bounded wall time.
Admission requires:

`spent + in_flight_reserved + requested <= hard_limit - protected_closeout`

for every relevant resource. Record reservation ID, task/idempotency key,
provider/model, worst case, expiry, and stage. Concurrent callers cannot
observe or spend the same capacity. On completion reconcile actual usage and
release the unused portion. Expired in-flight reservations require request-ID
reconciliation before release; never assume an unknown request was free.

Closeout reservation is separately protected. Only final report/freeze and, if
fully affordable, the frozen final test may consume it.

## 4. Watermarks and anytime behavior

At the soft watermark, stop broad evidence mining, speculative variants, and
low-value semantic evaluation. Continue only the shortest path to a validated
best-so-far, reproducible report, and reserved final test.

At closeout entry:

1. stop new exploration dispatch;
2. reap, reconcile, and persist all completed/in-flight events;
3. recover or cancel stale leases according to frozen policy;
4. select the best eligible dev candidate, not the latest;
5. freeze candidate/profile if all pre-test gates pass;
6. reserve the full final test atomically or report `test_not_executed`;
7. produce the anytime/terminal report.

Never spend into a hard limit hoping a later cache/refund will appear.

## 5. Task state machine

Canonical task states:

`PENDING -> READY -> RUNNING -> VERIFYING -> DONE`

Alternate terminal/branch transitions:

- `RUNNING -> RETRY_WAIT -> READY` only for a classified retryable failure and
  within retry/budget limits;
- `RUNNING|VERIFYING -> SPLIT_REQUIRED -> SUPERSEDED` after child task packets
  are atomically created; the parent never becomes `DONE`;
- `RUNNING -> STALE -> READY` after lease expiry and reconciliation;
- any nonterminal state -> `CANCELLED` on safe closeout;
- any nonterminal state -> `BLOCKED` only when external input/authority is
  required and no safe path remains;
- `VERIFYING -> FAILED` for nonretryable artifact failure.

Dispatch atomically removes a task from `READY`, records `RUNNING`, lease owner,
lease expiry, attempt, idempotency key, and budget reservation. Deterministic
executors and LLM workers emit the same completion-event contract.

Before budget or terminal decisions, reap and persist completed events. Parent
dependencies are satisfied only by promoted `DONE` artifacts, never by a worker
claim or staging file.

## 6. Ledger and recovery

The controller is the sole canonical writer. Every event has schema version,
run ID, strictly increasing sequence, timestamp, previous-event hash, payload
hash, actor role, and idempotency key. Write atomically with fsync/checksum or a
transactional database. Snapshots are derived and rebuildable from events.

On restart:

1. verify event hash chain and schema versions;
2. rebuild tasks, reservations, leases, artifacts, and best-so-far pointer;
3. reconcile provider request IDs and filesystem manifests;
4. mark expired work `STALE`, then retry only if idempotent and budgeted;
5. resume final test only when run/profile hashes exactly match.

Status freshness is bounded by `status.max_staleness_seconds`; write after every
state transition and provider call, not only periodic summaries.

## 7. Progress and best-so-far

Progress is derived from countable eligible artifacts and task states. Dynamic
DAG growth may change totals, so never present a single percentage without
denominator/version. Report:

- discovered/eligible/excluded/unknown/snapshot success and failure;
- task counts by state and critical path;
- current and best candidate hashes with dev metrics/deltas;
- spent, in-flight, remaining, burn rate, and closeout reserve for each budget;
- provider/model, request IDs, error classes, latency P50/P95, cache hit rate;
- last verified progress, bottleneck, ETA interval, marginal gain, and closeout
  state.

Create a deliverable v0 candidate after the vertical slice and update an anytime
report on every promotion. A crash or hard stop must still leave a useful result.

## 8. Iteration and concurrency

Each dev experiment records a falsifiable hypothesis, parent/candidate hashes,
change set, primary and safeguard deltas, cost, elapsed time, and failure class.
Stop at `max_dev_iterations`; also stop after `low_gain_patience` consecutive
valid iterations whose improvement is below `min_dev_gain_pp`, or when the
configured cost-per-point/expected-value rule rejects another iteration.

Concurrency is bounded by actual execution slots, ready independent tasks,
provider/API limits, observed failure rate, and atomic reservations. Evaluator
work consumes a declared slot. Reduce concurrency on rate limits, elevated
failure, or approaching watermarks; do not mechanically target a fixed count.

## 9. Error taxonomy and terminal state

Provider telemetry distinguishes `TRANSPORT`, `RATE_LIMIT`, `HTTP_4XX`,
`HTTP_5XX`, `TIMEOUT`, `EMPTY_RESPONSE`, `TRUNCATED`, `CONTEXT_OVERFLOW`,
`JSON_INVALID`, `SCHEMA_INVALID`, `SAFETY_REFUSAL`, and `SEMANTIC_INVALID`.
Retain provider request ID, latency, token/charge estimate, attempt, and retry
trace without logging secrets or protected truth.

Run terminal states are `SUCCESS`, `VALID_TEST_FAILED`, `INCONCLUSIVE`,
`INFRA_INCOMPLETE`, `PROTOCOL_INVALID`, `BUDGET_EXHAUSTED`, and `BLOCKED`.
Final reports additionally state whether test was never opened, partially
completed/resumable, or validly completed.

