# Design: Repository-Local MyAnyAgent Commit Identity

Date: 2026-08-04

## Goal

Make normal Git commits in this repository resolve to the GitHub App bot
`MyAnyAgent[bot]`, while keeping all configuration and authentication changes
local to this repository and correcting the already-pushed `main` commit.

## Commit Identity

Set repository-local Git configuration to:

- Name: `MyAnyAgent[bot]`
- Email: `312959697+myanyagent[bot]@users.noreply.github.com`
- `user.useConfigOnly: true`
- `commit.gpgsign: false`

The bot email is derived from the confirmed GitHub bot user ID `312959697`.
Human GPG signing is disabled only for this repository because the available
signing key belongs to `anyingiit`, not to the bot.

## App Authentication

Create a machine-local credential helper under `.git/`. It handles only the
target GitHub host and repository, generates a short-lived installation token
from the external private key on demand, and returns it to Git through the
standard `x-access-token` credential protocol. Repository path matching is
enabled locally so the helper can reject other repositories. Tokens never enter
the working tree, Git config, credential store, or remote URL.

## History Correction

Amend the current `main` tip with `--reset-author --no-gpg-sign`, preserving its
parent, message, and tree. Update the remote with an explicit
`--force-with-lease` for the observed remote SHA. After the history correction,
publish the approved identity documentation as a separate bot-authored commit.
Abort if the remote changed.

## Scope and Safety

- All Git identity and credential-helper configuration is repository-local.
- The helper refuses non-target GitHub hosts or repositories.
- Existing global Git identity, signing, and credential settings remain
  unchanged.
- The old commit SHA is replaced on `main`; no remote branch other than
  `main` is changed.

## Verification

- Local author and committer both resolve to `MyAnyAgent[bot]` and the bot email.
- The amended commit has no human GPG signature and has the expected parent and
  tree.
- The App helper can push using an installation token without persisting it.
- GitHub's commit page maps the new SHA to `myanyagent[bot]`.
- Global Git configuration is unchanged and unrelated repositories do not see
  the repository-local settings.
