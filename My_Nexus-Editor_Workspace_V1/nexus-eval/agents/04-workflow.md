# PR Workflow & Mechanics
## 3. PR Workflow

### 3.1 When OpenSpec is required

You **must** open an OpenSpec proposal under `openspec/changes/<id>/` before the implementation PR when:

- introducing a new capability (new plugin, new public API)
- making a breaking change to a public API
- cross-package architecture work, or significant performance/security work

You do **not** need a proposal for: bug fixes, internal refactors, dependency bumps, test/doc additions.

See `openspec/AGENTS.md` for the full workflow.

### 3.2 PR checklist

- [ ] Title follows Conventional Commits (same rules as commit messages)
- [ ] CLA signed (the bot will prompt first-time contributors) — see [`GOVERNANCE.md` §6.1](./GOVERNANCE.md#61-contributor-license-agreement-cla)
- [ ] AI-generated code disclosed if applicable ([`GOVERNANCE.md` §6.2](./GOVERNANCE.md#62-ai-generated-code))
- [ ] New runtime dependencies listed with license & rationale ([`GOVERNANCE.md` §6.3](./GOVERNANCE.md#63-new-runtime-dependencies))
- [ ] Change matches project scope ([`GOVERNANCE.md` §4](./GOVERNANCE.md#4-scope-policy))
- [ ] Description explains **why**, not just **what**
- [ ] Tests added (see test matrix below)
- [ ] `pnpm test` passes
- [ ] Affected packages build (`pnpm build`)
- [ ] Public-API changes update the relevant `packages/*/README.md`
- [ ] If touching `packages/core/src/live-preview-table.ts`, walk through the 12 Table Widget rules in `CLAUDE.md`
- [ ] New / changed capability → linked OpenSpec change id

### 3.3 Test matrix

| Change type | Required | Recommended |
|---|---|---|
| `packages/core` rendering | vitest unit tests | manual check in electron-demo |
| `plugin-*` | vitest unit tests | demo integration |
| React/Vue SDK | framework unit tests | mount in demo |
| Live-preview / table / wikilinks | **regression test required** | manual mouse interactions |
| Docs / config only | — | — |

---
## 2. Branches & Commits

### Branch naming

```
<type>/<scope>-<short-desc>
```

Examples: `feat/toolbar-list-toggle`, `fix/search-regex-escape`, `docs/roadmap-update`.

### Commit message (Conventional Commits)

```
<type>(<scope>): <subject>
```

- **type**: `feat` / `fix` / `perf` / `refactor` / `test` / `docs` / `chore` / `ci` / `build`
- **scope** (must be one of the following or omitted):

  | scope | Maps to |
  |---|---|
  | `core` | `packages/core` |
  | `react` | `packages/react` |
  | `vue` | `packages/vue` |
  | `gfm` | `packages/preset-gfm` |
  | `history` / `search` / `slash` / `toolbar` / `math` / `vim` / `wordcount` | corresponding `plugin-*` |
  | `electron` | `apps/electron-demo` |
  | `live-preview` / `wikilinks` / `image` | core subsystems (historical usage) |
  | `openspec` | `openspec/` |

- **subject**: imperative, English, ≤ 72 chars, no trailing period.

Reference commits already in `main`:

```
feat(image): Obsidian-style image preview with |width syntax and drag-resize
fix(live-preview): height-neutral decorations + always-on block widgets
test(live-preview): regression tests for click-drift invariants
```

### When to split a PR

- One PR, one concern. Don't mix refactor with feature work.
- Multi-package coordinated changes may live in one PR, but the description must have a per-package section.

---

## Build/test commands (authoritative)
"scripts": {
    "build": "pnpm --filter @floatboat/nexus-core build && pnpm --filter @floatboat/nexus-react build && pnpm --filter @floatboat/nexus-vue build && pnpm --filter @floatboat/nexus-preset-gfm build && pnpm --filter @floatboat/nexus-plugin-slash build && pnpm --filter @floatboat/nexus-plugin-history build && pnpm --filter @floatboat/nexus-plugin-search build && pnpm --filter @floatboat/nexus-plugin-toolbar build && pnpm --filter @floatboat/nexus-plugin-math build && pnpm --filter @floatboat/nexus-plugin-vim build && pnpm --filter @floatboat/nexus-plugin-wordcount build",
    "typecheck": "pnpm -r exec tsc --noEmit",
    "test": "vitest run",
    "dev:electron-demo": "pnpm --filter @floatboat/nexus-electron-demo dev",
    "build:electron-demo": "pnpm --filter @floatboat/nexus-electron-demo build",
    "publish:packages": "pnpm -r --filter \"./packages/*\" publish --access public --no-git-checks"
  },
  "devDependencies": {
    "@testing-library/react": "^16.3.0",
    "@types/mdast": "^4.0.4",
    "@types/node": "^24.6.0",
    "@types/react": "^19.2.2",
    "@types/react-dom": "^19.2.2",
    "@vue/test-utils": "^2.4.6",
    "jsdom": "^25.0.1",
    "react": "^19.2.0",
    "react-dom": "^19.2.0",
    "tsup": "^8.5.0",
    "typescript": "^5.9.3",
    "vue": "^3.5.22",
    "vitest": "^2.1.9"
  }
}
name: CI

on:
  push:
    branches:
      - main
  pull_request:
    branches:
      - main
  release:
    types:
      - published
  workflow_dispatch:
    inputs:
      publish:
        description: "Publish packages to npm"
        required: false
        default: false
        type: boolean

permissions:
  contents: read

env:
  NODE_VERSION: "22"
  PNPM_VERSION: "9.15.4"

jobs:
  verify:
    name: Test and Build
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Setup pnpm
        uses: pnpm/action-setup@v4
        with:
          version: ${{ env.PNPM_VERSION }}

      - name: Setup Node
        uses: actions/setup-node@v4
        with:
          node-version: ${{ env.NODE_VERSION }}
          cache: pnpm

      - name: Install dependencies
        run: pnpm install --frozen-lockfile

      - name: Typecheck
        run: pnpm typecheck

      - name: Run tests
        run: pnpm test

      - name: Build packages
        run: pnpm build

      - name: Build Electron demo
        run: pnpm build:electron-demo

  publish:
    name: Publish Packages
    runs-on: ubuntu-latest
    needs: verify
    if: github.event_name == 'release' || (github.event_name == 'workflow_dispatch' && inputs.publish)
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Setup pnpm
        uses: pnpm/action-setup@v4
        with:
          version: ${{ env.PNPM_VERSION }}

      - name: Setup Node
        uses: actions/setup-node@v4
        with:
          node-version: ${{ env.NODE_VERSION }}
          cache: pnpm
          registry-url: https://registry.npmjs.org/

      - name: Install dependencies
        run: pnpm install --frozen-lockfile

      - name: Build packages
        run: pnpm build

      - name: Configure npm auth
        run: npm config set //registry.npmjs.org/:_authToken "$NPM_TOKEN"
        env:
          NPM_TOKEN: ${{ secrets.NPM_KEY }}

      - name: Publish packages
        run: pnpm publish:packages
        env:
          NODE_AUTH_TOKEN: ${{ secrets.NPM_KEY }}
