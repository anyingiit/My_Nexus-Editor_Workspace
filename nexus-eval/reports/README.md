# reports/ — authoritative vs historical

## Authoritative (Round 9 max-judge run, tag=`r9_max_luna_final`)

- `report_card.md` / `report_card.json` — **the** evaluation report card
  (decision accuracy, reject-class recall, CI fidelity, per-PR pass, coverage
  μ / precision μ, self-consistency C, baseline deltas + paired bootstrap
  p-values, provenance, documented limitations).
- `score_{variant}_test_r9_max_luna_final.json` and
  `score_{variant}_dev_r9_max_luna_dev.json` — per-variant/per-split metrics WITH
  deterministic per-PR `rows` (used to recompute bootstrap).
- `bootstrap_test_r9_max_luna_final.json` and
  `bootstrap_dev_r9_max_luna_dev.json` — deterministic
  paired bootstrap (seed 1234, 10k iters) from the score rows.
- `AGENTS_freeze.json` (in `../dataset/`) — freeze hashes and excluded
  unrecoverable list.
- `cost_ledger.jsonl` + `cost_ledger_summary.md` — cost accounting, including
  the max-judge upgrade.
- `round9_upgrade.json` — machine-readable before/after model, metrics, freeze,
  and cost record.

## Historical / provisional (VOID as evidence)

The following files are from earlier, **provisional rounds** whose dataset was
not yet audit-repaired (missing first-review SHA recovery, admin-permission
assertions that the API cannot prove, and a non-deterministic judge-state
layout). They are kept for traceability only and must NOT be quoted as results:

- `collect.log`, `collect2.log`, `mine.log`, `rubric.log`
- `run_all.log`, `run_matrix.log`, `run_dev.log`, `run_test.log` (old-style,
  non-tag-bound, from rounds 1–2)
- `judge_dev.log`, `judge_ours_dev.log`, `judge_ours_dev_v2.log`,
  `consistency.log`, `consistency_dev.log`
- `score_*_dev_final.json` / `score_*_test_final.json` are CURRENT (overwritten
  by the final run); older un-suffixed score files (e.g. without `final`) are
  historical.

See `../PROGRESS.md` for the Round 9 max-judge outcome. Round 8 test artifacts
are retained as provisional upgrade evidence; the Round 9 test tag is the only
formal test run after the max-judge freeze.
