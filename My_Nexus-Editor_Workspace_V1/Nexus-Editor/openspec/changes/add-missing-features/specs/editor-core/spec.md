# Editor Core Spec

## ADDED Requirements

### Requirement: provide an internationalization framework

All user-visible strings SHALL be configurable via a locale object. Built-in `en` and `zh` locales SHALL be provided.

#### Scenario: Default English locale
- **WHEN** no locale is specified
- **THEN** all UI strings SHALL be in English

#### Scenario: Chinese locale
- **WHEN** `locale: zhLocale` is provided in config
- **THEN** context menu items, tooltips, and labels SHALL be in Chinese

#### Scenario: Custom locale
- **WHEN** a partial locale object is provided
- **THEN** specified strings SHALL override defaults
- **AND** unspecified strings SHALL fall back to English

### Requirement: provide a table of contents extraction API

`editor.getTableOfContents()` SHALL return an array of heading entries with level, text, and document positions.

#### Scenario: Extract TOC from document
- **WHEN** document contains h1, h2, and h3 headings
- **THEN** `getTableOfContents()` SHALL return entries in document order
- **AND** each entry SHALL have `level`, `text`, `from`, and `to` properties

#### Scenario: Empty document TOC
- **WHEN** document has no headings
- **THEN** `getTableOfContents()` SHALL return an empty array

### Requirement: provide markdown-to-HTML export

`editor.exportHTML()` SHALL convert the current document to semantic HTML with syntax-highlighted code blocks.

#### Scenario: Export simple document
- **WHEN** document contains headings, bold text, and a code block
- **THEN** `exportHTML()` SHALL return valid HTML with `<h1>`, `<strong>`, and `<pre><code>` elements

#### Scenario: Export preserves code highlighting
- **WHEN** document contains a fenced code block with language
- **THEN** exported HTML SHALL include syntax-highlighted code

### Requirement: provide a runtime theme switching API

`editor.setTheme(theme)` SHALL update the editor's visual theme without recreating the editor instance.

#### Scenario: Set theme preserves state
- **WHEN** `setTheme(darkTheme)` is called
- **THEN** document content, cursor position, and undo history SHALL be preserved
