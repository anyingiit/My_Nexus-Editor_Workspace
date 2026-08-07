# Dataset and Event Contract

Protocol version: `3.0.0`

## 1. Unit of observation

The decision benchmark's unit is a **qualified maintainer review event**, not a
PR's eventual disposition. A PR may contain several reviews, but the default
decision cohort uses the earliest event that satisfies this contract so that a
single PR cannot dominate the benchmark. Alternative event-selection policies
must be preregistered and must keep all events from one PR in one split group.

An eligible label event is a submitted GitHub review whose state is exactly
`APPROVED` or `CHANGES_REQUESTED`, made by a human maintainer whose authority at
the event time is mechanically supported. `COMMENTED`, merge, close, reopen,
label, assignment, and author actions are not decision labels.

For each sample, bind the label to the code reviewed by that event:

- `head_sha_at_event` is the review's submitted commit ID when available;
- otherwise it is the latest head SHA strictly no later than the event
  timestamp, with the reconstruction method and evidence recorded;
- `base_sha_at_event` is the base branch SHA paired with that head state;
- candidate-visible diff, commits, file metadata, and checks are reconstructed
  for that exact base/head pair and strictly as known at the event timestamp;
- later commits, later checks, later reviews, merge state, and close state are
  not candidate or judge input.

If either SHA or its historical snapshot cannot be reconstructed without
ambiguity, exclude the sample with `SNAPSHOT_UNRECONSTRUCTABLE`.

## 2. Required sample fields

Every record validates against `schemas/sample.schema.json` and includes:

```yaml
sample_id: stable opaque identifier
repository_id: stable opaque repository identifier
pull_request_group_id: opaque PR-level group identifier
label_event_id: immutable upstream review event identifier
label_event_type: APPROVE | REQUEST_CHANGES
label_timestamp: RFC 3339 timestamp
head_sha_at_event: 40-character commit SHA
base_sha_at_event: 40-character commit SHA
maintainer_identity_basis: CODEOWNERS | org_role | repository_permission | frozen_allowlist
maintainer_evidence_as_of: RFC 3339 timestamp
eligibility_cohort: decision | rationale | administrative | excluded | unknown
eligibility_reason: controlled reason code
exclusion_reason: controlled reason code or null
split_group_id: near-duplicate/related-work group
snapshot_manifest_sha256: hash of candidate-visible historical snapshot
ground_truth_manifest_sha256: protected label/rubric manifest hash
```

Raw account names and PR numbers are retained only in the isolated provenance
vault. Candidate/judge records use opaque IDs.

## 3. Maintainer identity

Authority must be established as of the label event, using the first available
mechanical basis in this order:

1. repository permission snapshot showing `maintain` or `admin`;
2. organization/team role snapshot linked to repository maintainership;
3. cutoff-valid CODEOWNERS or frozen maintainer allowlist with provenance.

Current permissions must not be projected backward without evidence. Bot-only,
unverifiable, or self-review events are excluded. The collector may write raw
identity evidence into quarantine but may not interpret protected labels.

## 4. Cohorts

- **decision**: eligible `APPROVE` or `REQUEST_CHANGES` event with reconstructable
  pre-event snapshot and verified maintainer identity.
- **rationale**: a decision sample with at least one independently normalized,
  scoreable issue point in protected review evidence. Approval samples may have
  an empty rationale and are not included in rationale recall denominators.
- **administrative**: duplicate, spam, superseded, abandoned, author-closed,
  bot-only closure, or merge/close without a qualified review. Report counts
  and reasons; do not treat these as code-quality rejects.
- **excluded**: a deterministically known ineligibility reason.
- **unknown**: missing/ambiguous authority, event, or snapshot evidence. Unknown
  records never enter accuracy denominators.

One record may be in `decision` and separately flagged `rationale_eligible`;
the canonical `eligibility_cohort` stores its primary cohort.

## 5. Cheap census

The census precedes snapshot downloads and LLM work. Fetch only PR/review event
metadata needed to compute:

- discovered PR and review counts;
- candidate decision events by class and event month;
- administrative, excluded, and unknown counts by controlled reason;
- authority-evidence availability;
- preliminary related-work grouping signals;
- estimated snapshot retrieval success from a small deterministic probe.

The census emits an `eligibility_manifest` and its checksum. Expensive diffs,
checks, comments, and historical repository trees are collected only for
eligible or explicitly sampled diagnostic records.

## 6. Split and leakage control

Before splitting, cluster records that share any of: PR, issue, branch/stack,
revert chain, cherry-pick relationship, patch fingerprint, or high-similarity
normalized diff. The `split_group_id`, grouping algorithm/version, threshold,
and manual adjudications are frozen.

Sort groups by the maximum label-event timestamp in each group and allocate
whole groups chronologically to train/dev/test according to the frozen split
ratios. Boundary groups never straddle splits. Emit actual counts and class
rates; do not force ratios by moving future groups backward.

The knowledge cutoff is the maximum event timestamp included in train after
group assignment. Candidate-visible repository files, configs, contributing
guides, direct commits, third-party references, derived rule sources, and annex
retrieval corpora must be available no later than that cutoff and be pinned to
commit SHA or SHA-256. Dev/test repository evolution is benchmark input only,
not candidate-construction evidence.

## 7. Ground-truth isolation

Protected ground truth contains label events, raw reviews/comments, normalized
rubric points, maintainer identities, and eventual PR outcomes. Store train,
dev, and test truth in distinct mounts/ACL domains. The visible snapshot must
not contain review text, label state, PR URL/number, merge/close result, reviewer
identity, or metadata that trivially reveals the label.

Dev evaluation returns only the fixed aggregate/controlled diagnostic schema.
It never returns raw comments, sample-level labels, rubric wording, or stable
identifiers to candidate workers. Test truth is write-once after collection and
readable only by the final evaluator.

## 8. Snapshot and CI treatment

Historical checks are replayed deterministically as snapshot facts. Check names,
status, conclusion, timestamp, and provenance must be no later than the label
event. CI replay accuracy measures dataset reconstruction, not candidate value,
and is excluded from the candidate success score.

Report decision metrics on the full decision cohort and separately on the
subset whose deterministic aggregate CI status is `CI_PASS`. Actual code
execution is outside this dataset protocol and remains disabled unless the
untrusted-code runner gate is explicitly approved.

