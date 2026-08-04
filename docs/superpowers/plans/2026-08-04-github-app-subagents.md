# GitHub App Auth and OpenCode Subagents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two repository-scoped OpenCode subagents using the requested OpenRouter models and save a minimal, non-secret record of the verified GitHub App access.

**Architecture:** Use one Markdown agent definition per model under `.opencode/agent/`; each definition selects an existing OpenRouter model and its validated `max` variant. Keep GitHub App identifiers and the runtime JWT-to-installation-token flow in a root `GITHUB_APP_AUTH.md`; never persist a JWT, installation token, or private key.

**Tech Stack:** OpenCode 1.18.11 agent frontmatter, OpenRouter models.dev catalog, GitHub REST API, Node.js built-in `crypto`/`fetch`, Markdown.

---

### Task 1: Add the DeepSeek subagent

**Files:**
- Create: `.opencode/agent/deepseek-v4-flash-0731-max.md`

- [ ] **Step 1: Create the agent definition with the exact model and variant**

```markdown
---
description: Uses OpenRouter DeepSeek V4 Flash 0731 at maximum reasoning for focused repository analysis and implementation support.
mode: subagent
model: openrouter/deepseek/deepseek-v4-flash-0731
variant: max
---

Use first-principles reasoning. Inspect repository context before acting, state
assumptions, make the smallest evidence-based change, and report verification
results explicitly.
```

- [ ] **Step 2: Verify the file has the expected frontmatter**

Run: `opencode debug agent deepseek-v4-flash-0731-max`

Expected: the resolved agent reports `mode: subagent`, model
`openrouter/deepseek/deepseek-v4-flash-0731`, and variant `max`.

### Task 2: Add the GPT subagent

**Files:**
- Create: `.opencode/agent/gpt-5.6-luna-max.md`

- [ ] **Step 1: Create the agent definition with the exact model and variant**

```markdown
---
description: Uses OpenRouter GPT-5.6 Luna at maximum reasoning for independent repository analysis and implementation support.
mode: subagent
model: openrouter/openai/gpt-5.6-luna
variant: max
---

Use first-principles reasoning. Inspect repository context before acting, state
assumptions, make the smallest evidence-based change, and report verification
results explicitly.
```

- [ ] **Step 2: Verify the file has the expected frontmatter**

Run: `opencode debug agent gpt-5.6-luna-max`

Expected: the resolved agent reports `mode: subagent`, model
`openrouter/openai/gpt-5.6-luna`, and variant `max`.

### Task 3: Save the minimal GitHub App access record

**Files:**
- Create: `GITHUB_APP_AUTH.md`

- [ ] **Step 1: Record only reusable non-secret authentication metadata**

```markdown
# GitHub App Access

- App: `MyAnyAgent` (`myanyagent`)
- App ID: `4483813`
- Client ID: `Iv23lioD363YBpJJB9QE`
- Installation ID: `151195329`
- Account: `anyingiit`
- Repository: `anyingiit/My_Nexus-Editor_Workspace`
- Private key: `~/.secrets/myanyagent.2026-08-04.private-key.pem` (external; never commit)

## Verified Access

Verified 2026-08-04 using a directly generated RS256 JWT and a repository-scoped installation token.

- Repository selection: `selected`
- Effective installation permissions: `actions:read`, `checks:read`, `contents:write`, `issues:write`, `metadata:read`
- Result: App metadata, installation lookup, installation-token exchange, and repository metadata access succeeded.

## Runtime Rule

Generate a fresh short-lived JWT and installation token when needed. Do not store either token or copy the private key into this repository.
```

- [ ] **Step 2: Check that the document contains no credential material**

Run: `rg -n 'BEGIN .*PRIVATE KEY|ghs_[A-Za-z0-9_]+|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+' GITHUB_APP_AUTH.md`

Expected: no matches.

### Task 4: Validate model catalog and GitHub App access

**Files:**
- Verify: `.opencode/agent/deepseek-v4-flash-0731-max.md`
- Verify: `.opencode/agent/gpt-5.6-luna-max.md`
- Verify: `GITHUB_APP_AUTH.md`

- [ ] **Step 1: Confirm both models exist and expose `max`**

Run: `opencode models --verbose openrouter | rg -n -A 105 -B 2 '^(openrouter/deepseek/deepseek-v4-flash-0731|openrouter/openai/gpt-5.6-luna)$'`

Expected: both model records are `active` and each contains a `max` variant with reasoning effort `max`.

- [ ] **Step 2: Confirm the resolved project agents**

Run: `opencode debug agent deepseek-v4-flash-0731-max && opencode debug agent gpt-5.6-luna-max`

Expected: both commands succeed and show the exact model/variant pair from Tasks 1 and 2.

- [ ] **Step 3: Re-run the direct GitHub App probe without persisting tokens**

Run the following from the repository root. The command reads the external key, prints only sanitized metadata, and keeps JWT/token values in process memory:

```bash
CLIENT_ID='Iv23lioD363YBpJJB9QE' KEY_FILE="$HOME/.secrets/myanyagent.2026-08-04.private-key.pem" REPO_OWNER='anyingiit' REPO_NAME='My_Nexus-Editor_Workspace' node <<'NODE'
const fs = require('fs');
const crypto = require('crypto');

const clientId = process.env.CLIENT_ID;
const key = fs.readFileSync(process.env.KEY_FILE);
const owner = process.env.REPO_OWNER;
const repo = process.env.REPO_NAME;
const base = 'https://api.github.com';
const b64 = value => Buffer.from(value).toString('base64url');
const now = Math.floor(Date.now() / 1000);
const header = b64(JSON.stringify({ alg: 'RS256', typ: 'JWT' }));
const payload = b64(JSON.stringify({ iat: now - 60, exp: now + 540, iss: clientId }));
const input = `${header}.${payload}`;
const jwt = `${input}.${crypto.createSign('RSA-SHA256').update(input).sign(key, 'base64url')}`;

async function api(path, token, options = {}) {
  const response = await fetch(`${base}${path}`, {
    ...options,
    headers: {
      Accept: 'application/vnd.github+json',
      Authorization: `Bearer ${token}`,
      'X-GitHub-Api-Version': '2022-11-28',
      ...(options.headers || {})
    }
  });
  const body = await response.json();
  if (!response.ok) throw new Error(`${response.status}: ${body.message || 'GitHub API request failed'}`);
  return body;
}

(async () => {
  const app = await api('/app', jwt);
  const installation = await api(`/repos/${owner}/${repo}/installation`, jwt);
  const access = await api(`/app/installations/${installation.id}/access_tokens`, jwt, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ repositories: [repo] })
  });
  const repository = await api(`/repos/${owner}/${repo}`, access.token);
  console.log(JSON.stringify({
    app: { id: app.id, slug: app.slug, client_id: app.client_id },
    installation: { id: installation.id, account: installation.account?.login, permissions: access.permissions },
    repository: { full_name: repository.full_name, default_branch: repository.default_branch }
  }, null, 2));
})().catch(error => {
  console.error(error.message);
  process.exitCode = 1;
});
NODE
```

Expected: the sanitized output identifies App ID `4483813`, installation ID `151195329`, repository `anyingiit/My_Nexus-Editor_Workspace`, and the documented permissions; no token value appears.

- [ ] **Step 4: Run repository hygiene checks**

Run: `git diff --check && git status --short`

Expected: no whitespace errors; the design, plan, agent, and authentication documentation files are the only task files added. Existing `.DS_Store` files remain untouched and must not be staged as part of this work. Do not commit because no commit was requested.

### Task 5: Restart OpenCode

- [ ] **Step 1: Restart the running OpenCode process**

OpenCode loads project configuration at startup. Quit and restart the current OpenCode session before using either subagent through `@deepseek-v4-flash-0731-max` or `@gpt-5.6-luna-max`.
