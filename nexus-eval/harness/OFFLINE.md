# nexus-eval — offline / fixture modes

This harness defaults to **real data + real LLM** (see `README.md`). Because
credentials may be absent in other environments, every stage also has a
deterministic **offline mode** that needs no GitHub API and no LLM calls.

## Offline data path (no GitHub)

The collected dataset lives in `dataset/` (JSON snapshot files). They are
already committed/frozen, so downstream stages can run without GitHub access.
If you must re-collect from scratch and GitHub is unavailable, you cannot
recreate raw data — use the frozen snapshot copies.

```bash
# Re-run split + isolation from frozen snapshots (no network)
python3 harness/split.py
```

## Offline scoring path (no LLM)

1. Build deterministic "perfect judge" fixtures from the frozen snapshots +
   ground truth + rubric:
   ```bash
   python3 harness/make_fixtures.py
   ```
2. Score the fixture run `state/judge_fixture` against ground truth:
   ```bash
   python3 harness/score.py harness/state/judge_fixture --split dev --json /tmp/score_dev.json
   python3 harness/score.py harness/state/judge_fixture --split test --json /tmp/score_test.json
   ```
   `make_fixtures.py` sets decision = merged?approve:reject and reasons from the
   rubric, so a perfect judge gets 100% decision accuracy and ~full coverage.
   This proves the scorer and report card work end to end with zero cost.
   Score JSON embeds deterministic per-PR `rows`, so `harness/bootstrap.py` can
   compute paired bootstrap p-values from the scored files alone (no LLM).

## Offline LLM mode

`llm_client.py` caches every response under `harness/llmcache/` keyed by the
full request. If a cache exists, no API call is made. To re-run the exact same
judge/rubric/rules prompts with no further spend:
- keep `harness/llmcache/` intact;
- re-run the same driver commands.

## Environment notes

- GitHub token: read from `scripts/myanyagent-credential-helper.cjs` (a
  repository-local GitHub App helper). If absent, set `GITHUB_TOKEN` env var.
- LLM keys: read from `~/.local/share/opencode/auth.json` under
  `opencode-go.key` and `openrouter.key`, or via env `OPENCODE_API_KEY` /
  `OPENROUTER_API_KEY`.
- Default provider: `opencode-go` (model `deepseek-v4-flash`); set
  `LLM_PROVIDER=openrouter` to use `deepseek/deepseek-v4-flash-0731`
  (matches the "flash" default family in root AGENTS.md). opencode-go requires
  a browser-like User-Agent (the client sets one; Cloudflare rejects the bare
  urllib UA).
- `admin_audit.py` and `mine_direct_push.py` are offline-safe: the permission
  probe is wrapped so the audit completes without network, and the direct-push
  miner degrades to a recorded empty result if the GitHub API is unavailable.
