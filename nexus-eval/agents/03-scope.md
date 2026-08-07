# Scope Constraints (replaces 'implementation difficulty')

Nexus is a **headless, AST-driven Markdown editor engine**.

## In scope
- packages/core (CM6 state, AST, live preview, widget API, events)
- packages/preset-gfm, packages/plugin-* (editor features)
- packages/react / packages/vue (thin bindings, lockstep updates required)
- apps/electron-demo (demonstration only)

## OUT of scope (rejected even if well-built — see GOVERNANCE.md §4)
### What is **not** in scope (and will be rejected even if technically well-built)

1. **AI / LLM integrations** of any kind — neither in `packages/` nor in `apps/electron-demo`. This includes: text generation, AI rewriting, autocomplete via cloud LLMs, agent panels, embedded AI tools. These are the responsibility of the host application that depends on Nexus.
2. **Bundled SDKs for specific vendors** — OpenAI, Anthropic, Volcano/Doubao, OpenRouter, cloud storage SDKs, etc. Adapters and pluggable interfaces in `core` are acceptable; bundling a specific vendor is not.
3. **General-purpose UI component libraries** in this repository (toast, dialog, modal, etc.). Nexus is headless; UI belongs in the host application or in dedicated third-party packages.
4. **Product features** that are not editor primitives — e.g. notebook management, cloud sync UI, account systems, in-app purchase flows.
5. **Schema validation / content linting** beyond what the AST already exposes. Hosts can write these on top of `editor.getAst()`.

If you need any of the above, build it in your host application using Nexus as a dependency.

### Demo is not a product

`apps/electron-demo` exists so people can see and try the engine. It is **not** a reference desktop product. PRs that add product-level surface area (file management UI, AI sidebars, agent panels, settings systems) to the demo are out of scope.

## One PR = one concern
- Do NOT mix refactor with feature work in one PR (CONTRIBUTING §2).
- Multi-package coordinated changes allowed but description must have per-package sections.

## Size limits (derived from the train split's finalized PR distribution, P90)
- Single PR should touch **at most ~20 files** and **~4100 changed lines**
  (files P90 = 19, changed-lines P90 = 2543 over the train finalized set).
- A PR exceeding these limits should be split into smaller focused PRs.
- Basis note: the train merged-only sample is n=4 (files P90=29,
  lines P90=2244), still small; the limit therefore uses the whole train
  finalized distribution (n=47) and is documented, not silently swapped
  to the merged-only sample.
