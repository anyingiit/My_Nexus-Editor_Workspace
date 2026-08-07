# Security and Access-Control Protocol

Protocol version: `3.0.0`

## 1. Threat model

Treat PR titles/bodies, issues, diffs, repository files, configs, package
metadata, generated artifacts, review text, and third-party documents as
untrusted. Threats include prompt injection, secret exfiltration, malicious
build hooks, dependency scripts, path traversal, symlink escapes, resource
exhaustion, label leakage, external mutation, and privilege inheritance.

Delimiters and text saying “ignore instructions” are defense in depth only.
Security depends on process identity, least-privilege mounts/ACLs, network
policy, credential absence, a static capability manifest, and a probed runtime
capability report authenticated by a protected launcher key.

## 2. Enforced access matrix

| Role | Train truth | Dev truth | Test truth | GitHub network | Candidate write | External write |
|---|---:|---:|---:|---:|---:|---:|
| collector | quarantine-write only | quarantine-write only | quarantine-write only | read-only allowlist | no | no |
| train evidence miner | read | no | no | no | staging only | no |
| candidate worker | minimum derived train evidence | no | no | no | staging only | no |
| judge | no | no | no | no | no | no |
| artifact evaluator | no | no | no | no | promotion decision only | no |
| dev evaluator | no | read | no | no | no | no |
| final evaluator | no | no | read | no | no | no |
| untrusted-code runner | no | no | no | none | dedicated output only | no |

“No” means the path is not mounted and the service credential cannot request
it. Read-only filesystem permissions alone are insufficient if public GitHub
can reveal the protected review; roles without GitHub permission receive no
GitHub/network tool.

## 3. Process boundaries

- Collector authenticates through a read-only proxy scoped to the exact
  repository and approved API methods. It writes raw responses to quarantine;
  a deterministic sanitizer creates downstream records.
- Candidate/analyzer processes mount only their task packet, cutoff-valid
  inputs, and dedicated staging directory. No host home, repository credentials,
  truth vault, or broad project tree is mounted.
- Judge is a text-in/JSON-out service identity with no tools, network, secrets,
  filesystem, or conversational memory across samples.
- Dev and final evaluators use different identities and truth mounts. Their
  output schema rejects raw comments, raw labels, stable protected IDs, and
  free-form excerpts.
- Artifact evaluation is distinct from protected benchmark evaluation.

## 4. Untrusted code

Untrusted code execution is `disabled` by default. Replay of stored CI metadata
does not execute code. If the run manifest explicitly enables execution, the
capability gate must demonstrate:

- rootless disposable container or VM;
- `network=none` and empty proxy variables;
- no secrets, SSH agent, cloud metadata access, GitHub/model credentials, host
  runtime socket, device mounts, or privileged capabilities;
- read-only pinned source and dedicated size-limited output mount;
- no-new-privileges, dropped capabilities, non-root UID, read-only rootfs, and
  bounded CPU, memory, PIDs, disk, output bytes, and wall time;
- sanitized environment and explicit command allowlist;
- teardown after every sample and hash-verified artifact egress.

`lint`, `format`, `tsc`, package managers, build systems, and test discovery can
load executable configuration/plugins. They are untrusted-code execution and
must never run on the host.

## 5. Static policy and runtime capability report gate

The static `capability_manifest` defines the default-deny role policy and
validates against `schemas/capability_manifest.schema.json`. Stage 0 then emits
a separate `runtime_capability_report` validating actual, not intended,
capabilities. Every mount records a stable `source_id`, class, target and access
mode so a truth vault cannot be swapped behind an otherwise identical path. It must
record role identity, executable, image digest, mounts with modes, network
policy, allowed API hosts/methods, environment variable names (never values),
secret mounts, sockets/devices, resource limits, writable paths, and probe
results. Validate the runtime report against
`schemas/runtime_capability_report.schema.json`.

The approved run config contains only the SHA-256 fingerprint of the launcher
attestation key. The launcher keeps the raw key outside candidate, judge and
untrusted-runner mounts and emits an HMAC-SHA256 over the canonical report,
including run/config identity, capability policy hash, image digests,
measurements, generation time and a single-use nonce. The controller verifies
that HMAC before final readiness and persists it in the readiness receipt.
Content self-hashes alone are integrity checks, not provenance.

Required negative probes verify that candidate/judge cannot resolve or reach
GitHub, truth paths are absent, external writes fail, the runtime socket is
absent, and path/symlink escapes fail. Never probe by displaying secret values.

If the harness cannot enforce any required boundary, fail before data download
or LLM calls with `PROTOCOL_INVALID` and instruct the operator to use an outer
sandbox/runner. Periodic human approval is not a substitute.

## 6. Sanitization and artifact promotion

Reject absolute paths, `..`, device paths, unexpected symlinks, special files,
and archives that escape their destination. Normalize text as data while
retaining an immutable raw checksum in quarantine. Apply size/count limits
before parsing. Candidate-visible content passes leakage scans for labels,
review text, URLs/PR IDs, reviewer identities, and post-event fields.

Artifacts are written to staging, validated deterministically, checksummed, and
atomically promoted by the controller. Workers never write canonical state.

## 7. Incident response

On suspected leakage, secret exposure, sandbox escape, profile mutation, or
external write capability: stop new work, revoke affected credentials outside
the run, preserve telemetry without secret contents, quarantine artifacts, and
end `PROTOCOL_INVALID`. Do not continue merely because outputs look plausible.
