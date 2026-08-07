#!/usr/bin/env python3
"""Deterministic paired bootstrap for variant comparisons (AGENTS.md 3.7).

Reads the per-PR score files (score_<variant>_<split>_<tag>.json, which embed
deterministic per-PR rows) and recomputes, from those rows alone:

  - observed delta in decision accuracy between variants A and B on the SAME PRs
  - one-sided bootstrap p-value: share of resamples where delta <= 0 (i.e. the
    probability that the observed advantage of B over A is due to chance).

A fixed seed and a fixed number of resamples make the result reproducible and
independently verifiable from the per-PR score files.

Usage:
  bootstrap.py --split dev --tag <tag> [--n 10000] [--seed 1234]
Writes reports/bootstrap.json (with per-comparison details + the source rows).
"""
import argparse
import json
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
REPORTS = os.path.join(ROOT, "reports")
VARIANTS = ["empty", "baseline", "ours"]


def load_score(split, variant, tag):
    p = os.path.join(REPORTS, f"score_{variant}_{split}_{tag}.json")
    with open(p) as fh:
        return json.load(fh)


def row_map(data):
    return {r["pr"]: r for r in data.get("rows", [])}


def paired_bootstrap(rows_a, rows_b, n_iter, rng):
    """rows_b is the 'treatment' variant, rows_a the reference.

    Returns (delta_acc, p_value). Bootstrap over PRs (resample PR indices with
    replacement); delta = mean(b_ok) - mean(a_ok)."""
    keys = sorted(set(rows_a) & set(rows_b))
    if not keys:
        return None
    a = [rows_a[k]["decision_ok"] for k in keys]
    b = [rows_b[k]["decision_ok"] for k in keys]
    m = len(keys)
    obs = sum(b) / m - sum(a) / m
    count_le0 = 0
    for _ in range(n_iter):
        idx = [rng.randrange(m) for _ in range(m)]
        ba = [a[i] for i in idx]
        bb = [b[i] for i in idx]
        if sum(bb) / m - sum(ba) / m <= 0:
            count_le0 += 1
    p = count_le0 / n_iter
    return {"n": m, "delta_acc": round(obs, 4), "p_value": round(p, 4)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="test")
    ap.add_argument("--tag", default="final")
    ap.add_argument("--n", type=int, default=10000)
    ap.add_argument("--seed", type=int, default=1234)
    args = ap.parse_args()

    data = {v: load_score(args.split, v, args.tag) for v in VARIANTS}
    rm = {v: row_map(data[v]) for v in VARIANTS}
    rng = random.Random(args.seed)

    res = {
        "split": args.split,
        "tag": args.tag,
        "n_iter": args.n,
        "seed": args.seed,
    }
    # ours (treatment) vs baseline (reference) and ours vs empty (reference)
    res["ours_vs_baseline"] = paired_bootstrap(rm["baseline"], rm["ours"], args.n, rng)
    res["ours_vs_empty"] = paired_bootstrap(rm["empty"], rm["ours"], args.n, rng)

    # observed raw (delta from summary means) for cross-checking
    res["observed"] = {
        "acc": {v: data[v].get("decision_accuracy") for v in VARIANTS},
    }

    os.makedirs(REPORTS, exist_ok=True)
    # The bootstrap result is BOUND to (split, tag): writing to a split/tag
    # specific file prevents a later dev bootstrap from clobbering the formal
    # test bootstrap (a previous consistency bug). The caller/report reads the
    # split-tagged file that belongs to the split it is reporting.
    out_name = f"bootstrap_{args.split}_{args.tag}.json"
    with open(os.path.join(REPORTS, out_name), "w") as fh:
        json.dump(res, fh, indent=2, ensure_ascii=False)
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
