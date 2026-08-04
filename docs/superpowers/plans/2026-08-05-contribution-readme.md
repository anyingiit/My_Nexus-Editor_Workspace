# Contribution README Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a concise root README that explains how this workspace gives local Agents controlled access to contribute to `Nexus-Editor`.

**Architecture:** Keep the documentation in one root `README.md`. Describe the existing `Nexus-Editor/` Git Submodule as the boundary between the workspace and upstream source, link to the existing project-local Agent definitions, and reference `GITHUB_APP_AUTH.md` instead of duplicating authorization metadata.

**Tech Stack:** Markdown, Git Submodule, OpenCode project-local Agent definitions.

---

### Task 1: Create the contribution README

**Files:**
- Create: `README.md`
- Reference: `.gitmodules`
- Reference: `.opencode/agent/gpt-5.6-luna-max.md`
- Reference: `.opencode/agent/deepseek-v4-flash-0731-max.md`
- Reference: `GITHUB_APP_AUTH.md`

- [ ] **Step 1: Confirm the documented sources are present**

Run:

```bash
test -f .gitmodules && \
test -f .opencode/agent/gpt-5.6-luna-max.md && \
test -f .opencode/agent/deepseek-v4-flash-0731-max.md && \
test -f GITHUB_APP_AUTH.md
```

Expected: the command exits successfully without output.

- [ ] **Step 2: Create `README.md` with the four approved sections**

Write exactly this content, adjusting only line wrapping if the repository formatter requires it:

```markdown
# My Nexus-Editor Workspace

## 项目目的

本项目的目标是在自由控制本地 Agent 的前提下，为
[Nexus-Editor](https://github.com/floatboatai/Nexus-Editor) 作出贡献。

仓库通过 [`Nexus-Editor/`](./Nexus-Editor) Git Submodule 引入上游代码：

- 本地 Agent 可以在受控的本地工作区中分析和修改上游代码。
- 父仓库只记录 Submodule 的 Gitlink 提交指针，不复制上游代码历史。
- 本地工作区与上游仓库保持边界，改动可以独立审查并通过 PR 贡献回上游。

## Submodule 协作

首次获取仓库时递归克隆 Submodule：

```bash
git clone --recurse-submodules https://github.com/anyingiit/My_Nexus-Editor_Workspace.git
```

已有 checkout 时初始化或同步 Submodule：

```bash
git submodule update --init --recursive
```

在 Submodule 内创建分支并完成改动：

```bash
git -C Nexus-Editor switch -c feature/my-change
# 在 Nexus-Editor/ 中修改并验证代码
git -C Nexus-Editor add -p
git -C Nexus-Editor commit -m "Improve editor behavior"
```

将分支推送到个人 Fork 的 `fork` remote，并向
`floatboatai/Nexus-Editor` 提出 PR。PR 合并后，在父仓库记录新的
Submodule 提交指针：

```bash
git add Nexus-Editor
git commit -m "Update Nexus-Editor submodule"
```

父仓库的提交只更新 Gitlink；上游源代码的提交应在 `Nexus-Editor/` 仓库内
完成。

## OpenCode 子 Agent

以下两个项目级 `mode: subagent` Agent 位于当前目录下，可在 OpenCode 中以
`@gpt-5.6-luna-max` 或 `@deepseek-v4-flash-0731-max` 的形式委派 Task：

| Agent | 定义文件 | 模型 |
| --- | --- | --- |
| `gpt-5.6-luna-max` | `.opencode/agent/gpt-5.6-luna-max.md` | `openrouter/openai/gpt-5.6-luna` |
| `deepseek-v4-flash-0731-max` | `.opencode/agent/deepseek-v4-flash-0731-max.md` | `openrouter/deepseek/deepseek-v4-flash-0731` |

两个 Agent 都使用 `max` variant，并遵循先检查上下文、基于证据做最小改动、
明确报告验证结果的约束。

## 远程仓库授权

授权说明位于 [`GITHUB_APP_AUTH.md`](./GITHUB_APP_AUTH.md)。该文档记录当前
工作区 GitHub App 的非敏感标识、仓库范围、已验证权限和运行时规则，不记录
密钥或访问 Token。

- 当前 checkout 的 `.git/config` 使用仓库本地凭据助手，不修改全局 Git 配置。
- 需要访问远程仓库时，凭据助手从仓库外的私钥文件读取密钥，运行时生成短时
  RS256 JWT，再交换为仓库范围的 Installation Token。
- 私钥位于仓库外；JWT 和 Installation Token 只在运行时使用，不写入或提交到
  仓库。
- 向上游提交 PR 时，应使用已授权的个人 Fork 或 remote；当前工作区的授权
  记录不等同于对上游仓库的写入权限。
```

- [ ] **Step 3: Review the README against the design constraints**

Confirm that the document contains the project purpose, Submodule workflow, both
Agent definitions and models, the authorization document link and runtime flow,
and no App IDs, Installation IDs, private keys, JWTs or Tokens.

### Task 2: Verify the documentation

**Files:**
- Verify: `README.md`
- Verify: `.gitmodules`
- Verify: `.opencode/agent/gpt-5.6-luna-max.md`
- Verify: `.opencode/agent/deepseek-v4-flash-0731-max.md`
- Verify: `GITHUB_APP_AUTH.md`

- [ ] **Step 1: Check formatting and required references**

Run:

```bash
git diff --check && \
rg -n "Nexus-Editor|gpt-5\.6-luna-max|deepseek-v4-flash-0731-max|GITHUB_APP_AUTH|Installation Token" README.md
```

Expected: `git diff --check` exits successfully and the search reports the
Submodule, both Agent names, the authorization document, and the token-handling
rule.

- [ ] **Step 2: Check the Submodule record and worktree state**

Run:

```bash
git submodule status --recursive
git status --short
```

Expected: the Submodule reports a resolved commit; `README.md` is the only task
file added or modified, and existing `.DS_Store` files remain untracked and
unstaged.

### Task 3: Commit the README

**Files:**
- Add: `README.md`

- [ ] **Step 1: Inspect the final staged scope**

Run:

```bash
git diff -- README.md
git status --short
```

Expected: the diff contains only the new README and no `.DS_Store` file is
staged.

- [ ] **Step 2: Commit the documentation**

Run:

```bash
git add README.md
git commit -m "Add contribution workspace README"
```

Expected: Git creates one commit containing only `README.md`.

- [ ] **Step 3: Verify the commit**

Run:

```bash
git show --format= --check HEAD
git show --stat --oneline HEAD
git status --short --branch
```

Expected: no whitespace errors; the commit lists only `README.md`; the existing
`.DS_Store` files remain untracked and the new commit is ahead of the remote
until an explicit push is requested.
