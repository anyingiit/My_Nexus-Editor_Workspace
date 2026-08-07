# Cost Ledger Summary

Generated: 2026-08-07 05:16:11 UTC

- Paid LLM calls (non-cached): **2204**
- Provider downgrades logged (opencode-go -> openrouter): **14**
- LLM calls served from cache: not billed and not written to the ledger (only paid calls are logged), so this ledger only counts fresh calls; reruns of identical prompts are free.
- Total cost: **$1.0150**  (≈ $1.01)
- Prompt tokens (paid): 27,747,070
- Completion tokens (paid): 3,416,381

## By provider

| provider | calls | cost |
|---|---|---|
| openrouter | 983 | $1.0150 |
| opencode-go | 1221 | $0.0000 |

## By model

| model | calls | cost |
|---|---|---|
| deepseek/deepseek-v4-flash-0731 | 983 | $1.0150 |
| deepseek-v4-flash | 393 | $0.0000 |
| deepseek-v4-pro | 101 | $0.0000 |
| gpt-5.6-luna | 727 | $0.0000 |

## By stage (tag prefix)

| stage | calls | cost |
|---|---|---|
| judge | 1490 | $0.7246 |
| consistency | 526 | $0.2609 |
| mine | 32 | $0.0095 |
| rubric | 61 | $0.0092 |
| nor | 8 | $0.0054 |
| match | 51 | $0.0044 |
| stab | 11 | $0.0006 |
| test | 1 | $0.0002 |
| j115 | 1 | $0.0001 |
| t | 1 | $0.0000 |
| mtest | 1 | $0.0000 |
| connectivity | 7 | $0.0000 |
| smoke | 1 | $0.0000 |
| small | 1 | $0.0000 |
| probe73 | 1 | $0.0000 |
| upgrade | 2 | $0.0000 |
| probe | 9 | $0.0000 |

## Provider downgrade events

- {"t": 1785982822.8217616, "from": "opencode-go", "to": "openrouter", "reason": "opencode-go failed: LLM returned empty content after retries: LLM HTTP 500: {\"type\":\"error\",\"error\":{\"type\":\"error\",\"message\":\"Internal server error\"}}", "tag": "judge-ours-73"}
- {"t": 1785985537.359682, "from": "opencode-go", "to": "openrouter", "reason": "opencode-go failed: LLM returned empty content after retries: None", "tag": "judge-empty-72"}
- {"t": 1785990143.923262, "from": "opencode-go", "to": "openrouter", "reason": "opencode-go failed: LLM returned empty content after retries: None", "tag": "judge-empty-73"}
- {"t": 1785993882.25755, "from": "opencode-go", "to": "openrouter", "reason": "opencode-go failed: LLM returned empty content after retries: None", "tag": "judge-ours-73"}
- {"t": 1785995280.9116285, "from": "opencode-go", "to": "openrouter", "reason": "opencode-go failed: LLM returned empty content after retries: LLM HTTP 500: {\"type\":\"error\",\"error\":{\"type\":\"error\",\"message\":\"Internal server error\"}}", "tag": "judge-ours-99"}
- {"t": 1785996769.1052618, "from": "opencode-go", "to": "openrouter", "reason": "opencode-go failed: LLM returned empty content after retries: LLM HTTP 503: {\"error\":{\"type\":\"server_error\",\"message\":\"Error from provider (Console Go): Upstream request failed: Endpoint is unavailable.\"}}", "tag": "judge-ours-116"}
- {"t": 1785997157.929157, "from": "opencode-go", "to": "openrouter", "reason": "opencode-go failed: LLM returned empty content after retries: LLM HTTP 503: ", "tag": "judge-ours-119"}
- {"t": 1786000519.63684, "from": "opencode-go", "to": "openrouter", "reason": "opencode-go failed: LLM returned empty content after retries: LLM HTTP 503: {\"error\":{\"type\":\"server_error\",\"message\":\"Error from provider (Console Go): Upstream request failed: Endpoint is unavailable.\"}}", "tag": "judge-empty-109"}
- {"t": 1786000877.5767565, "from": "opencode-go", "to": "openrouter", "reason": "opencode-go failed: LLM returned empty content after retries: LLM HTTP 500: {\"type\":\"error\",\"error\":{\"type\":\"error\",\"message\":\"Internal server error\"}}", "tag": "judge-empty-112"}
- {"t": 1786001502.3062303, "from": "opencode-go", "to": "openrouter", "reason": "opencode-go failed: LLM returned empty content after retries: LLM HTTP 500: {\"type\":\"error\",\"error\":{\"type\":\"error\",\"message\":\"Internal server error\"}}", "tag": "judge-empty-116"}
- {"t": 1786004890.37535, "from": "opencode-go", "to": "openrouter", "reason": "opencode-go failed: LLM returned empty content after retries: LLM HTTP 503: ", "tag": "consistency-ours-73-2"}
- {"t": 1786007015.4965408, "from": "opencode-go", "to": "openrouter", "reason": "opencode-go failed: LLM returned empty content after retries: LLM HTTP 500: {\"type\":\"error\",\"error\":{\"type\":\"error\",\"message\":\"Internal server error\"}}", "tag": "judge-ours-116"}
- {"t": 1786008412.4931188, "from": "opencode-go", "to": "openrouter", "reason": "opencode-go failed: LLM returned empty content after retries: None", "tag": "judge-empty-116"}
- {"t": 1786015985.2969866, "from": "opencode-go", "to": "openrouter", "reason": "Round 6R7 flash dev comparison: opencode-go returned repeated HTTP 500/503/empty-content responses on long first-review prompts; a strict-provider retry exceeded one hour, so the entire comparison is pinned to openrouter rather than mixing providers.", "tag": "round6r7-flash-dev"}

## Notes

- Default provider: `opencode-go` (cost $0 per API report); downgrade to `openrouter` only on opencode-go unavailability/rate limits/instability, each downgrade logged above with a reason.
- Rule mining, rubric generation, and judge stages are cached under `harness/llmcache/`; re-running identical prompts adds no cost.
- Deterministic stages (GitHub collection, split, scoring) incur no LLM cost.
