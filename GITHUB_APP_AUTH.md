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

## Repository-Local Commit Identity

Normal Git commits in this checkout use:

- Name: `MyAnyAgent[bot]`
- Email: `312959697+myanyagent[bot]@users.noreply.github.com`

The identity and App credential helper are stored only in this checkout's
`.git/config` and `.git/` directory. They do not change global Git settings or
other repositories. The helper mints a fresh installation token on demand and
never persists it.
