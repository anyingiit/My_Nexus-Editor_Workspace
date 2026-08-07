# Code Style (automated CI + repo conventions)

Priority: **automated CI requirements > dev-set validation > repo style > TypeScript best practices**

## CI / Lint (hard)
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

## Repository style (from CONTRIBUTING.md §4)
## 4. Code Style

- TypeScript strict mode; public exports must have explicit types.
- No "what" comments, no PR/issue back-references in comments. Only write a comment when the **why** is non-obvious (see CLAUDE.md "Doing tasks").
- UI changes must be exercised in the electron-demo. Type checks alone don't validate UI behavior.

---

## Testing conventions (from AGENTS.md/CLAUDE.md)
<!-- OPENSPEC:START -->
# OpenSpec Instructions

These instructions are for AI assistants working in this project.

Always open `@/openspec/AGENTS.md` when the request:
- Mentions planning or proposals (words like proposal, spec, change, plan)
- Introduces new capabilities, breaking changes, architecture shifts, or big performance/security work
- Sounds ambiguous and you need the authoritative spec before coding

Use `@/openspec/AGENTS.md` to learn:
- How to create and apply change proposals
- Spec format and conventions
- Project structure and guidelines

Keep this managed block so 'openspec update' can refresh the instructions.

<!-- OPENSPEC:END -->

## Superpowers

- Superpowers is installed for Codex on this machine via `~/.agents/skills/superpowers`.
- If the current runtime can access those skills, prefer the relevant Superpowers workflow for planning, debugging, code review, and execution.
- Keep OpenSpec instructions as the source of truth for spec-driven changes in this project.

## Table Widget Development Rules

When modifying `packages/core/src/live-preview-table.ts`:

1. **Never clear state that was just set in the same flow.** If a function sets `rangeStart`, don't call another function that nullifies it before the value is used. Trace the full lifecycle (mousedown → mousemove → mouseup) before changing cleanup logic.

2. **rAF polling must respect all active interaction states.** Before clearing state in a rAF loop, check ALL flags: `isRangeSelecting`, `cellMouseDown`, `self.editing`. Missing any one causes the "works then immediately breaks" pattern.

3. **Never use inline border styles for drag indicators.** Use `box-shadow` or absolute-positioned overlay divs. Setting `border*` on table cells destroys structural borders on cleanup.

4. **`contentEditable` must be off by default on table cells.** Only activate on mousedown→focus, deactivate on blur. Otherwise browser text selection enters cells from outside.

5. **HTML5 Drag API is forbidden for table grips.** Use mousedown/mousemove/mouseup custom drag. HTML5 drag creates uncontrollable ghost images and can't be constrained to the table.

6. **Column grip pills must be positioned relative to header cells** (via absolute overlay or inline in header), NOT in a separate `<tr>` row — separate rows don't align with content column widths.

7. **Test every change with ALL interaction paths:** click-to-edit, drag-to-select-range, grip-click-to-select-column, grip-drag-to-reorder, click-outside-to-deselect, delete-key-on-selection.

8. **Any mouse interaction that spans multiple frames MUST set `self.editing = true` and increment `tableEditingCount`.** This prevents CM6 from recreating the widget DOM mid-interaction (via `eq()` returning true). Release in the mouseup handler. Without this, CM6 may destroy the DOM between mousedown and mousemove, leaving event listeners pointing at detached nodes.

9. **Cell `blur` handlers MUST check for active grip drag before clearing `editing`.** Whe
