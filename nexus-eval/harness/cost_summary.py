#!/usr/bin/env python3
"""Summarize the cost ledger into reports/cost_ledger_summary.md."""
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.normpath(os.path.join(HERE, "..", "reports"))
LEDGER = os.path.join(REPORTS, "cost_ledger.jsonl")


def main():
    total = 0.0
    n = 0
    n_cached = 0
    n_downgrades = 0
    by_model = {}
    by_provider = {}
    by_tag_prefix = {}
    downgrades = []
    tokens_in = 0
    tokens_out = 0
    if os.path.exists(LEDGER):
        for ln in open(LEDGER):
            try:
                item = json.loads(ln)
            except Exception:
                continue
            if item.get("event") == "provider_downgrade":
                n_downgrades += 1
                downgrades.append(item)
                continue
            if item.get("cached"):
                n_cached += 1
                continue
            n += 1
            c = item.get("cost", 0.0)
            total += c
            u = item.get("usage", {})
            tokens_in += u.get("prompt_tokens", 0)
            tokens_out += u.get("completion_tokens", 0)
            m = item.get("model", "?")
            prov = item.get("provider")
            if not prov:
                # historical entries pre-dating the provider field: infer from
                # the model id (the openrouter model is fully-qualified).
                prov = "openrouter" if "/" in m else ("?")
            by_model[m] = by_model.get(m, [0, 0.0])
            by_model[m][0] += 1
            by_model[m][1] += c
            by_provider[prov] = by_provider.get(prov, [0, 0.0])
            by_provider[prov][0] += 1
            by_provider[prov][1] += c
            tag = item.get("tag", "")
            prefix = tag.split("-")[0] if tag else "?"
            by_tag_prefix[prefix] = by_tag_prefix.get(prefix, [0, 0.0])
            by_tag_prefix[prefix][0] += 1
            by_tag_prefix[prefix][1] += c

    lines = []
    lines.append("# Cost Ledger Summary\n")
    lines.append(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
    lines.append(f"- Paid LLM calls (non-cached): **{n}**")
    lines.append(f"- Provider downgrades logged (opencode-go -> openrouter): **{n_downgrades}**")
    lines.append("- LLM calls served from cache: not billed and not written to the "
                 "ledger (only paid calls are logged), so this ledger only counts "
                 "fresh calls; reruns of identical prompts are free.")
    lines.append(f"- Total cost: **${total:.4f}**  (≈ ${total:,.2f})")
    lines.append(f"- Prompt tokens (paid): {tokens_in:,}")
    lines.append(f"- Completion tokens (paid): {tokens_out:,}")
    lines.append("\n## By provider\n")
    lines.append("| provider | calls | cost |")
    lines.append("|---|---|---|")
    for prov, (cnt, c) in sorted(by_provider.items(), key=lambda x: -x[1][1]):
        lines.append(f"| {prov} | {cnt} | ${c:.4f} |")
    lines.append("\n## By model\n")
    lines.append("| model | calls | cost |")
    lines.append("|---|---|---|")
    for m, (cnt, c) in sorted(by_model.items(), key=lambda x: -x[1][1]):
        lines.append(f"| {m} | {cnt} | ${c:.4f} |")
    lines.append("\n## By stage (tag prefix)\n")
    lines.append("| stage | calls | cost |")
    lines.append("|---|---|---|")
    for t, (cnt, c) in sorted(by_tag_prefix.items(), key=lambda x: -x[1][1]):
        lines.append(f"| {t} | {cnt} | ${c:.4f} |")
    if downgrades:
        lines.append("\n## Provider downgrade events\n")
        for d in downgrades:
            lines.append("- " + json.dumps({
                "t": d.get("t"), "from": d.get("from_provider"),
                "to": d.get("to_provider"), "reason": d.get("reason"),
                "tag": d.get("tag")}, ensure_ascii=False))
    lines.append("\n## Notes\n")
    lines.append("- Default provider: `opencode-go` (cost $0 per API report); "
                 "downgrade to `openrouter` only on opencode-go unavailability/rate "
                 "limits/instability, each downgrade logged above with a reason.")
    lines.append("- Rule mining, rubric generation, and judge stages are cached "
                 "under `harness/llmcache/`; re-running identical prompts adds "
                 "no cost.")
    lines.append("- Deterministic stages (GitHub collection, split, scoring) "
                 "incur no LLM cost.")
    out = os.path.join(REPORTS, "cost_ledger_summary.md")
    with open(out, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print("Wrote", out)
    print(f"calls={n} cost=${total:.4f} downgrades={n_downgrades}")


if __name__ == "__main__":
    raise SystemExit(main())
