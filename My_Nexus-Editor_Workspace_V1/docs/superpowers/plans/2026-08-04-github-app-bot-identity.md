# Repository-Local MyAnyAgent Bot Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make normal commits and HTTPS pushes from this repository use the `MyAnyAgent[bot]` identity and App installation token, then replace the already-pushed human-attributed tip safely.

**Architecture:** Store commit identity and credential-helper settings in this repository's `.git/config` only. Store the token-minting helper under `.git/` so it is machine-local and never committed; it mints a short-lived token only for the target GitHub repository. Amend the current tip, preserve its parent and tree, and update `main` with an explicit `--force-with-lease`.

**Tech Stack:** Git local configuration, Node.js built-in `crypto`/`fetch`, GitHub App RS256 JWT, GitHub installation tokens, GitHub no-reply commit identity.

---

### Task 1: Prepare the repository-local identity policy

**Files:**
- Modify later: `GITHUB_APP_AUTH.md`

- [ ] **Step 1: Define the exact local commit identity section**

Append this exact section to `GITHUB_APP_AUTH.md` after the history correction
has been published:

```markdown
## Repository-Local Commit Identity

Normal Git commits in this checkout use:

- Name: `MyAnyAgent[bot]`
- Email: `312959697+myanyagent[bot]@users.noreply.github.com`

The identity and App credential helper are stored only in this checkout's
`.git/config` and `.git/` directory. They do not change global Git settings or
other repositories. The helper mints a fresh installation token on demand and
never persists it.
```

- [ ] **Step 2: Confirm the documentation contains no credential material**

Run: `rg -n -- '-----BEGIN .*PRIVATE KEY-----|ghs_[A-Za-z0-9_]+|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+' GITHUB_APP_AUTH.md`

Expected: no matches.

### Task 2: Install repository-local Git identity and App credential helper

**Files:**
- Create: `.git/myanyagent-credential-helper.cjs` (machine-local, never staged)
- Modify: `.git/config` through `git config --local`

- [ ] **Step 1: Create the machine-local helper**

Create `.git/myanyagent-credential-helper.cjs` with this exact content:

```javascript
const fs = require("node:fs");
const crypto = require("node:crypto");

const CLIENT_ID = "Iv23lioD363YBpJJB9QE";
const INSTALLATION_ID = "151195329";
const REPOSITORY = "anyingiit/My_Nexus-Editor_Workspace";
const KEY_FILE = `${process.env.HOME}/.secrets/myanyagent.2026-08-04.private-key.pem`;

const fields = {};
for (const line of fs.readFileSync(0, "utf8").split(/\r?\n/)) {
  const separator = line.indexOf("=");
  if (separator > 0) fields[line.slice(0, separator)] = line.slice(separator + 1);
}

if (process.argv[2] !== "get") process.exit(0);
if (fields.protocol !== "https" || fields.host !== "github.com" || fields.path !== `${REPOSITORY}.git`) {
  process.exit(0);
}

const b64 = value => Buffer.from(value).toString("base64url");
const now = Math.floor(Date.now() / 1000);
const header = b64(JSON.stringify({ alg: "RS256", typ: "JWT" }));
const payload = b64(JSON.stringify({ iat: now - 60, exp: now + 540, iss: CLIENT_ID }));
const input = `${header}.${payload}`;
const key = fs.readFileSync(KEY_FILE);
const jwt = `${input}.${crypto.createSign("RSA-SHA256").update(input).sign(key, "base64url")}`;

(async () => {
  const response = await fetch(`https://api.github.com/app/installations/${INSTALLATION_ID}/access_tokens`, {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${jwt}`,
      "Content-Type": "application/json",
      "X-GitHub-Api-Version": "2022-11-28"
    },
    body: JSON.stringify({ repositories: [REPOSITORY.split("/")[1]] })
  });
  const body = await response.json();
  if (!response.ok) throw new Error(`${response.status}: ${body.message || "GitHub App token request failed"}`);
  if (body.permissions?.contents !== "write") throw new Error("GitHub App token lacks contents:write");
  process.stdout.write(`username=x-access-token\npassword=${body.token}\n`);
})().catch(error => {
  console.error(error.message);
  process.exitCode = 1;
});
```

- [ ] **Step 2: Set only repository-local Git identity**

Run:

```bash
git config --local user.name 'MyAnyAgent[bot]'
git config --local user.email '312959697+myanyagent[bot]@users.noreply.github.com'
git config --local user.useConfigOnly true
git config --local commit.gpgsign false
git config --local credential.useHttpPath true
```

Expected: `git config --local --list` shows these values, including
`credential.usehttppath=true`; `~/.gitconfig` is unchanged.

- [ ] **Step 3: Make the local helper override inherited credential helpers**

Run:

```bash
git config --local --unset-all credential.helper || true
git config --local credential.helper ''
git config --local --add credential.helper "!node $PWD/.git/myanyagent-credential-helper.cjs"
```

Expected: this repository's local config clears the inherited keychain helper and invokes only the `.git/` helper. No global config is modified.

### Task 3: Verify local identity and token minting without exposing tokens

**Files:**
- Verify: `.git/config`
- Verify: `.git/myanyagent-credential-helper.cjs`

- [ ] **Step 1: Verify the effective identity and local-only scope**

Run:

```bash
git config --local --get-regexp '^(user\.(name|email|useconfigonly)|commit\.gpgsign|credential\.helper)$'
git config --global --get-regexp '^(user\.(name|email|useconfigonly)|commit\.gpgsign|credential\.helper)$' || true
```

Expected: local identity is `MyAnyAgent[bot]` with the bot email, local signing is `false`, local credential helper points into `.git/`, and global identity remains `tomwptgb` with its existing `osxkeychain` helper.

- [ ] **Step 2: Exercise the target credential helper without printing its password**

Run:

```bash
printf 'protocol=https\nhost=github.com\npath=anyingiit/My_Nexus-Editor_Workspace.git\n\n' |
  GIT_TERMINAL_PROMPT=0 git credential fill |
  node -e 'let s=""; process.stdin.on("data", d => s += d).on("end", () => { const f = Object.fromEntries(s.trim().split(/\n/).map(x => x.split(/=(.*)/s, 2))); if (f.username !== "x-access-token" || !/^ghs_/.test(f.password || "")) process.exit(1); console.log(JSON.stringify({ username: f.username, token_prefix: "ghs_", token_length: f.password.length })); })'
```

Expected: output contains only `username`, `token_prefix`, and token length; the token itself is not printed or persisted.

- [ ] **Step 3: Confirm the helper refuses a different repository**

Run: `printf 'protocol=https\nhost=github.com\npath=other/repository.git\n\n' | GIT_TERMINAL_PROMPT=0 git credential fill`

Expected: the command fails without returning a username or password for the unrelated repository.

### Task 4: Rewrite the published tip as the bot

**Files:**
- Rewrite: current `main` tip only

- [ ] **Step 1: Capture the remote lease and original tree metadata**

Run:

```bash
git fetch origin main
OLD_SHA=$(git rev-parse origin/main)
PARENT_SHA=$(git rev-parse HEAD^)
TREE_SHA=$(git rev-parse HEAD^{tree})
MESSAGE=$(git log -1 --format=%s HEAD)
test "$OLD_SHA" = "$(git rev-parse HEAD)"
```

Expected: `OLD_SHA` equals the currently published human-attributed tip, and `PARENT_SHA`, `TREE_SHA`, and `MESSAGE` are recorded in the shell for post-amend comparison.

- [ ] **Step 2: Amend only the existing commit metadata**

No working-tree file should be staged for this step; the purpose is to rewrite
only the existing commit metadata.

Run:

```bash
git commit --amend --reset-author --no-edit --no-gpg-sign
```

Expected: the new local tip has author and committer `MyAnyAgent[bot] <312959697+myanyagent[bot]@users.noreply.github.com>`, has no human GPG signature, and retains the original parent, message, and tree.

- [ ] **Step 3: Verify the amended object before publishing**

Run this in the same shell as Step 1 so `PARENT_SHA`, `TREE_SHA`, and
`MESSAGE` remain available:

Run:

```bash
test "$(git rev-parse HEAD^)" = "$PARENT_SHA"
test "$(git rev-parse HEAD^{tree})" = "$TREE_SHA"
test "$(git log -1 --format=%s HEAD)" = "$MESSAGE"
test "$(git log -1 --format=%an HEAD)" = 'MyAnyAgent[bot]'
test "$(git log -1 --format=%ae HEAD)" = '312959697+myanyagent[bot]@users.noreply.github.com'
test "$(git log -1 --format=%cn HEAD)" = 'MyAnyAgent[bot]'
test "$(git log -1 --format=%ce HEAD)" = '312959697+myanyagent[bot]@users.noreply.github.com'
test "$(git log -1 --format=%G? HEAD)" = 'N'
```

Expected: parent, tree, and message match; author and committer are the bot; signature status is `N` for an unsigned object.

### Task 5: Publish the corrected identity with a protected force update

**Files:**
- Remote: `origin/main`

- [ ] **Step 1: Update `main` with an explicit lease**

Run:

```bash
GIT_TERMINAL_PROMPT=0 git push --force-with-lease=refs/heads/main:$OLD_SHA origin main
```

Expected: push succeeds only if remote `main` still equals `OLD_SHA`; the remote branch advances to the amended bot commit. If the lease fails, stop without retrying with `--force`.

- [ ] **Step 2: Confirm the remote commit and no token persistence**

Run:

```bash
git fetch origin main
test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)"
git config --local --get-regexp '^(credential\.usehttppath|credential\.helper)$'
git remote -v
```

Expected: local and remote `main` match; credential helper is local `.git/` only; remote URLs contain no token.

- [ ] **Step 3: Publish the identity documentation as a separate bot commit**

Append the section from Task 1 to `GITHUB_APP_AUTH.md`, then stage only the
documentation files and create a normal bot-authored commit:

```bash
git add GITHUB_APP_AUTH.md docs/superpowers/specs/2026-08-04-github-app-bot-identity-design.md docs/superpowers/plans/2026-08-04-github-app-bot-identity.md
git commit -m "Document repository-local bot identity"
GIT_TERMINAL_PROMPT=0 git push origin main
```

Expected: the new documentation commit is also authored and committed by
`MyAnyAgent[bot]`; the corrected replacement for the old human commit remains
its parent.

### Task 6: Verify GitHub attribution and repository isolation

- [ ] **Step 1: Read the published commit metadata**

Run: `git show -s --format=fuller HEAD`

Expected: both Author and Commit are `MyAnyAgent[bot] <312959697+myanyagent[bot]@users.noreply.github.com>`.

- [ ] **Step 2: Verify the GitHub commit page**

Open: `https://github.com/anyingiit/My_Nexus-Editor_Workspace/commits/main`

Expected: both the rewritten history-correction commit and the later
documentation commit are attributed to `myanyagent[bot]`; the old
human-attributed SHA is no longer reachable from `main`.

- [ ] **Step 3: Verify global settings remain unchanged**

Run: `git config --global --get user.name && git config --global --get user.email && git config --global --get credential.helper`

Expected: global identity remains the pre-existing human identity and global credential helper remains unchanged.

- [ ] **Step 4: Verify local-only files are not staged**

Run: `git status --short --untracked-files=all`

Expected: `.git/myanyagent-credential-helper.cjs` does not appear because `.git/` is outside the worktree; only pre-existing unrelated `.DS_Store` files may remain untracked.
