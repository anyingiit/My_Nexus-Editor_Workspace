# Live Preview Spec

## ADDED Requirements

### Requirement: support nested inline formatting

Nested markdown formatting like `***bold italic***` or `**_mixed_**` SHALL render with combined styles (bold + italic) and correctly detect marker boundaries from AST nesting depth.

#### Scenario: Nested bold-italic renders correctly
- **WHEN** document contains `***text***`
- **THEN** the text SHALL be rendered with both bold and italic styles
- **AND** markers `***` SHALL be hidden when cursor is outside

#### Scenario: Mixed marker nesting
- **WHEN** document contains `**_text_**`
- **THEN** the outer bold markers `**` SHALL be hidden
- **AND** the inner italic markers `_` SHALL be hidden
- **AND** the text SHALL have both bold and italic styles

### Requirement: render footnote references and definitions

GFM footnote syntax `[^1]` and `[^1]: text` SHALL be rendered as superscript numbers and bottom-of-document definitions respectively.

#### Scenario: Footnote reference renders as superscript
- **WHEN** document contains `text[^1]`
- **THEN** `[^1]` SHALL be rendered as a superscript `1`

#### Scenario: Footnote definition renders at bottom
- **WHEN** document contains `[^1]: definition text`
- **THEN** it SHALL be rendered as a small-text definition block

### Requirement: render GFM autolinks

Bare URLs recognized by remark-gfm SHALL be rendered as clickable links with the same styling as `[text](url)` links.

#### Scenario: Autolink renders as clickable
- **WHEN** document contains `https://example.com`
- **THEN** the URL SHALL be rendered with link styling (blue, underline)

### Requirement: support Ctrl+Click on mark-decorated links

Links rendered via mark decorations (not widget replacement) SHALL support Ctrl+Click to open in a new tab.

#### Scenario: Ctrl+Click opens link
- **WHEN** user Ctrl+Clicks on a mark-decorated link
- **THEN** the link URL SHALL open in a new browser tab

### Requirement: support indented code blocks

Code blocks created with 4-space indentation (no fences) SHALL receive the same styling as fenced code blocks (background, monospace font) without syntax highlighting or language label.

#### Scenario: Indented code block renders with background
- **WHEN** document contains a line indented with 4 spaces
- **THEN** the line SHALL have code block background styling

### Requirement: support code block and heading folding

Users SHALL be able to collapse/expand code blocks and heading sections.

#### Scenario: Fold code block
- **WHEN** user clicks the fold indicator on a code block
- **THEN** the code block body SHALL collapse to show only the language label

#### Scenario: Fold heading section
- **WHEN** user clicks the fold indicator on a heading
- **THEN** content below the heading until the next same-or-higher level heading SHALL be hidden

### Requirement: auto-continue markdown structures on Enter

Pressing Enter inside list items, blockquotes, and ordered lists SHALL insert the appropriate prefix on the new line.

#### Scenario: Continue unordered list
- **WHEN** cursor is at end of a `- item` line and Enter is pressed
- **THEN** a new line with `- ` prefix SHALL be inserted

#### Scenario: Exit list on empty item
- **WHEN** cursor is on an empty `- ` line and Enter is pressed
- **THEN** the `- ` prefix SHALL be removed and the list SHALL be exited

#### Scenario: Continue blockquote
- **WHEN** cursor is at end of a `> text` line and Enter is pressed
- **THEN** a new line with `> ` prefix SHALL be inserted

### Requirement: support link and image hover previews

Hovering over a rendered link SHALL show a tooltip with the full URL. Hovering over an image reference SHALL show a small thumbnail.

#### Scenario: Link hover tooltip
- **WHEN** user hovers over a rendered link
- **THEN** a tooltip SHALL appear showing the full URL

#### Scenario: Image hover thumbnail
- **WHEN** user hovers over an image markdown reference
- **THEN** a small thumbnail preview SHALL appear
