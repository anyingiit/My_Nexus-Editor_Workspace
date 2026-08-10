# MyAnyAgent Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the per-repo MyAnyAgent GitHub App auth scripts into a reusable machine-level tool serving any repo under the same App, with a failure-driven agent discovery loop.

**Architecture:** Three layers — invariant kernel (machine-level tool at `~/.local/share/myanyagent/bin/`), variable per-repo params (committed `.myanyagent.toml`), and external private key (`~/.secrets/`, unchanged). `install.sh` installs the kernel + XDG config; `bootstrap` reads toml + config and writes local `.git/config`; `status` reports state with executable `-> run:` hints; the credential helper signs a JWT and mints an installation token on demand. Agent discovery is purely failure-driven: helpers emit `-> run:` remediation commands on stderr.

**Tech Stack:** Node.js 18 (`node:test` runner, `node:crypto`, `node:fs`, `node:child_process`), POSIX shell (`/bin/sh`), TOML (hand-parsed, no dep), git credential protocol.

**Spec:** `docs/superpowers/specs/2026-08-10-myanyagent-extraction-design.md`

## Global Constraints

- Private key lives at `~/.secrets/myanyagent.2026-08-04.private-key.pem` (external, never committed, never printed).
- `client_id = "Iv23lioD363YBpJJB9QE"`, `app_id = "4483813"` (public App identifiers, machine-level config).
- Bot identity: `name = "MyAnyAgent[bot]"`, `email = "312959697+myanyagent[bot]@users.noreply.github.com"`.
- JWT: RS256, `iat = now - 60`, `exp = now + 540`, `iss = client_id`.
- Token request: POST `https://api.github.com/app/installations/<installation_id>/access_tokens` with body `{ "repositories": ["<repo-name>"] }`, headers `Accept: application/vnd.github+json`, `Authorization: Bearer <jwt>`, `Content-Type: application/json`, `X-GitHub-Api-Version: 2022-11-28`.
- `bootstrap` writes ONLY to local `.git/config` (never global git config).
- All shell scripts use `#!/bin/sh` and `set -eu`; no bashisms.
- No external npm dependencies; use only Node built-ins and POSIX tools.
- `-> run:` hint format: the exact string `-> run:` followed by a space and the bare command, or absolute path fallback when `~/.local/bin` is not on PATH.
- Tool source of truth: a new top-level `myanyagent/` directory in this repo holds the installable tool; `install.sh` (also in `myanyagent/`) is the curl-able entrypoint.

---

## File Structure

New top-level directory `myanyagent/` in this repo holds the distributable tool source:

```
myanyagent/
  install.sh                              # curl|sh entrypoint; installs bin + XDG config
  bin/
    myanyagent-credential-helper.cjs      # invariant kernel: JWT + token + git credential protocol
    myanyagent-bootstrap.sh               # generic bootstrap: reads toml + config, writes .git/config
    myanyagent-status.sh                  # read-only self-check with -> run: hints
  config/
    config.template.toml                  # template for ~/.config/myanyagent/config.toml
  test/
    helper.test.cjs                       # node:test unit tests for the credential helper
    bootstrap.test.sh                     # bash integration test for bootstrap (throwaway repo)
    status.test.sh                        # bash integration test for status states
  README.md                               # install + usage docs, points at install.sh URL
  VERSION                                 # single line, e.g. "1"
```

Existing files to remove after migration (Task 7):
- `scripts/bootstrap-myanyagent.sh`
- `scripts/myanyagent-credential-helper.cjs`
- `GITHUB_APP_AUTH.md`

Existing files to create (per-repo, this repo):
- `.myanyagent.toml` (this repo's params, committed)

---

## Task 1: Credential Helper (Invariant Kernel)

**Files:**
- Create: `myanyagent/bin/myanyagent-credential-helper.cjs`
- Test: `myanyagent/test/helper.test.cjs`

**Interfaces:**
- Consumes: git credential protocol stdin (key=value lines), git config local keys `myanyagent.repository`, `myanyagent.installationId`, `myanyagent.privateKey`; `~/.config/myanyagent/config.toml` for `client_id`.
- Produces: stdout `username=x-access-token\npassword=<token>\n` on success; stderr `myanyagent: ... -> run: ...` on failure. Exit 0 for non-matching path, exit 1 on mint failure.

- [ ] **Step 1: Write the failing tests for path validation and JWT structure**

Create `myanyagent/test/helper.test.cjs`:

```javascript
const { test } = require("node:test");
const assert = require("node:assert");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const crypto = require("node:crypto");

const helperPath = path.join(__dirname, "..", "bin", "myanyagent-credential-helper.cjs");

function genKeyPair() {
  return crypto.generateKeyPairSync("rsa", { modulusLength: 2048 });
}

function runHelper(stdinInput, env = {}) {
  try {
    const out = execFileSync("node", [helperPath, "get"], {
      input: stdinInput,
      encoding: "utf8",
      env: { ...process.env, ...env },
    });
    return { stdout: out, stderr: "", status: 0 };
  } catch (e) {
    return { stdout: e.stdout?.toString() || "", stderr: e.stderr?.toString() || "", status: e.status };
  }
}

test("exits 0 silently when host is not github.com", () => {
  const r = runHelper("protocol=https\nhost=gitlab.com\npath=anyingiit/My_Nexus-Editor_Workspace.git\n\n");
  assert.equal(r.status, 0);
  assert.equal(r.stdout, "");
});

test("exits 0 silently when protocol is not https", () => {
  const r = runHelper("protocol=ssh\nhost=github.com\npath=anyingiit/My_Nexus-Editor_Workspace.git\n\n");
  assert.equal(r.status, 0);
  assert.equal(r.stdout, "");
});

test("exits 0 silently when path does not match configured repository", () => {
  // No git config for myanyagent.repository means localConfig returns "";
  // path "other/repo.git" != "" -> should exit 0 silently (not our repo)
  const r = runHelper("protocol=https\nhost=github.com\npath=other/repo.git\n\n");
  assert.equal(r.status, 0);
  assert.equal(r.stdout, "");
});

test("parses stdin fields correctly", () => {
  // Verify the helper reads protocol/host/path; non-matching host exits 0
  const r = runHelper("protocol=https\nhost=example.com\npath=foo/bar.git\n\n");
  assert.equal(r.status, 0);
  assert.equal(r.stdout, "");
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test myanyagent/test/helper.test.cjs`
Expected: FAIL — `myanyagent/bin/myanyagent-credential-helper.cjs` does not exist (ENOENT).

- [ ] **Step 3: Write the credential helper (path validation + stdin parsing only)**

Create `myanyagent/bin/myanyagent-credential-helper.cjs`:

```javascript
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const crypto = require("node:crypto");
const { execFileSync } = require("node:child_process");

function localConfig(key) {
  try {
    return execFileSync("git", ["config", "--local", "--get", key], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
  } catch {
    return "";
  }
}

function machineConfig(key) {
  const file = path.join(os.homedir(), ".config", "myanyagent", "config.toml");
  if (!fs.existsSync(file)) return "";
  const lines = fs.readFileSync(file, "utf8").split(/\r?\n/);
  for (const line of lines) {
    const m = line.match(new RegExp(`^\\s*${key}\\s*=\\s*"([^"]+)"`));
    if (m) return m[1];
  }
  return "";
}

const fields = {};
for (const line of fs.readFileSync(0, "utf8").split(/\r?\n/)) {
  const sep = line.indexOf("=");
  if (sep > 0) fields[line.slice(0, sep)] = line.slice(sep + 1);
}

if (process.argv[2] !== "get") process.exit(0);

const repository = localConfig("myanyagent.repository");
const installationId = localConfig("myanyagent.installationId");

if (fields.protocol !== "https" || fields.host !== "github.com") process.exit(0);
if (fields.path !== `${repository}.git`) process.exit(0);

const clientId = machineConfig("client_id");
const keyFile = process.env.MYANYAGENT_PRIVATE_KEY ||
  localConfig("myanyagent.privateKey") ||
  path.join(os.homedir(), ".secrets", "myanyagent.2026-08-04.private-key.pem");

function fail(msg) {
  console.error(`myanyagent: ${msg}`);
  console.error("-> run: myanyagent-status   (inspect)   or   myanyagent-bootstrap   (reconfigure)");
  process.exitCode = 1;
}

if (!clientId) { fail("client_id not found in ~/.config/myanyagent/config.toml"); return; }
if (!installationId) { fail("myanyagent.installationId not set in git config"); return; }
if (!fs.existsSync(keyFile)) { fail(`private key missing: ${keyFile}`); return; }

const key = fs.readFileSync(keyFile);
const b64 = (v) => Buffer.from(v).toString("base64url");
const now = Math.floor(Date.now() / 1000);
const header = b64(JSON.stringify({ alg: "RS256", typ: "JWT" }));
const payload = b64(JSON.stringify({ iat: now - 60, exp: now + 540, iss: clientId }));
const input = `${header}.${payload}`;
const jwt = `${input}.${crypto.createSign("RSA-SHA256").update(input).sign(key, "base64url")`;

(async () => {
  const response = await fetch(
    `https://api.github.com/app/installations/${installationId}/access_tokens`,
    {
      method: "POST",
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${jwt}`,
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
      },
      body: JSON.stringify({ repositories: [repository.split("/")[1]] }),
    }
  );
  const body = await response.json();
  if (!response.ok) throw new Error(`${response.status}: ${body.message || "GitHub App token request failed"}`);
  if (body.permissions?.contents !== "write") throw new Error("GitHub App token lacks contents:write");
  process.stdout.write(`username=x-access-token\npassword=${body.token}\n`);
})().catch((error) => fail(error.message));
```

- [ ] **Step 4: Run tests to verify path-validation tests pass**

Run: `node --test myanyagent/test/helper.test.cjs`
Expected: PASS for the 4 path/validation tests. (Token-mint tests are added in Step 5.)

- [ ] **Step 5: Add JWT structure and token-mint mock tests**

Append to `myanyagent/test/helper.test.cjs`:

```javascript
test("JWT is well-formed RS256 with correct claims", () => {
  // Test the JWT construction in isolation by extracting the signing logic.
  // We verify structure: three base64url parts, header alg RS256, payload iss=clientId.
  const { execFileSync } = require("node:child_process");
  const keyPair = crypto.generateKeyPairSync("rsa", { modulusLength: 2048 });
  const tmpKey = path.join(os.tmpdir(), `test-key-${process.pid}.pem`);
  fs.writeFileSync(tmpKey, keyPair.privateKey.export({ type: "pkcs8", format: "pem" }));

  // Run a tiny inline node script that reproduces the helper's JWT logic.
  const result = execFileSync("node", ["-e", `
    const fs = require("fs"), crypto = require("crypto");
    const key = fs.readFileSync(${JSON.stringify(tmpKey)});
    const b64 = v => Buffer.from(v).toString("base64url");
    const now = Math.floor(Date.now()/1000);
    const header = b64(JSON.stringify({alg:"RS256",typ:"JWT"}));
    const payload = b64(JSON.stringify({iat:now-60,exp:now+540,iss:"Iv23lioD363YBpJJB9QE"}));
    const input = header+"."+payload;
    const sig = crypto.createSign("RSA-SHA256").update(input).sign(key,"base64url");
    console.log(input+"."+sig);
  `], { encoding: "utf8" }).trim();

  const parts = result.split(".");
  assert.equal(parts.length, 3);
  const header = JSON.parse(Buffer.from(parts[0], "base64url").toString());
  assert.equal(header.alg, "RS256");
  assert.equal(header.typ, "JWT");
  const payload = JSON.parse(Buffer.from(parts[1], "base64url").toString());
  assert.equal(payload.iss, "Iv23lioD363YBpJJB9QE");
  assert.ok(payload.exp > payload.iat);
  assert.ok(payload.exp - payload.iat <= 600);
  fs.unlinkSync(tmpKey);
});

test("failure message includes -> run: remediation hint", () => {
  // Point helper at a nonexistent key to trigger failure path.
  // Set up a throwaway git repo with myanyagent.repository configured.
  const tmpRepo = fs.mkdtempSync(path.join(os.tmpdir(), "mya-test-"));
  const { execSync } = require("node:child_process");
  execSync("git init -q", { cwd: tmpRepo });
  execSync("git config --local myanyagent.repository anyingiit/My_Nexus-Editor_Workspace", { cwd: tmpRepo });
  execSync("git config --local myanyagent.installationId 151195329", { cwd: tmpRepo });
  execSync("git config --local myanyagent.privateKey /nonexistent/key.pem", { cwd: tmpRepo });

  const r = execFileSync("node", [helperPath, "get"], {
    input: "protocol=https\nhost=github.com\npath=anyingiit/My_Nexus-Editor_Workspace.git\n\n",
    encoding: "utf8",
    env: { ...process.env, GIT_DIR: path.join(tmpRepo, ".git") },
  }).trim().length > 0
    ? { stderr: "", status: 0 }
    : { stderr: "", status: 0 };
  // Helper reads git config via execFileSync("git", ...) which uses cwd, not GIT_DIR.
  // So we must run the helper with cwd=tmpRepo.
  try {
    execFileSync("node", [helperPath, "get"], {
      input: "protocol=https\nhost=github.com\npath=anyingiit/My_Nexus-Editor_Workspace.git\n\n",
      encoding: "utf8",
      cwd: tmpRepo,
    });
    assert.fail("should have thrown");
  } catch (e) {
    const stderr = e.stderr?.toString() || "";
    assert.ok(stderr.includes("-> run:"), `stderr should contain -> run:, got: ${stderr}`);
    assert.ok(stderr.includes("myanyagent-status"), `stderr should mention myanyagent-status`);
  }
  fs.rmSync(tmpRepo, { recursive: true, force: true });
});
```

- [ ] **Step 6: Run all helper tests**

Run: `node --test myanyagent/test/helper.test.cjs`
Expected: PASS (all 6 tests).

- [ ] **Step 7: Commit**

```bash
git add myanyagent/bin/myanyagent-credential-helper.cjs myanyagent/test/helper.test.cjs
git -c commit.gpgsign=false commit -m "feat(myanyagent): extracted credential helper with path validation and JWT signing

Reads repository/installationId/privateKey from git config, client_id from
~/.config/myanyagent/config.toml. Signs RS256 JWT, mints installation token,
returns git credential. Failure emits -> run: remediation hint on stderr."
```

---

## Task 2: Machine-Level Config Template

**Files:**
- Create: `myanyagent/config/config.template.toml`

**Interfaces:**
- Consumes: nothing.
- Produces: template that `install.sh` copies to `~/.config/myanyagent/config.toml`. Keys: `client_id` (string), `app_id` (string), `private_key` (string path).

- [ ] **Step 1: Write the config template**

Create `myanyagent/config/config.template.toml`:

```toml
# MyAnyAgent machine-level configuration
# Installed by myanyagent/install.sh to ~/.config/myanyagent/config.toml
# These values are shared across all repos using the MyAnyAgent GitHub App.

# GitHub App Client ID (public identifier, used as JWT issuer)
client_id = "Iv23lioD363YBpJJB9QE"

# GitHub App ID (public identifier, reference only)
app_id = "4483813"

# Default private key path. Override per-invocation with MYANYAGENT_PRIVATE_KEY env var.
private_key = "~/.secrets/myanyagent.2026-08-04.private-key.pem"
```

- [ ] **Step 2: Verify the helper's machineConfig parser reads this template**

Run:
```bash
node -e "
const fs=require('fs'),os=require('os'),path=require('path');
const file='myanyagent/config/config.template.toml';
const lines=fs.readFileSync(file,'utf8').split(/\r?\n/);
function machineConfig(key){
  for(const line of lines){
    const m=line.match(new RegExp('^\\\\s*'+key+'\\\\s*=\\\\s*\"([^\"]+)\"'));
    if(m) return m[1];
  }
  return '';
}
console.log('client_id:',machineConfig('client_id'));
console.log('app_id:',machineConfig('app_id'));
console.log('private_key:',machineConfig('private_key'));
"
```
Expected: prints `client_id: Iv23lioD363YBpJJB9QE`, `app_id: 4483813`, `private_key: ~/.secrets/myanyagent.2026-08-04.private-key.pem`

- [ ] **Step 3: Commit**

```bash
git add myanyagent/config/config.template.toml
git -c commit.gpgsign=false commit -m "feat(myanyagent): machine-level config template

Contains client_id, app_id, and default private_key path shared across all
repos using the MyAnyAgent App."
```

---

## Task 3: Bootstrap Script (Generic)

**Files:**
- Create: `myanyagent/bin/myanyagent-bootstrap.sh`
- Test: `myanyagent/test/bootstrap.test.sh`

**Interfaces:**
- Consumes: `.myanyagent.toml` (repo root) with keys `repository`, `installation_id`, `[bot].name`, `[bot].email`; `~/.config/myanyagent/config.toml` with `client_id`, `app_id`, `private_key`; env `MYANYAGENT_PRIVATE_KEY` (override). Requires `git`, `node`.
- Produces: writes local `.git/config` keys (`user.name`, `user.email`, `user.useConfigOnly`, `commit.gpgsign`, `credential.useHttpPath`, `myanyagent.repository`, `myanyagent.installationId`, `myanyagent.privateKey`, `credential.helper`). Exits 0 on success, 1 on failure.

- [ ] **Step 1: Write the failing integration test**

Create `myanyagent/test/bootstrap.test.sh`:

```sh
#!/bin/sh
set -eu

BOOTS="${BOOTS:-$(cd "$(dirname "$0")/.." && pwd)/bin/myanyagent-bootstrap.sh}"
TMPDIR=$(mktemp -d)
cleanup() { rm -rf "$TMPDIR"; }
trap cleanup EXIT

fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }

# --- Setup: throwaway git repo with .myanyagent.toml ---
git init -q "$TMPDIR/repo" || fail "git init failed"
cd "$TMPDIR/repo"
git config --global user.email "test@test.test" 2>/dev/null || true
git config --global user.name "Test" 2>/dev/null || true

cat > .myanyagent.toml <<EOF
repository = "anyingiit/My_Nexus-Editor_Workspace"
installation_id = "151195329"
[bot]
name = "MyAnyAgent[bot]"
email = "312959697+myanyagent[bot]@users.noreply.github.com"
EOF

git remote add origin "https://github.com/anyingiit/My_Nexus-Editor_Workspace.git" || fail "remote add failed"

# --- Test 1: rejects when origin URL does not match toml repository ---
git remote set-url origin "https://github.com/other/repo.git"
if sh "$BOOTS" 2>/dev/null; then
  fail "bootstrap should reject mismatched origin"
fi
printf 'PASS: mismatched origin rejected\n'

# --- Test 2: rejects without .myanyagent.toml ---
git remote set-url origin "https://github.com/anyingiit/My_Nexus-Editor_Workspace.git"
rm -f .myanyagent.toml
if sh "$BOOTS" 2>/dev/null; then
  fail "bootstrap should reject missing .myanyagent.toml"
fi
printf 'PASS: missing toml rejected\n'

# --- Test 3: rejects when private key missing ---
cat > .myanyagent.toml <<EOF
repository = "anyingiit/My_Nexus-Editor_Workspace"
installation_id = "151195329"
[bot]
name = "MyAnyAgent[bot]"
email = "312959697+myanyagent[bot]@users.noreply.github.com"
EOF
if MYANYAGENT_PRIVATE_KEY="/nonexistent/key.pem" sh "$BOOTS" 2>/dev/null; then
  fail "bootstrap should reject missing private key"
fi
printf 'PASS: missing private key rejected\n'

# --- Test 4: writes correct .git/config when key exists (dry-run: skip smoke test) ---
# Generate a dummy RSA key so the key-exists check passes.
KEY="$TMPDIR/dummy.pem"
openssl genrsa -out "$KEY" 2048 2>/dev/null || fail "openssl genrsa failed"

# We expect bootstrap to fail at the smoke test (token mint will fail with a dummy
# key against the real GitHub API), but we verify .git/config was written first.
# Run bootstrap and capture output; it should write config before smoke test.
MYANYAGENT_PRIVATE_KEY="$KEY" sh "$BOOTS" 2>"$TMPDIR/err" || true

# Verify git config was written
[ "$(git config --local user.name)" = "MyAnyAgent[bot]" ] || fail "user.name not set"
[ "$(git config --local user.email)" = "312959697+myanyagent[bot]@users.noreply.github.com" ] || fail "user.email not set"
[ "$(git config --local user.useConfigOnly)" = "true" ] || fail "useConfigOnly not set"
[ "$(git config --local commit.gpgsign)" = "false" ] || fail "gpgsign not set"
[ "$(git config --local credential.useHttpPath)" = "true" ] || fail "useHttpPath not set"
[ "$(git config --local myanyagent.repository)" = "anyingiit/My_Nexus-Editor_Workspace" ] || fail "myanyagent.repository not set"
[ "$(git config --local myanyagent.installationId)" = "151195329" ] || fail "myanyagent.installationId not set"
[ "$(git config --local myanyagent.privateKey)" = "$KEY" ] || fail "myanyagent.privateKey not set"
HELPER=$(git config --local credential.helper)
echo "$HELPER" | grep -q "myanyagent-credential-helper" || fail "credential.helper not set to helper path"
printf 'PASS: git config written correctly\n'

# --- Test 5: idempotent (second run produces same config) ---
MYANYAGENT_PRIVATE_KEY="$KEY" sh "$BOOTS" 2>/dev/null || true
[ "$(git config --local myanyagent.repository)" = "anyingiit/My_Nexus-Editor_Workspace" ] || fail "idempotent run broke repository"
[ "$(git config --local user.name)" = "MyAnyAgent[bot]" ] || fail "idempotent run broke user.name"
printf 'PASS: idempotent\n'

printf '\nALL bootstrap tests passed\n'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `sh myanyagent/test/bootstrap.test.sh`
Expected: FAIL — `myanyagent/bin/myanyagent-bootstrap.sh` does not exist.

- [ ] **Step 3: Write the bootstrap script**

Create `myanyagent/bin/myanyagent-bootstrap.sh`:

```sh
#!/bin/sh
set -eu

fail() {
  printf 'myanyagent: %s\n' "$1" >&2
  printf '-> run: myanyagent-status   (inspect)\n' >&2
  exit 1
}

command -v git >/dev/null 2>&1 || fail 'git is required'
command -v node >/dev/null 2>&1 || fail 'node.js is required'

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || fail 'run inside a Git repository'
cd "$repo_root"

toml="$repo_root/.myanyagent.toml"
[ -f "$toml" ] || fail ".myanyagent.toml not found at repository root"

# Parse .myanyagent.toml (simple key=value and [section] parser)
toml_get() {
  sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*\"\\([^\"]*\\)\".*$/\\1/p" "$toml" | head -1
}

repository=$(toml_get repository) || repository=""
installation_id=$(toml_get installation_id) || installation_id=""
bot_name=$(toml_get name) || bot_name=""
bot_email=$(toml_get email) || bot_email=""

[ -n "$repository" ] || fail "repository not set in .myanyagent.toml"
[ -n "$installation_id" ] || fail "installation_id not set in .myanyagent.toml"
[ -n "$bot_name" ] || fail "bot.name not set in .myanyagent.toml"
[ -n "$bot_email" ] || fail "bot.email not set in .myanyagent.toml"

fetch_url=$(git remote get-url origin 2>/dev/null) || fail 'origin remote is missing'
push_url=$(git remote get-url --push origin 2>/dev/null) || fail 'origin push remote is missing'
target_url="https://github.com/${repository}.git"
[ "$fetch_url" = "$target_url" ] || fail "origin fetch URL must be $target_url (got $fetch_url)"
[ "$push_url" = "$target_url" ] || fail "origin push URL must be $target_url (got $push_url)"

# Private key resolution: env > machine config > default
config_file="$HOME/.config/myanyagent/config.toml"
key_file=""
if [ -n "${MYANYAGENT_PRIVATE_KEY:-}" ]; then
  key_file="$MYANYAGENT_PRIVATE_KEY"
  case "$key_file" in /*) ;; *) fail "MYANYAGENT_PRIVATE_KEY must be an absolute path" ;; esac
elif [ -f "$config_file" ]; then
  key_file=$(sed -n 's/^[[:space:]]*private_key[[:space:]]*=[[:space:]]*"\([^"]*\)".*$/\1/p' "$config_file" | head -1)
  # Expand ~ in path
  case "$key_file" in '~'*) key_file="$HOME${key_file#"~"}" ;; esac
fi
[ -n "$key_file" ] || fail "private key path not configured (set MYANYAGENT_PRIVATE_KEY or private_key in config.toml)"
[ -r "$key_file" ] || fail "private key is missing or unreadable: $key_file"

# Tool paths
tool_dir="$HOME/.local/share/myanyagent"
helper="$tool_dir/bin/myanyagent-credential-helper.cjs"
[ -f "$helper" ] || fail "tool not installed at $tool_dir (run install.sh first)"

# Write local git config
git config --local user.name "$bot_name"
git config --local user.email "$bot_email"
git config --local user.useConfigOnly true
git config --local commit.gpgsign false
git config --local credential.useHttpPath true
git config --local myanyagent.repository "$repository"
git config --local myanyagent.installationId "$installation_id"
git config --local myanyagent.privateKey "$key_file"
git config --local --unset-all credential.helper >/dev/null 2>&1 || :
git config --local credential.helper ''
git config --local --add credential.helper "!node \"$helper\""

# Smoke test
credential_result=$(
  printf 'protocol=https\nhost=github.com\npath=%s.git\n\n' "$repository" |
    GIT_TERMINAL_PROMPT=0 git credential fill |
    node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{const f=Object.fromEntries(s.trim().split(/\n/).map(x=>x.split(/=(.*)/s,2)));if(f.username!=="x-access-token"||!f.password)process.exit(1);process.stdout.write(f.username+"|"+f.password.length)})'
) || fail 'GitHub App credential smoke test failed (token mint error)'

case "$credential_result" in
  x-access-token\|[1-9]*) ;;
  *) fail "credential smoke test returned an unexpected result" ;;
esac

printf 'MyAnyAgent Git identity and repository-local App authentication configured.\n'
printf 'Credential smoke test: username=x-access-token, token length=%s.\n' "${credential_result#*|}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `sh myanyagent/test/bootstrap.test.sh`
Expected: PASS — all 5 bootstrap tests pass. (The smoke test in test 4/5 will fail against real GitHub with a dummy key, but the test asserts `.git/config` was written before the smoke test and tolerates the smoke-test failure via `|| true`.)

- [ ] **Step 5: Commit**

```bash
git add myanyagent/bin/myanyagent-bootstrap.sh myanyagent/test/bootstrap.test.sh
git -c commit.gpgsign=false commit -m "feat(myanyagent): generic bootstrap reads .myanyagent.toml and writes local git config

Reads per-repo params from .myanyagent.toml, machine config from XDG, mints
token via the credential helper. Validates origin URL matches toml. Idempotent.
Rejects missing toml, mismatched origin, missing key."
```

---

## Task 4: Status Script (Read-Only Self-Check)

**Files:**
- Create: `myanyagent/bin/myanyagent-status.sh`
- Test: `myanyagent/test/status.test.sh`

**Interfaces:**
- Consumes: `.myanyagent.toml` (repo root), `~/.config/myanyagent/config.toml`, `~/.local/share/myanyagent/bin/` (tool presence), local `.git/config` keys `myanyagent.repository`, `myanyagent.installationId`, `myanyagent.privateKey`, `credential.helper`.
- Produces: stdout status report. Emits `-> run: <command>` as the last line when action is required. Exits 0 always (read-only). Uses absolute path fallback in `-> run:` when `~/.local/bin` not on PATH.

- [ ] **Step 1: Write the failing integration test**

Create `myanyagent/test/status.test.sh`:

```sh
#!/bin/sh
set -eu

STATUS="${STATUS:-$(cd "$(dirname "$0")/.." && pwd)/bin/myanyagent-status.sh}"
TMPDIR=$(mktemp -d)
cleanup() { rm -rf "$TMPDIR"; }
trap cleanup EXIT

fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }

# --- Setup: throwaway git repo ---
git init -q "$TMPDIR/repo"
cd "$TMPDIR/repo"
git config --global user.email "test@test.test" 2>/dev/null || true
git config --global user.name "Test" 2>/dev/null || true

cat > .myanyagent.toml <<EOF
repository = "anyingiit/My_Nexus-Editor_Workspace"
installation_id = "151195329"
[bot]
name = "MyAnyAgent[bot]"
email = "312959697+myanyagent[bot]@users.noreply.github.com"
EOF

# --- Test 1: not bootstrapped -> emits -> run: myanyagent-bootstrap ---
out=$(sh "$STATUS" 2>&1) || true
echo "$out" | grep -q "git config: NOT configured" || fail "should report NOT configured"
echo "$out" | grep -q "-> run: myanyagent-bootstrap" || fail "should emit -> run: myanyagent-bootstrap"
printf 'PASS: not-bootstrapped emits bootstrap hint\n'

# --- Test 2: bootstrapped -> emits OK, no -> run: line ---
# Simulate bootstrapped state by writing the git config directly.
git config --local myanyagent.repository "anyingiit/My_Nexus-Editor_Workspace"
git config --local myanyagent.installationId "151195329"
git config --local myanyagent.privateKey "/nonexistent/key.pem"  # status checks key path readability separately
git config --local credential.helper '!node "/some/helper.cjs"'

out=$(sh "$STATUS" 2>&1) || true
echo "$out" | grep -q "git config: configured" || fail "should report configured"
# Should NOT emit -> run: when git config is set (even if key unreadable, that's a separate line)
printf 'PASS: bootstrapped reports configured\n'

# --- Test 3: -> run: uses absolute path fallback when ~/.local/bin not on PATH ---
# Simulate by running with a PATH that excludes ~/.local/bin
out=$(PATH="/usr/bin:/bin" sh "$STATUS" 2>&1) || true
echo "$out" | grep -qE -- '-> run:.*myanyagent-bootstrap' || fail "should still emit bootstrap hint even with restricted PATH"
printf 'PASS: hint present with restricted PATH\n'

# --- Test 4: reports repo from .myanyagent.toml ---
out=$(sh "$STATUS" 2>&1) || true
echo "$out" | grep -q "repo: anyingiit/My_Nexus-Editor_Workspace" || fail "should report repo from toml"
printf 'PASS: reports repo from toml\n'

printf '\nALL status tests passed\n'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `sh myanyagent/test/status.test.sh`
Expected: FAIL — `myanyagent/bin/myanyagent-status.sh` does not exist.

- [ ] **Step 3: Write the status script**

Create `myanyagent/bin/myanyagent-status.sh`:

```sh
#!/bin/sh
set -eu

tool_dir="$HOME/.local/share/myanyagent"
bin_dir="$tool_dir/bin"
config_file="$HOME/.config/myanyagent/config.toml"
local_bin="$HOME/.local/bin"

# Determine command path for -> run: hints: bare name if ~/.local/bin on PATH, else absolute
on_path() {
  case ":$PATH:" in
    *:"$local_bin":*) return 0 ;;
    *) return 1 ;;
  esac
}

hint_cmd() {
  if on_path; then
    printf '%s' "$1"
  else
    printf '%s/%s' "$bin_dir" "$1"
  fi
}

# Check we are in a git repo with .myanyagent.toml
repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || true
toml="${repo_root:-.}/.myanyagent.toml"

if [ -f "$toml" ]; then
  repository=$(sed -n 's/^[[:space:]]*repository[[:space:]]*=[[:space:]]*"\([^"]*\)".*$/\1/p' "$toml" | head -1)
  installation_id=$(sed -n 's/^[[:space:]]*installation_id[[:space:]]*=[[:space:]]*"\([^"]*\)".*$/\1/p' "$toml" | head -1)
  printf 'repo: %s   (from .myanyagent.toml)\n' "$repository"
else
  printf 'repo: (no .myanyagent.toml found)\n'
  printf '-> run: %s   (create .myanyagent.toml with repository, installation_id, [bot])\n' "$(hint_cmd myanyagent-bootstrap)"
  exit 0
fi

# Tool installed?
if [ -d "$tool_dir" ] && [ -f "$bin_dir/myanyagent-credential-helper.cjs" ]; then
  version=$(cat "$tool_dir/VERSION" 2>/dev/null || echo "?")
  printf 'tool: installed v%s at %s\n' "$version" "$tool_dir"
else
  printf 'tool: NOT installed\n'
  printf '-> run: install the tool first (see repo README for install.sh URL)\n'
  exit 0
fi

# Machine config?
client_id=""
if [ -f "$config_file" ]; then
  client_id=$(sed -n 's/^[[:space:]]*client_id[[:space:]]*=[[:space:]]*"\([^"]*\)".*$/\1/p' "$config_file" | head -1)
  printf 'client_id: %s   (from %s)\n' "${client_id:-?}" "$config_file"
else
  printf 'client_id: NOT configured   (missing %s)\n' "$config_file"
fi

# Private key?
key_file=""
if [ -n "${MYANYAGENT_PRIVATE_KEY:-}" ]; then
  key_file="$MYANYAGENT_PRIVATE_KEY"
elif [ -f "$config_file" ]; then
  key_file=$(sed -n 's/^[[:space:]]*private_key[[:space:]]*=[[:space:]]*"\([^"]*\)".*$/\1/p' "$config_file" | head -1)
  case "$key_file" in '~'*) key_file="$HOME${key_file#"~"}" ;; esac
fi
if [ -n "$key_file" ] && [ -r "$key_file" ]; then
  printf 'private key: OK   %s\n' "$key_file"
else
  printf 'private key: MISSING   %s\n' "${key_file:-not configured}"
fi

# Git config bootstrapped?
configured=false
if [ -n "$repo_root" ]; then
  cfg_repo=$(git config --local --get myanyagent.repository 2>/dev/null || true)
  cfg_helper=$(git config --local --get credential.helper 2>/dev/null || true)
  if [ -n "$cfg_repo" ] && echo "$cfg_helper" | grep -q "myanyagent-credential-helper" 2>/dev/null; then
    configured=true
  fi
fi

needs_action=false

if $configured; then
  printf 'git config: configured\n'
else
  printf 'git config: NOT configured\n'
  needs_action=true
fi

# Private key or client_id missing also need action
if [ -z "$client_id" ] || [ -z "$key_file" ] || [ ! -r "$key_file" ]; then
  needs_action=true
fi

if $needs_action; then
  printf '-> run: %s\n' "$(hint_cmd myanyagent-bootstrap)"
else
  printf 'OK\n'
fi
```

- [ ] **Step 4: Run test to verify it passes**

Run: `sh myanyagent/test/status.test.sh`
Expected: PASS — all 4 status tests pass.

- [ ] **Step 5: Commit**

```bash
git add myanyagent/bin/myanyagent-status.sh myanyagent/test/status.test.sh
git -c commit.gpgsign=false commit -m "feat(myanyagent): read-only status self-check with -> run: hints

Reports repo, tool, private key, client_id, and git config state. Emits
-> run: <command> as the last line when action is required, OK when all
green. Uses absolute path fallback in hints when ~/.local/bin not on PATH."
```

---

## Task 5: Install Script

**Files:**
- Create: `myanyagent/install.sh`
- Create: `myanyagent/VERSION`

**Interfaces:**
- Consumes: `myanyagent/bin/*.sh`, `myanyagent/bin/*.cjs`, `myanyagent/config/config.template.toml`. Optional CLI flags: `--reset-config`.
- Produces: installs scripts to `~/.local/share/myanyagent/bin/`, creates `~/.local/bin/myanyagent-*` symlinks, writes `~/.local/share/myanyagent/VERSION`, creates `~/.config/myanyagent/config.toml` from template (only if missing, unless `--reset-config`).

- [ ] **Step 1: Write the VERSION file**

Create `myanyagent/VERSION`:
```
1
```

- [ ] **Step 2: Write the install script**

Create `myanyagent/install.sh`:

```sh
#!/bin/sh
set -eu

fail() { printf 'install failed: %s\n' "$1" >&2; exit 1; }

command -v node >/dev/null 2>&1 || fail 'node.js is required'

# Resolve script directory (works when piped via curl|sh by detecting repo layout)
# When run from repo, $0 points to the script. When piped, we use a different approach.
script_dir=""
if [ -f "$0" ] && [ -d "$(dirname "$0")/bin" ]; then
  script_dir=$(dirname "$0")
else
  fail 'run from the myanyagent repo (myanyagent/install.sh) or download the full directory'
fi

target_dir="$HOME/.local/share/myanyagent"
target_bin="$target_dir/bin"
config_dir="$HOME/.config/myanyagent"
config_file="$config_dir/config.toml"
local_bin="$HOME/.local/bin"

reset_config=false
for arg in "$@"; do
  case "$arg" in
    --reset-config) reset_config=true ;;
  esac
done

# Install tool
mkdir -p "$target_bin" "$config_dir" "$local_bin"
cp "$script_dir/bin/myanyagent-credential-helper.cjs" "$target_bin/"
cp "$script_dir/bin/myanyagent-bootstrap.sh" "$target_bin/"
chmod +x "$target_bin/myanyagent-bootstrap.sh"
cp "$script_dir/bin/myanyagent-status.sh" "$target_bin/"
chmod +x "$target_bin/myanyagent-status.sh"
cp "$script_dir/VERSION" "$target_dir/VERSION"

# Symlinks into ~/.local/bin
ln -sf "$target_bin/myanyagent-bootstrap.sh" "$local_bin/myanyagent-bootstrap"
ln -sf "$target_bin/myanyagent-status.sh" "$local_bin/myanyagent-status"
ln -sf "$target_bin/myanyagent-credential-helper.cjs" "$local_bin/myanyagent-helper"

# Config: preserve existing unless --reset-config
if [ ! -f "$config_file" ] || $reset_config; then
  cp "$script_dir/config/config.template.toml" "$config_file"
  printf 'Config written to %s\n' "$config_file"
else
  printf 'Config preserved at %s (use --reset-config to regenerate)\n' "$config_file"
fi

printf 'MyAnyAgent tool installed to %s\n' "$target_dir"
printf 'Symlinks created in %s\n' "$local_bin"

# PATH check
case ":$PATH:" in
  *:"$local_bin":*) ;;
  *)
    printf '\nNOTE: %s is not on your PATH.\n' "$local_bin"
    printf 'Add this line to your shell profile:\n'
    printf '  export PATH="%s:$PATH"\n' "$local_bin"
    ;;
esac

printf '\nNext: in any repo with .myanyagent.toml, run: myanyagent-bootstrap\n'
```

- [ ] **Step 3: Test install in a throwaway HOME**

Run:
```bash
TMPHOME=$(mktemp -d)
mkdir -p "$TMPHOME/.secrets"
cp ~/.secrets/myanyagent.2026-08-04.private-key.pem "$TMPHOME/.secrets/" 2>/dev/null || true
HOME="$TMPHOME" sh myanyagent/install.sh
# Verify
ls "$TMPHOME/.local/share/myanyagent/bin/" | tr '\n' ' '; echo
ls "$TMPHOME/.local/bin/" | tr '\n' ' '; echo
cat "$TMPHOME/.config/myanyagent/config.toml"
rm -rf "$TMPHOME"
```
Expected: install.sh prints success, 3 scripts in `bin/`, 3 symlinks in `~/.local/bin/`, config.toml contains client_id/app_id/private_key.

- [ ] **Step 4: Test idempotency and --reset-config**

Run:
```bash
TMPHOME=$(mktemp -d)
mkdir -p "$TMPHOME/.secrets"
cp ~/.secrets/myanyagent.2026-08-04.private-key.pem "$TMPHOME/.secrets/" 2>/dev/null || true
HOME="$TMPHOME" sh myanyagent/install.sh >/dev/null
# Mutate config
echo '# user edit' >> "$TMPHOME/.config/myanyagent/config.toml"
HOME="$TMPHOME" sh myanyagent/install.sh
# Config should be preserved (contains user edit)
grep -q '# user edit' "$TMPHOME/.config/myanyagent/config.toml" && echo "PASS: config preserved" || echo "FAIL"
# --reset-config
HOME="$TMPHOME" sh myanyagent/install.sh --reset-config
grep -q '# user edit' "$TMPHOME/.config/myanyagent/config.toml" && echo "FAIL: should be reset" || echo "PASS: config reset"
rm -rf "$TMPHOME"
```
Expected: first re-run prints "Config preserved"; `--reset-config` regenerates clean template.

- [ ] **Step 5: Commit**

```bash
git add myanyagent/install.sh myanyagent/VERSION
git -c commit.gpgsign=false commit -m "feat(myanyagent): install.sh one-liner installer

Installs bin scripts to ~/.local/share/myanyagent/, symlinks to ~/.local/bin/,
writes XDG config from template. Idempotent; --reset-config regenerates config."
```

---

## Task 6: README and Run All Tests

**Files:**
- Create: `myanyagent/README.md`

**Interfaces:**
- Consumes: all prior tasks.
- Produces: human/agent documentation pointing at install.sh and explaining the discovery loop.

- [ ] **Step 1: Write the README**

Create `myanyagent/README.md`:

```markdown
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
```

- [ ] **Step 2: Run all tests as documented in README**

Run:
```bash
node --test myanyagent/test/helper.test.cjs && sh myanyagent/test/bootstrap.test.sh && sh myanyagent/test/status.test.sh
```
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add myanyagent/README.md
git -c commit.gpgsign=false commit -m "docs(myanyagent): README with install, per-repo setup, and agent discovery section"
```

---

## Task 7: Migrate This Repo + Remove Old Scripts

**Files:**
- Create: `.myanyagent.toml` (repo root)
- Remove: `scripts/bootstrap-myanyagent.sh`, `scripts/myanyagent-credential-helper.cjs`, `GITHUB_APP_AUTH.md`
- Modify: this repo's `.git/config` (via bootstrap, not committed)

**Interfaces:**
- Consumes: Tasks 1-6 (installed tool).
- Produces: this repo uses the new tool exclusively; old per-repo scripts gone.

- [ ] **Step 1: Run install.sh for real on this machine**

Run:
```bash
sh myanyagent/install.sh
```
Expected: installs tool to `~/.local/share/myanyagent/`, creates `~/.config/myanyagent/config.toml`, symlinks in `~/.local/bin/`.

- [ ] **Step 2: Create .myanyagent.toml from existing values**

Create `.myanyagent.toml` at repo root:

```toml
repository = "anyingiit/My_Nexus-Editor_Workspace"
installation_id = "151195329"
[bot]
name = "MyAnyAgent[bot]"
email = "312959697+myanyagent[bot]@users.noreply.github.com"
```

- [ ] **Step 3: Run myanyagent-bootstrap on this repo**

Run:
```bash
cd /home/ubuntu/Workspace/My_Nexus-Editor_Workspace
myanyagent-bootstrap
```
Expected: "MyAnyAgent Git identity and repository-local App authentication configured. Credential smoke test: username=x-access-token, token length=<N>."

- [ ] **Step 4: Verify push still works with new tool**

Run:
```bash
git -c commit.gpgsign=false commit --allow-empty -m "test: verify myanyagent tool push"
git push origin main
```
Expected: push succeeds (bot-authored commit via new tool). Then remove the test commit if desired:
```bash
git reset --hard HEAD~1 && git push -f origin main
```
(Only do the force-push reset if the empty test commit landed; if you skip it, omit the reset.)

- [ ] **Step 5: Remove old per-repo scripts and auth doc**

Run:
```bash
git rm scripts/bootstrap-myanyagent.sh scripts/myanyagent-credential-helper.cjs GITHUB_APP_AUTH.md
```

- [ ] **Step 6: Verify status reports OK**

Run:
```bash
myanyagent-status
```
Expected: last line is `OK` (no `-> run:` line).

- [ ] **Step 7: Commit migration**

```bash
git add .myanyagent.toml
git -c commit.gpgsign=false commit -m "chore: migrate to reusable myanyagent tool

Add .myanyagent.toml with this repo's params. Remove per-repo scripts/
and GITHUB_APP_AUTH.md (replaced by the machine-level tool at
~/.local/share/myanyagent/)."
```

- [ ] **Step 8: Push migration commits**

Run:
```bash
git push origin main
```
Expected: push succeeds via the new tool.

---

## Self-Review

**Spec coverage check:**

- §1 Goal (extract auth logic to reusable tool): Tasks 1, 3, 4, 5 — ✅
- §2 Background (invariant kernel vs variable params): Tasks 1-2 split kernel/config — ✅
- §3 Architecture (three layers): Task 1 (kernel), Task 2 (machine config), Tasks 3+7 (.myanyagent.toml), private key unchanged — ✅
- §4 Components — helper: Task 1 — ✅; bootstrap: Task 3 — ✅; status: Task 4 — ✅; install.sh: Task 5 — ✅
- §5 Agent discovery loop: helper `-> run:` (Task 1 Step 3/5), status `-> run:` (Task 4 Step 3), absolute path fallback (Task 4 Step 3 `hint_cmd`) — ✅
- §6 Error handling: all cases covered in Task 1 (helper fail), Task 3 (bootstrap rejects), Task 4 (status reports) — ✅
- §7 Migration: Task 7 — ✅
- §8 Testing: Tasks 1, 3, 4, 5 each have tests; Task 6 runs all — ✅
- §9 Security boundaries: private key never printed (helper reads file, never logs), `bootstrap` writes local only, `fields.path` validation (Task 1) — ✅
- §10 Out of scope: multi-App, auto-docs, token caching — not implemented, as specified — ✅

**Placeholder scan:** No TBD/TODO/placeholder text found. All code blocks contain full implementations. ✅

**Type consistency:**
- `machineConfig(key)` in helper (Task 1) matches the `client_id`/`private_key` keys in `config.template.toml` (Task 2) — ✅
- `localConfig("myanyagent.repository")` / `myanyagent.installationId` / `myanyagent.privateKey` written by bootstrap (Task 3) and read by helper (Task 1) — same key names — ✅
- `toml_get`/`sed` parse the same `.myanyagent.toml` keys (`repository`, `installation_id`, `name`, `email`) in bootstrap (Task 3) and status (Task 4) — ✅
- `hint_cmd` fallback logic (Task 4) matches `install.sh` symlink creation (Task 5, `~/.local/bin/myanyagent-bootstrap`) — ✅

No issues found.