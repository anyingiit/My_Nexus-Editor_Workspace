# Theming Spec

## ADDED Requirements

### Requirement: provide a theme configuration API

A `NexusTheme` interface SHALL define all customizable visual properties (colors, fonts, spacing, border radius). `EditorConfig.theme` SHALL accept a theme object.

#### Scenario: Apply light theme
- **WHEN** editor is created with `theme: lightTheme`
- **THEN** all editor elements SHALL use the light theme color values

#### Scenario: Apply dark theme
- **WHEN** editor is created with `theme: darkTheme`
- **THEN** all editor elements SHALL use dark background and light text colors

#### Scenario: Custom theme
- **WHEN** editor is created with a custom `NexusTheme` object
- **THEN** all specified properties SHALL override defaults

### Requirement: provide built-in light and dark themes

Two preset themes SHALL be exported: `lightTheme` and `darkTheme`.

#### Scenario: Light theme colors
- **WHEN** `lightTheme` is applied
- **THEN** editor background SHALL be white/light
- **AND** code block background SHALL be `#f6f8fa` or similar light gray
- **AND** text SHALL be dark (#24292e or similar)

#### Scenario: Dark theme colors
- **WHEN** `darkTheme` is applied
- **THEN** editor background SHALL be dark (#1e1e1e or similar)
- **AND** code block background SHALL be darker gray
- **AND** text SHALL be light (#d4d4d4 or similar)
- **AND** syntax highlighting colors SHALL be adjusted for dark backgrounds

### Requirement: use CSS custom properties for all visual values

All hardcoded colors, fonts, and spacing SHALL be replaced with CSS custom properties (`--nexus-*`) that the theme system sets on the editor root element.

#### Scenario: CSS variables are set
- **WHEN** a theme is applied
- **THEN** the editor root element SHALL have CSS custom properties for all theme values
- **AND** all internal styles SHALL reference these properties

### Requirement: support runtime theme switching

Themes SHALL be switchable at runtime without destroying and recreating the editor.

#### Scenario: Switch theme at runtime
- **WHEN** `editor.setTheme(darkTheme)` is called
- **THEN** all visual elements SHALL update to dark theme immediately
- **AND** document content and cursor position SHALL be preserved
