# Configuration

The files in this directory are non-secret examples. `run_config.example.yaml`
is directly usable only for the deterministic, network-free offline demo: its
approval names an offline fixture and both provider entries are stubs. It cannot
authorize a live provider, GitHub access, a full evaluation, or code execution.

1. For an offline demo, use `run_config.example.yaml` unchanged.
2. For any real run, copy it to the controller's run directory, change mode,
   run ID, providers, deadline, and set `approval.confirmed: false` first.
3. Replace every example limit using the user's hard limits and the measured
   vertical-slice cost/latency envelope.
4. Pin the actual `requirements.lock` SHA-256 and the fingerprint of the
   protected launcher's HMAC key. The key itself never belongs in YAML.
5. Recompute stage allocations and closeout reserves so worst-case in-flight
   work and a complete final test fit.
6. Validate against `../schemas/run_config.schema.json`.
7. Have the user inspect the canonical bytes/hash, then set `approval.confirmed`
   and confirmation identity/time. The controller must record a new hash.
8. Treat the sandbox capability manifest as static policy. Generate the actual
   `runtime_capability_report` from probes; do not copy its example and claim it
   describes the environment.
9. Resolve every profile artifact hash to actual bytes and freeze
   `evaluation_profile` only after all pre-test gates pass.

The example numbers are intentionally small probe caps, not recommendations for
the full Nexus-Editor evaluation and not evidence that the benchmark is powered.
The demo's fixture confirmation is valid only when the controller verifies
`mode: demo`, `provider: deterministic_stub`, and no network. For a real run,
`approval.confirmed: false` is the required starting state and a hard fail-closed
control until the user confirms the final canonical manifest.

Never store credentials, tokens, launcher HMAC keys, secret values, protected labels, or
raw review comments in these configuration files.
