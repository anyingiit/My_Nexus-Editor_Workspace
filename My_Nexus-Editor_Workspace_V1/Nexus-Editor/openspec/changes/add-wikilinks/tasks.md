# Implementation Tasks

## 1. Core package — wikilinks extension
- [x] 1.1 Create `packages/core/src/wikilinks.ts` with `WIKILINK_RE` scanner, `WikiLinkRange` type, and `scanWikiLinks(doc)` helper
- [x] 1.2 Implement `buildWikiLinkDecorations(doc, selection, ranges, resolve)` producing cursor-aware mark decorations (`[[`/`]]` hidden when cursor off-line, shown when on-line; alias-only visible text)
- [x] 1.3 Implement `createWikilinksExtension({ resolve, onNavigate, suggest })` returning `Extension[]` with StateField + `mousedown` handler + `autocompletion`
- [x] 1.4 Export `createWikilinksPlugin(options)` NexusPlugin from `wikilinks.ts`
- [x] 1.5 Wire both exports into `packages/core/src/index.ts`
- [x] 1.6 Unit tests in `packages/core/test/wikilinks.test.ts` covering: plain `[[a]]`, alias `[[a|label]]`, resolved-vs-unresolved style, cursor-off hides markers, cursor-on shows markers, adjacent punctuation, escape sequences `\[[`, navigation callback fires with correct target
- [x] 1.7 Autocomplete unit test: typing `[[q` produces suggest query `q` and inserts `target]]` on accept

## 2. Electron main — batch read IPC
- [x] 2.1 Add `ipcMain.handle("vault:read-all", ...)` in `apps/electron-demo/electron/main.ts` returning `{ path, content }[]` for all vault markdown files
- [x] 2.2 Reuse `scanDirectory` + `SUPPORTED_EXT`; enforce `assertInsideVault` on each path read
- [x] 2.3 Expose `vault.readAll()` in preload bridge and bridge typings

## 3. Electron renderer — link index
- [x] 3.1 Create `apps/electron-demo/src/renderer/link-index.ts`: `LinkIndex` class with `rebuild(files)`, `updateFile(path, content)`, `removeFile(path)`, `resolve(name, fromPath)`, `getBacklinks(targetPath)`, `getAllNoteNames()`, `subscribe(listener)`
- [x] 3.2 Use the core `scanWikiLinks` helper to parse each file's contents
- [x] 3.3 Resolution order: (a) exact absolute path match, (b) relative path from fromPath, (c) same-directory basename, (d) globally-unique basename, (e) null (unresolved)
- [x] 3.4 Fire `notify()` when maps change so subscribers (backlinks panel, editor redecorate) refresh

## 4. Electron renderer — backlinks panel
- [x] 4.1 Create `apps/electron-demo/src/renderer/backlinks-panel.ts` exposing `createBacklinksPanel({ index, onOpenFile, getActiveFile })`
- [x] 4.2 Panel shows: file name (linkified) + first matching line snippet per backlink; empty state when no backlinks
- [x] 4.3 Re-render on `index.subscribe` notifications and `setActiveFile`
- [x] 4.4 Add toolbar toggle button in `app.ts` (icon 🔗) and include the panel in the `main-area` layout

## 5. Electron renderer — wire into editor
- [x] 5.1 Pass `createWikilinksPlugin({ resolve, suggest, onNavigate })` into `createEditor` inside `editor-shell.ts`
- [x] 5.2 `resolve` calls `linkIndex.resolve(name, state.activeFile)`; `onNavigate` dispatches to the same `handleVaultFileOpen` used by the tree, creating the file first if unresolved
- [x] 5.3 `suggest(query)` returns `linkIndex.getAllNoteNames()` filtered by fuzzy substring match
- [x] 5.4 After every `onChange`, push the new content into `linkIndex.updateFile(activeFile, content)` so backlinks update live
- [x] 5.5 After a successful `vault:list`, call `window.nexusDemo.vault.readAll()` and seed the index

## 6. Build + verify
- [x] 6.1 `openspec validate add-wikilinks --strict` passes
- [x] 6.2 `pnpm -w build` passes (core + electron-demo + presets)
- [x] 6.3 `pnpm test` passes including the new wikilinks suite (159/159)
- [ ] 6.4 Manual smoke: open the demo, create `A.md` with `[[B]]`, verify B becomes resolved after creation, verify backlinks panel shows A when viewing B
