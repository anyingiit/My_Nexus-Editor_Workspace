# V3 architecture

V3 is a small, fail-closed experiment harness, not a self-contained workflow
engine and not a promise that prose alone can create a security boundary. It
separates four kinds of responsibility:

1. `AGENTS.md` is the short constitution loaded by semantic workers.
2. `protocol/`, `config/`, and `schemas/` define the frozen experiment contract.
3. `src/agents_v3/` enforces deterministic state, budget, artifact, context, and
   evaluation rules.
4. `sandbox/` is the outer process boundary for untrusted code. A preflight
   failure blocks execution; it never degrades to running repository tools on
   the host.

## Trust boundaries

```text
GitHub (untrusted, read-only)
        |
        v
collector -> sanitized public inputs ---------> candidate / judge
        |                                           |
        v                                           v
protected labels ----------------------------> evaluator / scorer
                                                    |
                                                    v
                                      aggregate diagnostics only
```

The collector is the only role that may have GitHub network access. Candidate
workers and judges receive neither credentials nor protected label mounts. The
final-test evaluator can read the frozen test labels and evaluation profile but
cannot modify the candidate. Directory conventions and capability manifests
make violations detectable; OS identities, containers, or service boundaries
must supply the actual access control.

## Deterministic control path

The SQLite store is the single writer for task and budget state. A scheduler
claims a READY task with a lease, records completion events before evaluating
budget termination, and recovers expired RUNNING tasks idempotently. Budget
reservations charge the worst case before dispatch so concurrent requests
cannot oversubscribe a limit. Actual usage commits against the reservation;
unused capacity is released.

Artifacts begin in staging, pass deterministic gates first, and are promoted
atomically only when their recorded digest matches the file. Semantic artifact
evaluation is optional and separate from protected benchmark scoring.

## Evaluation path

The historical-PR benchmark is explicitly an offline proxy for policy
extraction. It does not establish that an AGENTS file improves code written by
an agent. That claim requires the separately specified agent-in-the-loop paired
experiment.

Each offline sample binds its label event to the head/base snapshot at that
event. Administrative outcomes are not converted into code-quality rejection
labels. Time splits operate on event time and grouped near-duplicates under one
knowledge cutoff.

Final test is a one-way barrier. Candidate, dataset, prompt, model settings,
packer, rubric, harness, and container identities are hashed into an immutable
profile, and every non-derived hash is rechecked against resolved bytes. The
runtime report is HMAC-authenticated by the protected launcher key whose
fingerprint was approved in the run config. A valid negative result is terminal; an infrastructure interruption
may resume only missing work under that same profile.

## Anytime closeout

Every accepted dev iteration may update `best-so-far`; the last candidate is
never assumed to be the best. At a soft limit, new exploration stops. The
closeout reserve can only freeze the best candidate, emit an anytime report,
and, if the pre-reserved final-test budget is sufficient, perform the one final
test. Insufficient final-test budget produces a truthful untested intermediate
result without crossing the barrier.
