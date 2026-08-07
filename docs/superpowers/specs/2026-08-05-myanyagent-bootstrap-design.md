# Design: Portable MyAnyAgent Git Bootstrap

Date: 2026-08-05

## Goal

Make the repository-local MyAnyAgent commit identity and GitHub App HTTPS
authentication reproducible on another device without migrating `.git/`, a
private key, or any short-lived token.

## Tracked Components

- `scripts/myanyagent-credential-helper.cjs` implements the Git credential
  protocol and mints a repository-scoped installation token on demand.
- `scripts/bootstrap-myanyagent.sh` validates prerequisites and reconstructs
  this checkout's local Git configuration idempotently.
- `GITHUB_APP_AUTH.md` documents the bootstrap command and external private-key
  prerequisite.

## Local Configuration

The bootstrap writes only `.git/config` values for this checkout:

- `user.name=MyAnyAgent[bot]`
- `user.email=312959697+myanyagent[bot]@users.noreply.github.com`
- `user.useConfigOnly=true`
- `commit.gpgsign=false`
- `credential.useHttpPath=true`
- `myanyagent.privateKey=<external local path>`
- an empty credential-helper entry followed by the tracked helper command

Global Git configuration and unrelated repositories remain unchanged.

## Security Boundaries

The helper accepts only the target HTTPS host and repository. The private key
must be provisioned separately on each device and is never copied into the
repository. Installation tokens remain in process memory and are returned only
to Git's credential consumer.

The bootstrap fails closed when Git, Node.js, the expected remote, or the
private key is missing. It performs a sanitized credential smoke test without
printing the token.

## Verification

- Shell and Node syntax checks pass.
- Bootstrap is safe to run repeatedly.
- The target credential request returns `x-access-token` and a short-lived
  token without exposing its value.
- A different repository is rejected.
- Local identity and helper configuration are correct while global settings are
  unchanged.
- The bootstrap files and documentation are committed and pushed as bot
  commits.
