# Nexus-Editor AGENTS.md - Evaluation Report Card

Generated: 2026-08-07 05:15:38 UTC

## Provenance
- Judge model: `gpt-5.6-luna` via `opencode-go` (reasoning=`max`; fixed across all variants and dev/test; fallback_enabled=`False`; per AGENTS.md 4.2a/4.2b the same model+provider measures consistency C).
- AGENTS (combined `ours` files) sha256: `b77be1ffa2c7de1413d1b4ea4120790e9e7bf2dd7dd87a27eb13db33dbff98e2`
- Split sha256: `4ea70cd4b9d9ee435b7d9855ceba12f9aa1bb64f023f7dacd716854fea79fcd5` | policy: `time-ascending by created_at (authoritative _pr_list.json); 60/20/20; universe = A (finalized PRs with an evidence-gated final actor, Round5 rule) UNION B (open PRs with an admin decision formal review where the REVIEW ACTOR's own author_association is OWNER/MEMBER/COLLABORATOR OR the actor carries direct-push weak evidence; other commenters' associations never substitute for the review actor); open PRs without an admin decision review excluded; unrecoverable first-review snapshots excluded (1.2)`
- Test run note: test judged ONCE after AGENTS freeze. Round 12 / v7 data-audit universe = finalized PRs with an evidence-gated final actor UNION open PRs with an admin DECISION formal review whose REVIEW ACTOR's own author_association is OWNER/MEMBER/COLLABORATOR OR carries direct-push weak evidence (any other commenter's association never substitutes for the review actor; GT = earliest admin decision-review state, open PRs without such a review are outside the literal set); author-self-finalized PRs are INCLUDED only when the author is an evidenced maintainer and EXCLUDED otherwise; unrecoverable first-review snapshots excluded (1.2); every review's author_association/state/submitted_at/commit_id saved in dataset/evidence/reviews_full_v7.json
- C measurement run: 5x on 5 dev PRs (variant=ours).

## Self-consistency ceiling C
- C (full agreement across 5 runs on 5 PRs, variant=ours): **100.0%**
  > Threshold set <= C per AGENTS.md 3.5/3.6.

## Variant comparison (same judge model+provider, same harness, same set)

| variant | set | n | decision acc | reject recall | CI match | per-PR pass | coverage m-u | precision m-u |
|---|---|---|---|---|---|---|---|---|
| empty | dev | 15 | 73.3% | 78.6% | 100.0% | 6.7% | 31.8% | 37.3% |
| baseline | dev | 15 | 86.7% | 92.9% | 100.0% | 20.0% | 36.1% | 21.4% |
| ours | dev | 15 | 93.3% | 92.9% | 100.0% | 33.3% | 39.5% | 33.9% |
| empty | test | 17 | 82.3% | 100.0% | 100.0% | 23.5% | 36.6% | 45.6% |
| baseline | test | 17 | 82.3% | 100.0% | 100.0% | 41.2% | 50.1% | 43.8% |
| ours | test | 17 | 76.5% | 91.7% | 100.0% | 23.5% | 35.9% | 29.0% |

## Pass criteria check (formal test set; per AGENTS.md 3.6/3.7)

- MISS: Decision accuracy (ours, test) >= threshold=min(85%, C)=85.0%
- PASS: Reject-class recall (ours, test) >= 70%
- MISS: Ours beats empty baseline: delta >= 5pp OR paired bootstrap p < 0.05
- MISS: Ours beats CONTRIBUTING baseline: delta >= 5pp OR paired bootstrap p < 0.05
- delta ours-vs-empty acc = -5.9% (paired bootstrap p=1.0000)
- delta ours-vs-baseline acc = -5.9% (paired bootstrap p=1.0000)
- dev delta ours-vs-baseline acc = 6.7% (dev-driven iteration evidence)
- test set composition: 5 approve / 12 reject/close -> the approve side IS represented in this round's universe (5 approves incl. 4 open-with-admin-review PRs, so the baseline is no longer simply saturated).

  NOTE: rubric coverage mu / precision mu and CI replay fidelity (below) are REPORTED metrics, not additional acceptance thresholds in AGENTS.md 3.6.

## Report card metrics (formal test set)
- decision accuracy: 76.5%
- reject-class recall: 91.7%
- CI conclusion match (replay fidelity): 100.0%
- per-PR pass (decision ^ CI ^ coverage>=50%): 23.5%
- rubric coverage mean: 35.9% | precision mean: 29.0%

## Clause status (AGENTS.md 1.x / 2.x)
- **MISS**: 1.1 admin-handled universe (manage permission provable) — permission API returns 403 for the App token (also probed for ketd/redreamality/2391384896/aoe) - no actor's manage permission is PROVABLE. Round 6 universe is evidence-gated and extended to OPEN PRs: 77 of 110 finalized PRs included (final-actor evidence: weak as mechanically-filtered direct pushes = {'ketd': 95, '2391384896': 14, 'redreamality': 2, 'aoe': 1}), PLUS open PRs with an admin decision formal review (7: [15, 84, 87, 112, 116, 117, 119]; GT = earliest admin decision-review state). author-self-finalized PRs WITHOUT maintainer evidence excluded (33); weak evidence is reported as weak evidence, never proof of admin.
- **LIMITED**: 1.2 first-review snapshot (literal formal review, submitted_at + commit_id) — literal formal-review snapshots: 26 of 79 scored PRs (coverage 32.9%); 53 use an explicit 'first_activity_proxy' snapshot that is NOT disguised as a literal first review; 5 PRs ([23, 65, 83, 97, 137]) excluded as unrecoverable. Per-split counts in split.json.
- **PASS**: 1.3 ground-truth isolation (judge reads only first.json + AGENTS) — score.py is the only consumer of dataset/ground_truth; judge.py reads dataset/snapshots/<n>/first.json + agents only. leak_check.json reports no ground-truth fields in first.json.
- **PASS**: 1.4 time split train/60 dev/20 test/20 (created_at) — time-ascending split by authoritative created_at; boundaries asserted by split.py (train/dev/test = 47/15/17).
- **PASS**: 2.0 layered AGENTS.md, core 100-300 lines — agents/AGENTS.md core is within 100-300 lines; sub-modules loaded on demand via the index table.
- **PASS**: 2.2 constraint sources incl. direct-push weak evidence, strict '>' priority — priority chain is strict '>': repo hard rules > test/dev results > PR rejection reasons > implicit norms > direct-push weak evidence > third-party supplements; direct-push commits are mechanically filtered before use as weak evidence.
- **PASS**: 2.5 scope: P90 file/lines limits + no refactor-feature mixing — train-merged-PR P90 values (files, +/- lines) recorded in agents/03-scope.md; mixing refactor with new features in one PR is rejected.
- **PASS**: 3.2 CI replay (no full re-run) — CI results are replayed from stored status/check-run data at the first-review head; no real re-run performed.
- **MISS**: 3.6 decision accuracy >=85% on test — ours(test) acc=76.5%
- **PASS**: 3.6 reject-class recall >=70% on test — ours(test) reject recall=91.7%
- **REPORT**: 3.6 rubric coverage mu / precision mu (report-only) — ours(test) coverage mu=35.9% precision mu=29.0%
- **MISS**: 3.7 ours significantly better than empty baseline — delta=-5.9% p=1.0000
- **MISS**: 3.7 ours significantly better than CONTRIBUTING baseline — delta=-5.9% p=1.0000

## Documented limitations
- **First-review vs final-merge label tension**: ground truth labels are the FINAL outcome (`merged => approve`, `closed => reject`), while the judge evaluates the snapshot at the first-review/first-activity point. PRs that were failing at that point but later fixed and merged would be labelled 'approve' yet cannot be approved at the snapshot state. No labels/scorer were changed.
- **First-review snapshot is a proxy for review-less PRs**: only PRs with a formal review have a literal first-review snapshot; the rest use an explicit 'first_activity_proxy' timestamp (earliest issue/review-comment, else created_at) with a faithful recovered head SHA. Counts per split in `split.json` (literal_first_review_counts / first_review_basis_counts).
- **Admin permission unverifiable (MISS on the 1.1 permission dimension):** the contributing GitHub App token receives HTTP 403 from `/collaborators/*/permission` and `/collaborators`; no actor is provably OWNER/MEMBER. Per-actor evidence tiers are used instead: weak maintainer evidence (surviving mechanically-filtered direct pushes on main; actors ketd/2391384896/redreamality), the final actor's OWN COLLABORATOR+ association, or a distinct non-author close/merge actor (permission unverified). Authors closing/merging their own PR are included ONLY when they are evidence-gated maintainers, and are otherwise excluded (33 PRs; see `split.json.excluded_self_finalized`). See `dataset/evidence/admin_audit.json` + `protocol.json`.
- **Approve-class still relatively thin**: the universe has 12 approve-flagged PRs (train 5, dev 1, test 5), of which 7 are OPEN PRs admitted via an admin decision formal review ([15, 84, 87, 112, 116, 117, 119]); ground truth for those is the admin review state, not a merge. Test has 5 approves (112/116/117/119 open-admin-approve + 135 merged). Approve-side behaviour is now measurable but still a minority of the set.
- **3.7 vs BASELINES MISS on the formal test set**: ours(test) acc = 76.5% vs CONTRIBUTING baseline 82.3% (delta -5.9%, paired bootstrap p=1.0000) and vs empty 82.3% (delta -5.9%, p=1.0000); both comparisons are MISSING the >=5pp / p<0.05 bar. Honestly reported: this is a judgement gap on this run's test set, not a 100%-saturation artifact, and no label/scorer/default-reject was changed to force a result. (Dev-set iteration evidence remains positive: 6.7% on dev, see below.)
- **Open PRs without an admin decision formal review are outside the literal set** (listed in split.json `open_excluded`); open PRs WITH an admin decision formal review ARE included, with the earliest admin decision-review state as ground truth.
- **Judge consistency / provider binding (this round)**: judge = `gpt-5.6-luna` via `opencode-go` with reasoning=`max` with the cross-provider FALLBACK DISABLED (`JUDGE_NO_FALLBACK=1`) for the whole round (dev, test, and the C sample all share the same model+provider, per AGENTS.md 4.2a). No opencode-go -> openrouter downgrade was triggered during the formal test (recorded in reports/cost_ledger.jsonl). C was measured as 100.0% over 5 runs x 5 dev PRs with this same judge. Judge-model selection history: deepseek-v4-flash measured C=0.80 (< 85%) earlier, which permitted the strong-model upgrade (AGENTS.md 3.5/4.2c); `gpt-5.6-luna` measures C=1.00, so the final acceptance threshold was set to min(85%, 100%) = 85%.

## Cost ledger summary
- paid LLM calls: 2204; total cost: **$1.0150**
- provider downgrades logged: 14
  - deepseek-v4-flash: 393 calls
  - deepseek-v4-pro: 101 calls
  - deepseek/deepseek-v4-flash-0731: 983 calls
  - gpt-5.6-luna: 727 calls
  - provider opencode-go: 1221 calls
  - provider openrouter: 983 calls

## Files
- Split: `dataset/split.json` (time split, boundaries, first-review basis, admin filter, per-PR evidence)
- Protocol/audit: `dataset/evidence/protocol.json` | Admin audit: `dataset/evidence/admin_audit.json` | Head-SHA: `dataset/evidence/head_sha_report.json`
- Rubric: `rubric/<pr>.json` | Judge output: `harness/state/judge_*/` (tag-bound)
- Freeze: `dataset/AGENTS_freeze.json` (binds AGENTS + split + judge model + provider + reasoning + fallback) | Cost: `reports/cost_ledger.jsonl`
- Bootstrap: `reports/bootstrap_<split>_<tag>.json` (split/tag-bound so a dev bootstrap can never clobber the formal test bootstrap; deterministic, recomputable from per-PR score rows)
