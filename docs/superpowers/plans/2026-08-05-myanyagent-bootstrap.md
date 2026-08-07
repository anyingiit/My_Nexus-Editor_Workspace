# MyAnyAgent 跨设备 Bootstrap 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 将当前仓库的 MyAnyAgent Git 身份与 GitHub App HTTPS 认证能力变成可迁移、可重复执行的 bootstrap，并把当前本地已有提交与新增能力一起推送到远程 `main`。

**架构：** 将 credential helper 和 bootstrap 入口放入仓库的 `scripts/`，使代码随 clone 迁移；将私钥路径、Git 身份和 helper 命令写入每台设备本地的 `.git/config`。helper 只为目标仓库即时生成短期 installation token，bootstrap 负责前置检查、幂等配置和无泄露 smoke test。

**技术栈：** POSIX `sh`、Node.js 内置 `crypto`/`fetch`、Git credential protocol、GitHub App RS256 JWT、Markdown。

---

### Task 1: 创建可迁移 credential helper

**文件：**
- Create: `scripts/myanyagent-credential-helper.cjs`

- [ ] **Step 1: 创建 helper 文件**

写入以下完整内容：

```javascript
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const crypto = require("node:crypto");
const { execFileSync } = require("node:child_process");

const CLIENT_ID = "Iv23lioD363YBpJJB9QE";
const INSTALLATION_ID = "151195329";
const REPOSITORY = "anyingiit/My_Nexus-Editor_Workspace";

const fields = {};
for (const line of fs.readFileSync(0, "utf8").split(/\r?\n/)) {
  const separator = line.indexOf("=");
  if (separator > 0) fields[line.slice(0, separator)] = line.slice(separator + 1);
}

if (process.argv[2] !== "get") process.exit(0);
if (fields.protocol !== "https" || fields.host !== "github.com" || fields.path !== `${REPOSITORY}.git`) {
  process.exit(0);
}

function localConfig(key) {
  try {
    return execFileSync("git", ["config", "--local", "--get", key], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"]
    }).trim();
  } catch {
    return "";
  }
}

const keyFile = process.env.MYANYAGENT_PRIVATE_KEY ||
  localConfig("myanyagent.privateKey") ||
  path.join(os.homedir(), ".secrets", "myanyagent.2026-08-04.private-key.pem");
const key = fs.readFileSync(keyFile);
const b64 = value => Buffer.from(value).toString("base64url");
const now = Math.floor(Date.now() / 1000);
const header = b64(JSON.stringify({ alg: "RS256", typ: "JWT" }));
const payload = b64(JSON.stringify({ iat: now - 60, exp: now + 540, iss: CLIENT_ID }));
const input = `${header}.${payload}`;
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

- [ ] **Step 2: 检查 Node 语法**

Run: `node --check scripts/myanyagent-credential-helper.cjs`

Expected: exit code 0 and no syntax error.

### Task 2: 创建幂等 bootstrap 入口

**文件：**
- Create: `scripts/bootstrap-myanyagent.sh`

- [ ] **Step 1: 创建 bootstrap 脚本**

写入以下完整内容：

```sh
#!/bin/sh
set -eu

fail() {
  printf 'bootstrap failed: %s\n' "$1" >&2
  exit 1
}

command -v git >/dev/null 2>&1 || fail 'git is required'
command -v node >/dev/null 2>&1 || fail 'node.js is required'

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || fail 'run inside a Git repository'
cd "$repo_root"

target_url='https://github.com/anyingiit/My_Nexus-Editor_Workspace.git'
fetch_url=$(git remote get-url origin 2>/dev/null) || fail 'origin remote is missing'
push_url=$(git remote get-url --push origin 2>/dev/null) || fail 'origin push remote is missing'
[ "$fetch_url" = "$target_url" ] || fail "origin fetch URL must be $target_url"
[ "$push_url" = "$target_url" ] || fail "origin push URL must be $target_url"

key_file=${MYANYAGENT_PRIVATE_KEY:-"$HOME/.secrets/myanyagent.2026-08-04.private-key.pem"}
case "$key_file" in
  /*) ;;
  *) fail 'MYANYAGENT_PRIVATE_KEY must be an absolute path' ;;
esac
[ -r "$key_file" ] || fail "private key is missing or unreadable: $key_file"

helper="$repo_root/scripts/myanyagent-credential-helper.cjs"
[ -f "$helper" ] || fail "credential helper is missing: $helper"

git config --local user.name 'MyAnyAgent[bot]'
git config --local user.email '312959697+myanyagent[bot]@users.noreply.github.com'
git config --local user.useConfigOnly true
git config --local commit.gpgsign false
git config --local credential.useHttpPath true
git config --local myanyagent.privateKey "$key_file"
git config --local --unset-all credential.helper >/dev/null 2>&1 || :
git config --local credential.helper ''
git config --local --add credential.helper "!node \"$helper\""

credential_result=$(
  printf 'protocol=https\nhost=github.com\npath=anyingiit/My_Nexus-Editor_Workspace.git\n\n' |
    GIT_TERMINAL_PROMPT=0 git credential fill |
    node -e 'let s=""; process.stdin.on("data", d => s += d).on("end", () => { const f = Object.fromEntries(s.trim().split(/\n/).map(x => x.split(/=(.*)/s, 2))); if (f.username !== "x-access-token" || !f.password) process.exit(1); process.stdout.write(`${f.username}|${f.password.length}`); })'
) || fail 'GitHub App credential smoke test failed'

case "$credential_result" in
  x-access-token\|[1-9]*) ;;
  *) fail 'credential smoke test returned an unexpected result' ;;
esac

printf 'MyAnyAgent Git identity and repository-local App authentication configured.\n'
printf 'Credential smoke test: username=x-access-token, token length=%s.\n' "${credential_result#*|}"
```

- [ ] **Step 2: 检查 shell 语法并设置执行权限**

Run: `sh -n scripts/bootstrap-myanyagent.sh && chmod +x scripts/bootstrap-myanyagent.sh`

Expected: syntax check succeeds; the script is executable in the working tree.

### Task 3: 更新跨设备文档

**文件：**
- Modify: `GITHUB_APP_AUTH.md`
- Verify: `docs/superpowers/specs/2026-08-05-myanyagent-bootstrap-design.zh-CN.md`
- Verify: `docs/superpowers/specs/2026-08-05-myanyagent-bootstrap-design.md`

- [ ] **Step 1: 在认证文档中加入 bootstrap 使用方式**

Append this exact section to `GITHUB_APP_AUTH.md`:

```markdown
## Cross-Device Bootstrap

Prerequisites on each device:

- Git and Node.js are installed.
- `origin` uses the HTTPS URL for this repository.
- The private key is provisioned separately at
  `~/.secrets/myanyagent.2026-08-04.private-key.pem` with restrictive file
  permissions.

From the repository root, run:

```bash
MYANYAGENT_PRIVATE_KEY="$HOME/.secrets/myanyagent.2026-08-04.private-key.pem" \
  ./scripts/bootstrap-myanyagent.sh
```

The bootstrap writes only this checkout's `.git/config`, validates the target
remote, and verifies token minting without printing the token. It never copies
the private key into the repository. For a different key location, set
`MYANYAGENT_PRIVATE_KEY` to an absolute path.
```

- [ ] **Step 2: 扫描 tracked 文档中的真实凭据特征**

Run:

```bash
if rg -n -- '-----BEGIN (RSA|OPENSSH|EC|DSA|PGP) PRIVATE KEY-----|ghs_[A-Za-z0-9_]{20,}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}' GITHUB_APP_AUTH.md scripts docs; then
  exit 1
fi
```

Expected: no output and exit code 0.

### Task 4: 在当前设备运行 bootstrap 并验证隔离

**文件：**
- Modify: current repository `.git/config` only
- Do not stage: `.git/` helper/config values

- [ ] **Step 1: 运行 bootstrap**

Run:

```bash
MYANYAGENT_PRIVATE_KEY="$HOME/.secrets/myanyagent.2026-08-04.private-key.pem" \
  ./scripts/bootstrap-myanyagent.sh
```

Expected: bootstrap succeeds and reports only the `x-access-token` username and token length, never the token itself.

- [ ] **Step 2: 重复运行以验证幂等性**

Run the same command again.

Expected: it succeeds without duplicate `credential.helper` entries or changed global configuration.

- [ ] **Step 3: 验证错误仓库被拒绝**

Run:

```bash
set +e
other_output=$(printf 'protocol=https\nhost=github.com\npath=other/repository.git\n\n' | GIT_TERMINAL_PROMPT=0 git credential fill 2>/dev/null)
other_code=$?
set -e
```

Expected: the command exits nonzero and returns no credentials.

- [ ] **Step 4: 验证 local/global Git 配置边界**

Run:

```bash
git config --local --get-regexp '^(user\.(name|email|useconfigonly)|commit\.gpgsign|credential\.(usehttppath|helper)|myanyagent\.privatekey)$'
git config --show-origin --get-regexp '^credential\.helper$'
git config --global --get user.name
git config --global --get user.email
git config --global --get commit.gpgsign
```

Expected: local values point to `MyAnyAgent[bot]`, the external key path and tracked helper; global identity remains unchanged and the system keychain helper is only inherited outside this repository.

### Task 5: Commit and push all approved changes

**文件：**
- Add: `scripts/myanyagent-credential-helper.cjs`
- Add: `scripts/bootstrap-myanyagent.sh`
- Add: `docs/superpowers/specs/2026-08-05-myanyagent-bootstrap-design.md`
- Add: `docs/superpowers/specs/2026-08-05-myanyagent-bootstrap-design.zh-CN.md`
- Add: `docs/superpowers/plans/2026-08-05-myanyagent-bootstrap.md`
- Modify: `GITHUB_APP_AUTH.md`
- Do not stage: `.DS_Store`

- [ ] **Step 1: Confirm the remote is still the ancestor of local work**

Run:

```bash
git fetch origin main
test "$(git rev-parse origin/main)" = '8c1e5a0'
git merge-base --is-ancestor origin/main HEAD
```

Expected: the current remote tip is unchanged and is an ancestor of the two existing local contribution README commits.

- [ ] **Step 2: Stage only intended tracked files**

Run:

```bash
git add GITHUB_APP_AUTH.md scripts/myanyagent-credential-helper.cjs scripts/bootstrap-myanyagent.sh docs/superpowers/specs/2026-08-05-myanyagent-bootstrap-design.md docs/superpowers/specs/2026-08-05-myanyagent-bootstrap-design.zh-CN.md docs/superpowers/plans/2026-08-05-myanyagent-bootstrap.md
git diff --cached --check
git diff --cached --name-only
```

Expected: only the six listed task files are staged; `.DS_Store` is absent.

- [ ] **Step 3: Create a bot-authored bootstrap commit**

Run: `git commit -m "Add portable MyAnyAgent Git bootstrap"`

Expected: the new commit author and committer are `MyAnyAgent[bot]` with the bot no-reply email and no human GPG signature.

- [ ] **Step 4: Push all local commits normally**

Run: `GIT_TERMINAL_PROMPT=0 git push origin main`

Expected: the two existing local contribution README commits and the new bootstrap commit are pushed by fast-forward to `origin/main`; no force push is needed.

### Task 6: Final verification

- [ ] **Step 1: Verify branch and commit identities**

Run:

```bash
git fetch origin main
test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)"
test "$(git show -s --format=%an HEAD)" = 'MyAnyAgent[bot]'
test "$(git show -s --format=%ae HEAD)" = '312959697+myanyagent[bot]@users.noreply.github.com'
test "$(git show -s --format=%cn HEAD)" = 'MyAnyAgent[bot]'
test "$(git show -s --format=%ce HEAD)" = '312959697+myanyagent[bot]@users.noreply.github.com'
git log --oneline --decorate -5
```

Expected: local and remote `main` match, and the bootstrap commit is bot-authored.

- [ ] **Step 2: Verify no credential material was committed**

Run:

```bash
if rg -n -- '-----BEGIN (RSA|OPENSSH|EC|DSA|PGP) PRIVATE KEY-----|ghs_[A-Za-z0-9_]{20,}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}' scripts GITHUB_APP_AUTH.md docs; then
  exit 1
fi
git remote -v
git status --short --untracked-files=all
```

Expected: no credential matches, remote URLs contain no token, and only pre-existing `.DS_Store` files remain untracked.

- [ ] **Step 3: Verify GitHub commit attribution**

Open: `https://github.com/anyingiit/My_Nexus-Editor_Workspace/commits/main`

Expected: the bootstrap commit and the two existing contribution README commits display as `myanyagent[bot]`.
