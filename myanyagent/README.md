# MyAnyAgent — Reusable GitHub App Git Auth Tool

A machine-level tool that authenticates `git push` using a GitHub App
installation token, reusable across any repository authorized under the
same App. No per-repo scripts; each repo just commits a small
`.myanyagent.toml` declaring its parameters.

## Install (per machine, once)

```sh
# From a clone of this repo:
sh myanyagent/install.sh
```

This installs the tool to `~/.local/share/myanyagent/`, creates symlinks
in `~/.local/bin/`, and writes `~/.config/myanyagent/config.toml`.

Prerequisites: `git`, `node` 18+, and the GitHub App private key at
`~/.secrets/myanyagent.<date>.private-key.pem` (provision separately,
never commit).

## Per-Repo Setup

1. Add the repository to the GitHub App's selected repositories in App
   settings.
2. Create `.myanyagent.toml` at the repo root:

   ```toml
   repository = "owner/repo"
   installation_id = "123456"
   [bot]
   name = "MyAnyAgent[bot]"
   email = "123456789+myanyagent[bot]@users.noreply.github.com"
   ```

3. Commit it.
4. Run `myanyagent-bootstrap` in the repo.
5. `git push` to verify.

## Agent Discovery (how AI agents learn the tool)

The tool is designed for agent use with zero prior knowledge:

- If a repo has `.myanyagent.toml`, it declares MyAnyAgent usage.
- On `git push` auth failure, the credential helper prints a remediation
  hint on stderr: `-> run: myanyagent-status`.
- `myanyagent-status` reports state and prints `-> run: <command>` when
  action is needed (or `OK` when all green).
- The agent follows the `-> run:` lines to self-heal.

No command memorization required; the tool self-describes the next step.

## Commands

| Command | Purpose |
|---------|---------|
| `myanyagent-bootstrap` | Configure local `.git/config` from `.myanyagent.toml` + machine config |
| `myanyagent-status` | Read-only state report with `-> run:` hints |
| `myanyagent-helper` | Git credential helper (called by git, not directly) |

## Tests

```sh
node --test myanyagent/test/helper.test.cjs
sh myanyagent/test/bootstrap.test.sh
sh myanyagent/test/status.test.sh
```