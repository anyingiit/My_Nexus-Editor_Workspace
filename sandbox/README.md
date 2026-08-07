# Untrusted-code runner

PR code and repository configuration are untrusted. `lint`, `format`, and
`typecheck` may load executable plugins, so they use the same boundary as tests.
Execution is disabled unless Stage 0 validates the capability manifest and a
digest-pinned runner image is available.

## Build and run

Build from a reviewed, digest-pinned base image. The digest must also appear in
the frozen evaluation profile:

```sh
podman build \
  --build-arg BASE_IMAGE='registry.example/safe-runner@sha256:<64-hex-digest>' \
  -t local/agents-v3-runner \
  -f sandbox/Containerfile.runner .
```

Resolve the built image to an immutable digest, create a dedicated output
directory, then invoke:

```sh
CONTAINER_ENGINE=podman sandbox/run-untrusted.sh \
  'registry.example/agents-v3-runner@sha256:<64-hex-digest>' \
  /absolute/path/to/read-only-source \
  /absolute/path/to/dedicated-output \
  /bin/sh -lc 'npm test'
```

The default entrypoint allowlist is `/bin/sh,/usr/bin/env`. Tighten or extend
it explicitly and set the measured limits from the approved run manifest:

```sh
RUNNER_COMMAND_ALLOWLIST='/bin/sh' \
RUNNER_WALL_SECONDS=600 \
RUNNER_DISK_MB=512 \
RUNNER_OUTPUT_MB=64 \
CONTAINER_ENGINE=podman sandbox/run-untrusted.sh ...
```

`RUNNER_PREFLIGHT_ONLY=1` performs path, digest, command, and numeric-limit
checks without starting an engine. It is a diagnostic only and never counts as
a passed runtime capability probe.

The launcher fails unless the engine is rootless. It sets no network, a
read-only root filesystem, non-root UID, dropped capabilities, no-new-privilege,
resource limits, an isolated temporary filesystem, read-only source, and a
single dedicated writable output mount. It never mounts Docker/Podman sockets,
host homes, GitHub/model credentials, or protected labels.

The launcher rejects source/output ancestor overlap, because a nested writable
bind can alias the supposedly read-only tree. An outer Python watchdog enforces
wall time and total output bytes; the only anonymous writable filesystem is a
size-limited `/tmp`. If the watchdog or a supported rootless engine is absent,
the job fails closed.

Container flags do not create train/dev/test isolation by themselves. Run the
collector, miners, workers, judge, and evaluators as separate identities or
containers with only the paths declared in
`capability_manifest.example.json`. Validate that manifest with
`agents_v3.security.assert_capability_manifest` before any collection or model
call. Prompt delimiters remain defense in depth; they are not an access-control
boundary.
