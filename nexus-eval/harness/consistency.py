#!/usr/bin/env python3
"""Measure judge self-consistency C (AGENTS.md 3.5).

Runs the judge across the SAME PR multiple times (default 5) with the same
AGENTS.md variant and counts how often the decision is identical. C is the
upper bound for achievable accuracy with this judge model.

Usage: consistency.py [variant] [n_sample] [n_runs]
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm_client import complete  # noqa: E402
from judge import (JUDGE_SYSTEM, build_user_prompt, variant_text, parse,
                   judge_provider_name, judge_model_name,
                   judge_reasoning_kwargs, judge_reasoning_name,
                   judge_fallback_enabled)  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
SNAP = os.path.join(ROOT, "dataset", "snapshots")
SPLIT = os.path.join(ROOT, "dataset", "split.json")
STATE = os.path.join(HERE, "state")


def run(variant="ours", n_sample=8, n_runs=5, split="dev", tag="untagged"):
    split_data = json.load(open(SPLIT))
    sample = [p for p in split_data["prs"] if p["split"] == split]
    sample.sort(key=lambda p: p["number"])
    sample = sample[:n_sample]
    if not sample:
        print(f"no {split} PRs yet")
        return 0
    agents_text = variant_text(variant)
    import hashlib
    import llm_client
    agents_sha = hashlib.sha256(agents_text.encode("utf-8")).hexdigest()
    harness_sha = hashlib.sha256(
        open(os.path.join(HERE, "judge.py"), "rb").read()).hexdigest()
    judge_provider = judge_provider_name()
    judge_model = judge_model_name(judge_provider)
    out_dir = os.path.join(STATE, f"consistency_{variant.replace('/', '_')}_{split}__{tag}")
    os.makedirs(out_dir, exist_ok=True)

    decisions = {}
    for pr in sample:
        n = pr["number"]
        snap = json.load(open(os.path.join(SNAP, str(n), "first.json")))
        key = str(n)
        decisions[key] = []
        for run_i in range(n_runs):
            out_path = os.path.join(out_dir, f"{n}_r{run_i}.json")
            if os.path.exists(out_path):
                obj = json.load(open(out_path))
            else:
                # same judge config as judge.py (default temperature; reasoning
                # controlled by JUDGE_REASONING, must stay fixed across the round);
                # force fresh call (cache=False) so each run varies.
                kw = {"max_tokens": 8000,
                      "tag": f"consistency-{variant}-{n}-{run_i}", "cache": False,
                      "model": judge_model, "provider": judge_provider,
                      "fallback": judge_fallback_enabled()}
                kw.update(judge_reasoning_kwargs())
                text, _ = complete(
                    [{"role": "system", "content": JUDGE_SYSTEM},
                     {"role": "user", "content": build_user_prompt(snap, agents_text)}],
                    **kw)
                obj = parse(text, text)
                json.dump(obj, open(out_path, "w"), indent=2, ensure_ascii=False)
            decisions[key].append(obj.get("decision"))
        print(f"PR {n}: decisions={decisions[key]}")

    # overall consistency: % of PRs where all runs agree on the majority decision
    agree = 0
    for n, ds in decisions.items():
        from collections import Counter
        c = Counter(ds)
        top = c.most_common(1)[0][1]
        if top == len(ds):
            agree += 1
    C = agree / len(decisions) if decisions else 0.0
    print(f"C (self-consistency, full agreement) = {C:.2f} over {len(decisions)} PRs x {n_runs} runs")
    with open(os.path.join(STATE, f"consistency_result_{tag}.json"), "w") as fh:
        json.dump({"C": C, "n_pr": len(decisions), "n_runs": n_runs,
                   "variant": variant, "split": split, "tag": tag,
                   "judge_model": judge_model, "judge_provider": judge_provider,
                   "judge_reasoning": judge_reasoning_name(),
                   "fallback_enabled": judge_fallback_enabled(),
                   "agents_sha": agents_sha, "harness_sha": harness_sha,
                   "decisions": decisions},
              fh, indent=2, ensure_ascii=False)
    # also keep the canonical path (last run) for backward tooling
    with open(os.path.join(STATE, "consistency_result.json"), "w") as fh:
        json.dump({"C": C, "n_pr": len(decisions), "n_runs": n_runs,
            "variant": variant, "split": split, "tag": tag,
            "judge_model": judge_model, "judge_provider": judge_provider,
            "judge_reasoning": judge_reasoning_name(),
            "fallback_enabled": judge_fallback_enabled(),
            "agents_sha": agents_sha, "harness_sha": harness_sha,
            "decisions": decisions},
                  fh, indent=2, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    a = sys.argv[1:] or ["ours", "8", "5", "dev"]
    split = a[3] if len(a) > 3 else "dev"
    tag = a[4] if len(a) > 4 else "untagged"
    sys.exit(run(a[0], int(a[1]) if len(a) > 1 else 8,
                 int(a[2]) if len(a) > 2 else 5, split=split, tag=tag))
