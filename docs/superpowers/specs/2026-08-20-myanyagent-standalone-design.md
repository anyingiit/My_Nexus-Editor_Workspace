# myanyagent → standalone repository — design

> Status: approved 2026-08-20. Target: `https://github.com/anyingiit/myanyagent` (exists, empty).

## Context

`myanyagent/` in this workspace is a complete, reusable GitHub App git-auth tool
(credential helper + bootstrap + status + installer + tests, 7 commits of
history, merged via PR #1). It is promoted to an independent project so it can
be installed, versioned, and discovered on its own.

## Decisions (user-approved)

| Decision | Choice |
|---|---|
| Git history | Preserve via subdirectory filter (8 commits, paths rewritten to root) |
| Local location | `~/Workspace/myanyagent` (sibling of this workspace) |
| Workspace `myanyagent/` dir | Deleted after the new repo is pushed and verified |
| Untracked `docs/contributing-to-third-party-repos.md` | Included in the new repo |

## Approach

1. **Extract**: `git clone --no-local` the workspace to `~/Workspace/myanyagent`,
   then `git filter-repo --subdirectory-filter myanyagent` (installed into an
   isolated venv under /tmp; fallback: `git filter-branch --subdirectory-filter`
   + manual ref cleanup). Only the clone is touched; the workspace is never
   filtered.
2. **Standalone-ify**: copy the untracked `docs/` in; rewrite README paths
   (`myanyagent/install.sh` → `install.sh`, `myanyagent/test/…` → `test/…`);
   add `LICENSE` (MIT, copied from workspace), `.gitignore`, and a self-describing
   `.myanyagent.toml` (`repository = "anyingiit/myanyagent"`, same
   `installation_id = "151195329"`, same bot identity). Grep for and fix any
   residual `myanyagent/` self-references.
3. **Dogfood push**: `git remote add origin https://github.com/anyingiit/myanyagent.git`,
   run `myanyagent-bootstrap` (machine tool configures bot identity + credential
   helper), run all three tests, commit as the bot, push `main`.
4. **Workspace cleanup**: `git rm -r myanyagent` (plus the untracked `docs/`
   residue), commit, push. Historical records under `docs/superpowers/` are
   intentionally left untouched.

## Verification

- `git log --oneline` in the new repo shows exactly the 8 myanyagent commits.
- `node --test test/helper.test.cjs && sh test/bootstrap.test.sh && sh test/status.test.sh` pass.
- `git ls-remote origin` shows `main` on GitHub; `myanyagent-status` is green
  in both repos.

## Risks

- **Installation coverage**: workspace docs record the App as
  `repository_selection: all` on `@anyingiit` (candidate evidence, not yet
  re-verified). The bootstrap/push is the empirical test; a 403/404 means the
  repo must be added in the App's settings first.
- **Filter mishap**: only the clone is filtered; delete and re-clone to redo.
- **Ordering**: workspace deletion happens only after the remote is verified.
- Secrets: the private key never enters any repo; `.myanyagent.toml` carries no
  secrets (already public convention).
