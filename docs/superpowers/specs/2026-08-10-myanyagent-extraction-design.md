# Design: Extract MyAnyAgent Into a Reusable Machine-Level Tool

Date: 2026-08-10

## Goal

Extract the GitHub App authentication logic currently embedded in this
repository (`scripts/bootstrap-myanyagent.sh`,
`scripts/myanyagent-credential-helper.cjs`) into a reusable machine-level
tool that serves any repository authorized under the same GitHub App
(`MyAnyAgent`), eliminating per-repository script duplication.

## Background

The current setup works well but is project-scoped:

- The credential helper hardcodes `CLIENT_ID`, `INSTALLATION_ID`,
  `REPOSITORY`.
- The bootstrap script hardcodes `target_url` and the bot identity.
- Switching to another repository requires copying both scripts and
  editing the hardcoded values.

First-principles analysis: GitHub App authentication is "sign a JWT with
a private key, exchange it for an installation token, feed the token to
git." Only four values vary per repository: `repository`,
`installation_id`, bot `name`, and bot `email`. The signing and token
exchange logic is invariant and is what should be extracted.

## Decisions

- **Scope**: a single GitHub App (`MyAnyAgent`) serving multiple
  repositories. One private key, one tool, many repos.
- **Deployment**: machine-level installation via `install.sh`
  (`curl | sh`). The tool lives under `~/.local/share/myanyagent/`;
  machine defaults live under `~/.config/myanyagent/`.
- **Per-repo config**: a committed `.myanyagent.toml` at the repository
  root describes that repo's parameters. Shared across devices and
  team members via git.
- **Agent discovery**: purely failure-driven. The tool does not write to
  `AGENTS.md` or any project documentation. When git push fails auth, the
  credential helper prints an executable remediation command on stderr,
  forming a self-describing recovery loop. `myanyagent-status` is the
  single read-only entry point that reports state and the next action.

## Architecture

Three layers, strictly separated:

1. **Invariant kernel** (machine-level tool, installed once, upgraded
   once): JWT signing, installation-token exchange, the git credential
   protocol. Located at `~/.local/share/myanyagent/bin/`.
2. **Variable parameters** (per-repo `.myanyagent.toml`, committed):
   `repository`, `installation_id`, bot identity.
3. **Private key** (external, never committed): `~/.secrets/` with
   restrictive permissions. Unchanged from the current setup.

### File layout

```
~/.local/share/myanyagent/
  bin/
    myanyagent-credential-helper.cjs   # invariant kernel
    myanyagent-bootstrap.sh            # generic bootstrap
    myanyagent-status.sh               # read-only self-check
  VERSION                              # tool version

~/.config/myanyagent/
  config.toml                          # client_id, app_id, default private key path

~/.secrets/myanyagent.<date>.private-key.pem   # unchanged

<each repo>/
  .myanyagent.toml                     # this repo's parameters, committed
```

### `.myanyagent.toml` format

```toml
repository = "anyingiit/My_Nexus-Editor_Workspace"
installation_id = "151195329"
[bot]
name = "MyAnyAgent[bot]"
email = "312959697+myanyagent[bot]@users.noreply.github.com"
```

Only per-repo values. No `client_id` (machine-level), no scripts, no
secrets. This file is safe to commit and is the opt-in signal that a
repo declares MyAnyAgent usage.

### `~/.config/myanyagent/config.toml` format

```toml
client_id = "Iv23lioD363YBpJJB9QE"
app_id = "4483813"
private_key = "~/.secrets/myanyagent.2026-08-04.private-key.pem"
```

These are the single-App shared invariants. `private_key` is the
default; the environment variable `MYANYAGENT_PRIVATE_KEY` overrides it
per invocation (preserved from the current bootstrap).

`client_id` is the JWT issuer (`iss` claim) and is the only value the
helper functionally uses. `app_id` is stored for reference and the
`/app` metadata endpoint; it is not used for token minting. Keeping it
avoids a future lookup when GitHub API responses reference the numeric
App ID.

## Components

### `myanyagent-credential-helper.cjs` (invariant kernel)

Invoked by git via the credential protocol. Behavior:

1. Read git config (local) keys: `myanyagent.repository`,
   `myanyagent.installationId`, `myanyagent.privateKey`. Read
   `client_id` from `~/.config/myanyagent/config.toml`.
2. Validate `fields.path` matches `myanyagent.repository` (prevents
   cross-repo token leakage). Exit 0 silently if the path does not
   match (git falls through to other helpers).
3. Load the private key, sign an RS256 JWT (`iat = now - 60`,
   `exp = now + 540`, `iss = client_id`).
4. POST to
   `https://api.github.com/app/installations/<installation_id>/access_tokens`
   with `repositories: [<repo-name>]` to mint a repository-scoped
   token.
5. On success, output `username=x-access-token\npassword=<token>`.
6. On failure, write to stderr a remediation hint:
   ```
   myanyagent: token mint failed (<reason>).
   -> run: myanyagent-status   (inspect)   or   myanyagent-bootstrap   (reconfigure)
   ```
   This is the agent discovery loop: the agent reads the `-> run:`
   line and executes it without needing prior knowledge of the tool.

The helper is parameterized via git config rather than environment
variables because git invokes credential helpers in a controlled
environment where git config is the most reliable channel. The
existing `localConfig()` pattern from the current helper is extended.

### `myanyagent-bootstrap.sh`

Run from the repository root with no arguments. Behavior:

1. Verify `git`, `node` are available; bail with a clear message if not.
2. Verify the working directory is a git repository and `origin` uses
   HTTPS.
3. Require `.myanyagent.toml` in the repo root; parse `repository`,
   `installation_id`, `bot.name`, `bot.email`.
4. Read `~/.config/myanyagent/config.toml` for `client_id` and the
   default `private_key` path. `MYANYAGENT_PRIVATE_KEY` overrides the
   private-key path if set (must be absolute).
5. Verify `origin` fetch and push URLs equal
   `https://github.com/<repository>.git` (reject running in the wrong
   repo).
6. Verify the private key is readable.
7. Write only to this checkout's `.git/config` (local, never global):
   - `user.name`, `user.email`, `user.useConfigOnly=true`,
     `commit.gpgsign=false`
   - `credential.useHttpPath=true`
   - `myanyagent.repository`, `myanyagent.installationId`,
     `myanyagent.privateKey`
   - Reset `credential.helper` and add the helper command using the
     absolute path
     `~/.local/share/myanyagent/bin/myanyagent-credential-helper.cjs`
     (no PATH dependency).
8. Smoke test: run `git credential fill` for the target repository and
   verify `username=x-access-token` and a non-empty token, without
   printing the token.
9. On success, print a confirmation. On failure, print
   `-> run: myanyagent-status`.

Idempotent: safe to run repeatedly; rewrites the same local config
keys.

### `myanyagent-status.sh`

Read-only self-check. Run from any repo with a `.myanyagent.toml`.
Output:

```
repo: anyingiit/My_Nexus-Editor_Workspace   (from .myanyagent.toml)
tool: installed v1 at ~/.local/share/myanyagent
private key: OK   ~/.secrets/myanyagent.2026-08-04.private-key.pem
client_id: Iv23lioD363YBpJJB9QE   (from ~/.config/myanyagent/config.toml)
git config: configured
OK
```

When not yet bootstrapped:

```
repo: anyingiit/My_Nexus-Editor_Workspace   (from .myanyagent.toml)
tool: installed v1 at ~/.local/share/myanyagent
private key: OK   ~/.secrets/myanyagent.2026-08-04.private-key.pem
client_id: Iv23lioD363YBpJJB9QE   (from ~/.config/myanyagent/config.toml)
git config: NOT configured
-> run: myanyagent-bootstrap
```

The trailing `-> run:` line is the agent's action signal. When
everything is OK, no `-> run:` line is emitted. This is the single
entry point an agent can always call to learn the next step. The
`-> run:` commands use bare names (`myanyagent-bootstrap`) relying on
`~/.local/bin` being on `PATH`. If status detects the tool is installed
but the symlinks are missing or `~/.local/bin` is not on `PATH`, it
falls back to the absolute path
(`~/.local/share/myanyagent/bin/myanyagent-bootstrap`) in the hint so
the command is always directly executable.

### `install.sh`

Hosted in a dedicated distribution location (a raw GitHub URL or a
dedicated `myanyagent` GitHub repo's `install.sh`). Distributed via
`curl | sh`. Installs the three scripts to
`~/.local/share/myanyagent/bin/`, writes a `VERSION` file, and creates
`~/.config/myanyagent/config.toml` with the shared `client_id`,
`app_id`, and a default `private_key` path (prompting or defaulting to
`~/.secrets/myanyagent.2026-08-04.private-key.pem`). Idempotent;
re-running upgrades the tool and preserves the config unless
`--reset-config` is passed.

Also installs `myanyagent-status`, `myanyagent-bootstrap`, and
`myanyagent-helper` symlinks in `~/.local/bin/` pointing into
`~/.local/share/myanyagent/bin/`, so the bare command names in
`-> run:` hints are executable (assuming `~/.local/bin` is on `PATH`,
which is standard on Linux). If `~/.local/bin` is not on `PATH`, the
install script prints a one-line reminder to add it.

Does not touch any repository, any global git config, or the private
key itself.

## Agent Discovery Loop

The tool is designed for agent use without prior knowledge. The
recovery loop is self-describing:

```
Agent clones a repo with .myanyagent.toml (does not know its meaning yet)
  -> git push fails authentication
  -> helper stderr: "myanyagent: ... -> run: myanyagent-status"
  -> Agent runs myanyagent-status
  -> status output: "... git config: NOT configured -> run: myanyagent-bootstrap"
  -> Agent runs myanyagent-bootstrap
  -> push succeeds
```

The agent never memorizes any myanyagent command. Every failure and
every status check emits the exact next command as a `-> run:` line.
This is the discovery contract.

If the machine-level tool is not yet installed, `git credential fill`
falls through to the default credential chain and git's own error
appears. In that case the `.myanyagent.toml` filename in the repo is a
human/agent hint to install the tool. `install.sh` URL is documented
in a top-level `README.md` snippet inside the repo (human-maintained,
not auto-written by the tool).

## Error Handling

- **Private key missing or unreadable**: `bootstrap` and `status` both
  report the expected path clearly; `helper` failure message includes
  `-> run: myanyagent-bootstrap`.
- **`origin` URL mismatch with `.myanyagent.toml`**: `bootstrap`
  refuses, prints both URLs and tells the user to check the remote.
- **Token mint 401/403**: `helper` prints GitHub's returned `message`
  plus the remediation hint.
- **Tool not installed**: git falls back to the default credential
  chain; the `.myanyagent.toml` file and a human-maintained README
  snippet point to the `install.sh` URL.
- **Wrong repo in git config**: `helper` validates `fields.path`
  against `myanyagent.repository` and exits 0 silently, letting git
  try other helpers (prevents cross-repo token leakage).

## Migration From Current Setup

For `My_Nexus-Editor_Workspace` (the existing repo):

1. Run `install.sh` once on the machine (installs the tool and writes
   `~/.config/myanyagent/config.toml`).
2. Create `.myanyagent.toml` at the repo root from the values currently
   in `GITHUB_APP_AUTH.md` and the hardcoded scripts. Commit it.
3. Remove `scripts/bootstrap-myanyagent.sh`,
   `scripts/myanyagent-credential-helper.cjs`, and `GITHUB_APP_AUTH.md`
   (parameters now live in `.myanyagent.toml`; documentation migrates
   to a short README snippet pointing at the `install.sh` URL).
4. Run `myanyagent-bootstrap` to rewrite `.git/config`.
5. `git push` to verify.

For a new repository (e.g. `Playground` if later authorized under the
same App):

1. Add the repository to the GitHub App's selected repositories in
   the App settings.
2. Place `.myanyagent.toml` at the repo root with that repo's
   `repository`, `installation_id`, and bot identity. Commit.
3. Run `myanyagent-bootstrap`.
4. `git push` to verify.

## Testing Strategy

- **`myanyagent-credential-helper.cjs`**: unit tests for JWT structure
  (header/payload/signature), token-exchange mock (stub the GitHub API
  response), and `fields.path` validation (rejects mismatched repos,
  silently exits for unrelated hosts).
- **`myanyagent-bootstrap.sh`**: integration test in a throwaway git
  repo with a stub `.myanyagent.toml` and a mock private key; assert
  the correct `.git/config` keys are written and the smoke test
  passes. Assert idempotency on a second run. Assert rejection when
  `origin` does not match the toml.
- **`myanyagent-status.sh`**: test all state combinations (tool
  missing, private key missing, not bootstrapped, bootstrapped) and
  assert the `-> run:` line is present exactly when action is
  required and absent when OK.
- **`install.sh`**: assert idempotency (re-run upgrades tool, preserves
  config); assert `--reset-config` regenerates the config file.

## Security Boundaries

- The private key is never copied into any repository and never
  printed.
- Installation tokens live only in process memory and are returned
  only to git's credential consumer.
- The helper validates `fields.path` against the configured repository
  to prevent cross-repo token leakage.
- `bootstrap` writes only to the local checkout's `.git/config`; global
  git config and other repositories are untouched.
- The `client_id` and `app_id` are not secrets (they are public App
  identifiers) and live in the machine-level config file, not in any
  committed repo file.
- Tokens are short-lived (max 10 minutes) and scoped to a single
  repository via the `repositories` field in the token request.

## Out Of Scope

- Multi-App support (different GitHub Apps per repo). The current
  design assumes a single App. A future `app` key in
  `.myanyagent.toml` could extend this without breaking the single-App
  case.
- Auto-writing project documentation (`AGENTS.md` etc.). The tool
  never modifies project files other than `.git/config`.
- Global opencode agent instructions. Per-repo `.myanyagent.toml` is
  the only discovery signal; the tool does not pollute global agent
  config.
- Token caching. Each git operation mints a fresh token. Caching is a
  possible future optimization but is not needed for correctness.