# Ghost & Escape Demo

Back to [[index]].

## Unresolved target

Clicking [[Ghost Note]] should render with a **dashed red underline** because
the target doesn't exist yet. When you click it, the app creates
`Ghost Note.md` next to this file and opens it.

Another ghost with alias: [[NonExistent|phantom]].

## Escape sequence (must NOT become a link)

The string `\[[NotALink]]` on this line should stay literal — no decoration,
no click target.

## Code fence (v1 caveat)

The following code fence intentionally contains `[[InsideFence]]` — v1 still
detects it as a link. This is called out in `design.md` (§ Risks / Trade-offs)
as an acceptable v1 limitation; v2 will skip code ranges.

```md
see [[InsideFence]]
```
