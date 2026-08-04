# Design: GitHub App Auth and OpenCode Subagents

Date: 2026-08-04

## Scope

Prepare this repository for two callable OpenCode subagents and document the
verified GitHub App access without storing any credential material. The
configuration is repository-scoped; global OpenCode configuration remains
unchanged.

## Components

- `.opencode/agent/deepseek-v4-flash-0731-max.md` selects
  `openrouter/deepseek/deepseek-v4-flash-0731` with variant `max`.
- `.opencode/agent/gpt-5.6-luna-max.md` selects
  `openrouter/openai/gpt-5.6-luna` with variant `max`.
- `GITHUB_APP_AUTH.md` records only non-secret identifiers, repository scope,
  granted permissions, and the runtime authentication flow.

Both agents use `mode: subagent`, remain visible to the primary agent, and
share a short first-principles prompt focused on evidence, minimal changes,
and explicit verification.

## Authentication Flow

At runtime, sign a short-lived RS256 JWT with the configured GitHub App Client
ID and the private key at the user-provided path. Exchange that JWT for a
short-lived installation token scoped to the current repository. Never persist
the JWT or installation token and never copy the private key into the
repository.

## Verification

- Confirm the App endpoint accepts the Client ID and private key signature.
- Confirm the target repository has an installation for the App.
- Confirm an installation token can read repository metadata and report its
  effective permissions.
- Confirm OpenCode resolves both project agents with the requested model IDs
  and `max` variants.

## Non-goals

- Do not change the primary model or global provider configuration.
- Do not make a model request solely for configuration validation.
- Do not commit credentials, bearer tokens, or private key contents.
