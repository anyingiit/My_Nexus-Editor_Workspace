# Note Vault Spec — Wiki Link Additions

## ADDED Requirements

### Requirement: Vault Bulk Read

The electron demo SHALL expose an IPC channel `vault:read-all` that returns every supported markdown file in the active vault as `{ path, content }` pairs, used by the renderer to seed the wiki-link index without N individual round-trips.

#### Scenario: Bulk read returns every markdown file
- **WHEN** the active vault contains `a.md`, `Drafts/b.md`, `image.png`, `.git/config`
- **AND** the renderer invokes `vault:read-all`
- **THEN** the resolved array SHALL include `a.md` and `Drafts/b.md`
- **AND** it SHALL exclude `image.png` and anything inside `.git/`

#### Scenario: Bulk read validates each path is inside the vault
- **WHEN** an internal race produces a candidate path outside the vault
- **THEN** the handler SHALL refuse to read that file and SHALL throw an error
- **AND** no file outside the vault SHALL appear in the result

### Requirement: Wiki Link Index

The electron demo SHALL maintain a vault-scoped bidirectional link index keyed by absolute file path. The index SHALL expose `resolve(name, fromPath)`, `getBacklinks(targetPath)`, `getAllNoteNames()`, `updateFile(path, content)`, `removeFile(path)`, and `subscribe(listener)`.

#### Scenario: Resolving a globally unique basename
- **WHEN** the vault contains exactly one file named `Topics/AI.md`
- **AND** a note references `[[AI]]` from `Journal/2026.md`
- **THEN** `resolve("AI", "Journal/2026.md")` SHALL return the absolute path of `Topics/AI.md`

#### Scenario: Resolving with a same-directory collision prefers the neighbor
- **WHEN** the vault contains `work/Meeting.md` and `personal/Meeting.md`
- **AND** a note at `work/Inbox.md` references `[[Meeting]]`
- **THEN** `resolve("Meeting", "work/Inbox.md")` SHALL return `work/Meeting.md`

#### Scenario: Resolving an unknown name returns null
- **WHEN** no file matches `Ghost` under any rule
- **THEN** `resolve("Ghost", fromPath)` SHALL return null

#### Scenario: Backlinks list every inbound reference
- **WHEN** `work/Inbox.md` contains `[[Meeting]]` and `personal/Diary.md` contains `[[Meeting|notes]]`
- **AND** both resolve to `work/Meeting.md`
- **THEN** `getBacklinks("<abs>/work/Meeting.md")` SHALL include both source paths with their match offsets

#### Scenario: Updating a file rebuilds its outgoing edges
- **WHEN** `a.md` previously linked to `[[X]]` and is updated to link to `[[Y]]`
- **AND** `updateFile("<abs>/a.md", newContent)` is called
- **THEN** the forward map for `a.md` SHALL no longer contain `X`
- **AND** `getBacklinks` for the old target SHALL no longer list `a.md`
- **AND** `getBacklinks` for the new target SHALL list `a.md`

#### Scenario: Removing a file drops its edges
- **WHEN** `removeFile(path)` is called
- **THEN** the file SHALL disappear from both forward and backward maps

### Requirement: Backlinks Panel

The electron demo SHALL render a backlinks panel that lists every file whose content contains a wiki link resolving to the currently-active file, with a one-line context snippet per entry, and that updates whenever the index or the active file changes.

#### Scenario: Panel shows inbound references
- **WHEN** the active file is `work/Meeting.md`
- **AND** `work/Inbox.md` contains the line `See [[Meeting]] tomorrow`
- **THEN** the backlinks panel SHALL show `work/Inbox.md` with snippet `See [[Meeting]] tomorrow`

#### Scenario: Empty state when no backlinks exist
- **WHEN** no file links to the active file
- **THEN** the panel SHALL show an "No backlinks" empty-state label

#### Scenario: Panel refreshes when the user types a new wiki link
- **WHEN** the user types `[[Meeting]]` into `new.md` while `Meeting.md` is active
- **THEN** the panel SHALL include `new.md` without requiring save or reload

#### Scenario: Clicking a backlink opens the source file
- **WHEN** the user clicks a backlinks entry
- **THEN** the editor SHALL load the clicked file's content via the existing dirty-guard flow

### Requirement: Wiki Link Navigation

The electron demo SHALL pass a `resolve` and `onNavigate` pair to the core wikilinks extension so that clicking a wiki link in the editor opens the resolved target, and clicking an unresolved wiki link creates the target file next to the currently-active file before opening it.

#### Scenario: Click on resolved link opens target
- **WHEN** the user clicks `[[Meeting]]` while editing `Inbox.md`
- **AND** the index resolves `Meeting` to `work/Meeting.md`
- **THEN** the editor SHALL load `work/Meeting.md` via the dirty-guarded switch

#### Scenario: Click on unresolved link creates and opens
- **WHEN** the user clicks `[[NewTopic]]` and `resolve` returns null
- **THEN** the handler SHALL call `vault:create-file` with parent = dirname of the active file and name = `NewTopic.md`
- **AND** SHALL then load the newly created file into the editor

#### Scenario: Autocomplete suggests from the vault
- **WHEN** the user types `[[mee`
- **THEN** the completion popup SHALL list all index entries whose basename (case-insensitive) contains `mee`
