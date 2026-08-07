# Core AST Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first working `@nexus/core` package for `Nexus-Editor`, including a minimal CodeMirror 6-backed editor lifecycle, Markdown document hooks, a remark-based AST pipeline, and layered plugin registration.

**Architecture:** The implementation will scaffold a small pnpm workspace with a single published package, `packages/core`, and a shared Vitest test setup. The core package will wrap a minimal CodeMirror 6 `EditorView`, use CM6 state as the canonical Markdown source, run a remark parser over the current document after changes, and compose plugin-provided shortcut, remark, and CM6 extension contributions through one editor factory.

**Tech Stack:** TypeScript, pnpm workspaces, Vitest, jsdom, CodeMirror 6, unified, remark-parse, mdast

---

### Task 1: Scaffold the workspace and package boundaries

**Files:**
- Create: `package.json`
- Create: `pnpm-workspace.yaml`
- Create: `tsconfig.base.json`
- Create: `vitest.config.ts`
- Create: `packages/core/package.json`
- Create: `packages/core/tsconfig.json`
- Create: `packages/core/src/index.ts`
- Create: `packages/core/src/types.ts`
- Create: `packages/core/src/editor.ts`
- Create: `packages/core/test/editor.test.ts`

- [x] **Step 1: Write the failing workspace smoke test**

```ts
import { describe, expect, it } from "vitest";
import { createEditor } from "../src/index";

describe("createEditor", () => {
  it("creates an editor with the initial document", () => {
    const container = document.createElement("div");
    const editor = createEditor({ container, initialValue: "# Hello" });

    expect(editor.getDocument()).toBe("# Hello");
    editor.destroy();
  });
});
```

- [x] **Step 2: Run test to verify it fails**

Run: `pnpm exec vitest run packages/core/test/editor.test.ts`
Expected: FAIL because the workspace and exported editor factory do not exist yet

- [x] **Step 3: Write minimal workspace scaffolding and editor factory**

```ts
export interface EditorConfig {
  container: HTMLElement;
  initialValue?: string;
}

export interface EditorAPI {
  getDocument(): string;
  destroy(): void;
}

export function createEditor(config: EditorConfig): EditorAPI {
  const view = new EditorView({
    parent: config.container,
    state: EditorState.create({
      doc: config.initialValue ?? "",
    }),
  });

  return {
    getDocument() {
      return view.state.doc.toString();
    },
    destroy() {
      view.destroy();
    },
  };
}
```

- [x] **Step 4: Run test to verify it passes**

Run: `pnpm exec vitest run packages/core/test/editor.test.ts`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add package.json pnpm-workspace.yaml tsconfig.base.json vitest.config.ts packages/core/package.json packages/core/tsconfig.json packages/core/src/index.ts packages/core/src/types.ts packages/core/src/editor.ts packages/core/test/editor.test.ts
git commit -m "feat: scaffold core editor workspace"
```

### Task 2: Add document mutation and lifecycle hook semantics

**Files:**
- Modify: `packages/core/src/types.ts`
- Modify: `packages/core/src/editor.ts`
- Modify: `packages/core/src/index.ts`
- Modify: `packages/core/test/editor.test.ts`

- [x] **Step 1: Write the failing document and lifecycle tests**

```ts
it("emits change, focus, and blur hooks with canonical document values", () => {
  const container = document.createElement("div");
  const events: string[] = [];
  const docs: string[] = [];
  const editor = createEditor({
    container,
    initialValue: "start",
    onChange(doc) {
      docs.push(doc);
    },
    onFocus() {
      events.push("focus");
    },
    onBlur() {
      events.push("blur");
    },
  });

  editor.focus();
  editor.setDocument("next");
  editor.blur();

  expect(editor.getDocument()).toBe("next");
  expect(docs).toEqual(["next"]);
  expect(events).toEqual(["focus", "blur"]);
  editor.destroy();
});
```

- [x] **Step 2: Run test to verify it fails**

Run: `pnpm exec vitest run packages/core/test/editor.test.ts -t "emits change, focus, and blur hooks with canonical document values"`
Expected: FAIL because focus, blur, and setDocument behavior are not implemented

- [x] **Step 3: Write minimal lifecycle and mutation behavior**

```ts
export interface EditorConfig {
  container: HTMLElement;
  initialValue?: string;
  onChange?: (doc: string, ast: Root) => void;
  onFocus?: () => void;
  onBlur?: () => void;
}

export interface EditorAPI {
  getDocument(): string;
  setDocument(next: string): void;
  focus(): void;
  blur(): void;
  destroy(): void;
}
```

```ts
setDocument(next) {
  view.dispatch({
    changes: { from: 0, to: view.state.doc.length, insert: next },
  });
},
focus() {
  view.focus();
},
blur() {
  (view.contentDOM as HTMLElement).blur();
},
```

- [x] **Step 4: Run test to verify it passes**

Run: `pnpm exec vitest run packages/core/test/editor.test.ts`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add packages/core/src/types.ts packages/core/src/editor.ts packages/core/src/index.ts packages/core/test/editor.test.ts
git commit -m "feat: add core document lifecycle hooks"
```

### Task 3: Add the Markdown-to-AST pipeline

**Files:**
- Modify: `packages/core/package.json`
- Modify: `packages/core/src/types.ts`
- Modify: `packages/core/src/editor.ts`
- Modify: `packages/core/test/editor.test.ts`

- [x] **Step 1: Write the failing AST tests**

```ts
it("emits a parsed AST for the current markdown document", () => {
  const container = document.createElement("div");
  const astTypes: string[] = [];
  const editor = createEditor({
    container,
    onChange(_doc, ast) {
      astTypes.push(ast.type);
    },
  });

  editor.setDocument("# Heading");

  expect(astTypes).toEqual(["root"]);
  editor.destroy();
});
```

```ts
it("keeps the editor usable when the parser throws", () => {
  const container = document.createElement("div");
  const docs: string[] = [];
  const editor = createEditor({
    container,
    parser: {
      parse() {
        throw new Error("boom");
      },
    },
    onChange(doc) {
      docs.push(doc);
    },
  });

  editor.setDocument("after failure");

  expect(editor.getDocument()).toBe("after failure");
  expect(docs).toEqual(["after failure"]);
  editor.destroy();
});
```

- [x] **Step 2: Run test to verify it fails**

Run: `pnpm exec vitest run packages/core/test/editor.test.ts -t "AST"`
Expected: FAIL because AST parsing and parser injection do not exist

- [x] **Step 3: Write minimal parsing support**

```ts
import { unified } from "unified";
import remarkParse from "remark-parse";

const defaultParser = {
  parse(markdown: string): Root {
    return unified().use(remarkParse).parse(markdown) as Root;
  },
};
```

```ts
function parseDocument(parser: ParserLike, markdown: string, fallback: Root): Root {
  try {
    return parser.parse(markdown);
  } catch {
    return fallback;
  }
}
```

- [x] **Step 4: Run test to verify it passes**

Run: `pnpm exec vitest run packages/core/test/editor.test.ts`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add packages/core/package.json packages/core/src/types.ts packages/core/src/editor.ts packages/core/test/editor.test.ts
git commit -m "feat: add markdown ast pipeline"
```

### Task 4: Add layered plugin registration and composition tests

**Files:**
- Modify: `packages/core/src/types.ts`
- Modify: `packages/core/src/editor.ts`
- Modify: `packages/core/src/index.ts`
- Modify: `packages/core/test/editor.test.ts`

- [x] **Step 1: Write the failing plugin composition tests**

```ts
it("composes remark and shortcut plugin contributions", () => {
  const container = document.createElement("div");
  let shortcutResult = false;
  const editor = createEditor({
    container,
    plugins: [
      {
        name: "shortcut",
        shortcuts: [
          {
            key: "Mod-k",
            run(api) {
              api.setDocument("shortcut-ran");
              shortcutResult = true;
              return true;
            },
          },
        ],
      },
    ],
  });

  expect(editor.runShortcut("Mod-k")).toBe(true);
  expect(shortcutResult).toBe(true);
  expect(editor.getDocument()).toBe("shortcut-ran");
  editor.destroy();
});
```

- [x] **Step 2: Run test to verify it fails**

Run: `pnpm exec vitest run packages/core/test/editor.test.ts -t "composes remark and shortcut plugin contributions"`
Expected: FAIL because plugin registration and shortcut execution do not exist

- [x] **Step 3: Write minimal plugin registration support**

```ts
export interface NexusPlugin {
  name: string;
  shortcuts?: Array<{ key: string; run: (editor: EditorAPI) => boolean }>;
  remarkPlugins?: Plugin[];
  cmExtensions?: Extension[];
}
```

```ts
runShortcut(key) {
  const shortcut = shortcuts.find((entry) => entry.key === key);
  return shortcut ? shortcut.run(api) : false;
},
```

- [x] **Step 4: Run test to verify it passes**

Run: `pnpm exec vitest run packages/core/test/editor.test.ts`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add packages/core/src/types.ts packages/core/src/editor.ts packages/core/src/index.ts packages/core/test/editor.test.ts
git commit -m "feat: add layered plugin registration"
```
