#!/usr/bin/env python3
"""Judge driver: runs the judge LLM over a set of PRs.

For each PR, judge receives:
  - the first-review snapshot (commits, files changed, CI/checks replayed from
    stored data, title, description) as the ONLY allowed input state, and
  - an AGENTS.md variant (the "test standard").
Judge outputs structured decisions; scorer compares them to ground truth.

Variant resolution:
  "empty"          -> empty AGENTS.md text
  "baseline"       -> repo's original CONTRIBUTING.md + GOVERNANCE.md + ci.yml
  "ours"           -> generated agents/AGENTS.md (+ sub-modules)
  "<file>:<mtime>" -> raw file path override (dev iteration)
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm_client import complete  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
SNAP = os.path.join(ROOT, "dataset", "snapshots")
AGENTS_DIR = os.path.join(ROOT, "agents")
STATE = os.path.join(HERE, "state")
CACHE = os.path.join(HERE, "llmcache")

REPO_RULES = os.path.join(ROOT, "..", "Nexus-Editor")


JUDGE_SYSTEM = (
    "You are a senior maintainer reviewing a pull request for the Nexus-Editor "
    "Markdown editor. You see the PR as it stood at the FIRST review time: its "
    "title, description, the commits that existed then, the files changed, and "
    "the CI/check results observed for that state. CI was already run and its "
    "results are replayed below. You MUST make an approval decision and give "
    "review comments.\n\n"
    "Return ONLY a JSON object:\n"
    "{\n"
    "  \"ci_result\": \"pass\" | \"fail\" | \"unknown\",\n"
    "  \"decision\": \"approve\" | \"reject\",\n"
    "  \"reasons\": [\"<one concrete review comment each>\"]\n"
    "}\n"
    "CI convention (be faithful to the replayed data):\n"
    "  - \"fail\"    when ANY recorded check/status has a definitive failure "
    "(conclusion failure/cancelled/timed_out/action_required/error; state failure/error).\n"
    "  - \"pass\"    when at least one check/status is success AND nothing is failing.\n"
    "  - \"unknown\" when nothing is conclusive yet (all pending/queued/skipped/absent, "
    "e.g. an unsigned-CLA license/cla status that merely shows 'pending').\n"
    "Use 'approve' when the PR should be merged as-is (possibly with minor "
    "non-blocking nits). Use 'reject' when it needs changes or should not be "
    "merged. Reasons must be concrete, PR-specific, and grounded in what you "
    "see; empty array is allowed only if the PR is flawless. Before deciding, "
    "inspect the actual patch hunks and apply the supplied contribution rules; "
    "do not infer the final outcome or hidden reviewer comments."
)


def render_checks(snap):
    lines = []
    sth = snap.get("status_first")
    cr = snap.get("check_runs_first")
    if sth:
        lines.append(f"Combined status: {sth.get('state')} "
                     f"({sth.get('total_count')} statuses)")
        for s in sth.get("statuses") or []:
            lines.append(f"- status {s.get('context')}: {s.get('state')} "
                         f"- {s.get('description') or ''}")
    if cr:
        lines.append(f"Check runs: {cr.get('total_count')}")
        for c in cr.get("check_runs") or []:
            lines.append(f"- check {c.get('name')}: "
                         f"status={c.get('status')} conclusion={c.get('conclusion')}")
    if not lines:
        lines.append("(no check results recorded for this state)")
    return "\n".join(lines)


# Prompt budget policy (deterministic, derived from the train/dev full-universe
# distribution and audited every round - see reports/judge_input_audit_*.json):
#   - FLOOR: every changed file is GUARANTEED its first hunks up to this many
#     chars, so on a large PR no file's patch can be silently starved by the
#     earlier files (previously the total was filled "earliest files first",
#     which dropped dozens of file patches on the biggest PRs).
#   - PER_MAX: a single file's patch is capped at this many chars; anything
#     beyond is explicitly marked "[N hunks omitted]" (never silently cut).
#   - TOTAL: whole-patch budget. After granting every file its floor, leftover
#     budget is distributed in deterministic file order up to PER_MAX each, so
#     p90 total-patch PRs render near-fully while pathological 500k-char diffs
#     stay bounded and still show the head of every file.
# All three variants (empty / baseline / ours) share this exact evidence
# rendering, so the comparison stays fair (AGENTS.md 3.7).
PER_FILE_PATCH_FLOOR = 2000
PER_FILE_PATCH_MAX = 12000
TOTAL_PATCH_CHARS = 120000
MAX_AGENTS_CHARS = 90000


def _split_patch_hunks(patch):
    """Deterministically split a git patch text into (diff_header, hunks).

    The diff header (``diff --git``/``---``/``+++`` lines before the first
    ``@@``) is returned separately so it is always kept even when hunks are
    truncated. Each hunk string includes its own ``@@ ...`` header line.
    """
    lines = patch.split("\n")
    hdr = []
    hunks = []
    cur = None
    for ln in lines:
        if ln.startswith("@@"):
            if cur is not None:
                hunks.append("\n".join(cur))
                cur = []
            cur = [ln]
        elif cur is not None:
            cur.append(ln)
        else:
            hdr.append(ln)
    if cur is not None:
        hunks.append("\n".join(cur))
    return "\n".join(hdr), hunks


def _slice_patch_by_hunks(patch, max_chars):
    """Return (shown_text, n_omitted_hunks).

    Keeps the diff header plus as many LEADING hunks as fit within max_chars.
    When the next hunk only partially fits, its head is shown with an explicit
    "[hunk truncated]" marker instead of being dropped (so a single huge added
    file still shows its beginning). Anything beyond is counted (returned, not
    silently dropped) and the caller appends an explicit "[N hunks omitted]"
    marker. Whole content is returned untouched when it already fits.
    """
    if len(patch) <= max_chars:
        return patch, 0
    hdr, hunks = _split_patch_hunks(patch)
    budget = max_chars - len(hdr) - (1 if hdr else 0)
    kept = []
    i = 0
    while i < len(hunks) and budget > 0:
        block = "\n" + hunks[i]
        if len(block) <= budget:
            kept.append(block)
            budget -= len(block)
            i += 1
        else:
            kept.append(block[:budget] + "\n... [hunk truncated]")
            budget = 0
            i += 1
            break
    omitted = len(hunks) - i
    text = hdr + "".join(kept)
    if omitted > 0:
        text += f"\n... [{omitted} hunks omitted]"
    return text, omitted


def render_files(snap):
    """Files changed at first review, INCLUDING diff patches when stored.

    Deterministic compact rendering with a per-file fairness guarantee:
      1. every file's header (name + add/del counts) is always shown;
      2. every changed file is guaranteed its FIRST hunks (up to FLOOR chars);
      3. leftover budget is grown per file (in file order) up to PER_MAX;
      4. any truncation is marked explicitly ("[N hunks omitted]" or
         "[patch omitted, budget exhausted]"), never silent.
    """
    cmp = snap.get("compare_first_review")
    if cmp and cmp.get("files") is not None:
        files = cmp["files"]
    else:
        files = snap.get("files_first") or []
    files = files or []

    # grant every file its floor; track (floor, cap=min(len, PER_MAX))
    alloc = []
    floor_sum = 0
    for f in files:
        plen = len(f.get("patch") or "")
        floor = min(plen, PER_FILE_PATCH_FLOOR) if plen else 0
        alloc.append([floor, min(plen, PER_FILE_PATCH_MAX)])
        floor_sum += floor
    # pathological safety: if even the floors exceed the total, scale them down
    # proportionally (still keeps every file visible).
    if floor_sum > TOTAL_PATCH_CHARS and files:
        scale = TOTAL_PATCH_CHARS / floor_sum
        for a in alloc:
            if a[0]:
                a[0] = max(1, int(a[0] * scale))
        floor_sum = sum(a[0] for a in alloc)
    # distribute leftover budget in file order
    remaining = TOTAL_PATCH_CHARS - floor_sum
    for a in alloc:
        grow = min(a[1] - a[0], remaining)
        if grow <= 0:
            continue
        a[0] += grow
        remaining -= grow

    lines = []
    for i, f in enumerate(files):
        fn = f.get("filename") or "?"
        head = f"- {fn} (+{f.get('additions')}/-{f.get('deletions')})"
        patch = f.get("patch") or ""
        if not patch:
            lines.append(head)
            continue
        alloc_chars = alloc[i][0]
        if alloc_chars <= 0:
            lines.append(f"{head}: [patch omitted, budget exhausted]")
            continue
        text, _ = _slice_patch_by_hunks(patch, alloc_chars)
        lines.append(head)
        lines.append(text)
    if not lines:
        lines = ["(no file list available)"]
    return "\n".join(lines)


def render_commits(snap):
    commits = snap.get("commits_at_first_review") or []
    return "\n".join(f"- {c['sha'][:8]} {c['message'].splitlines()[0]}" for c in commits) \
        or "(no commits at first review)"


def build_user_prompt(snap, agents_text, max_agents=MAX_AGENTS_CHARS):
    return f"""# PR #{snap['pr_number']}: {snap['title']}

## Description
{snap['body'] or '(empty)'}

## Commits at first review
{render_commits(snap)}

## Files changed at first review
{render_files(snap)}

## CI / Checks (replayed)
{render_checks(snap)}

## Contribution rules to apply
--- START AGENTS.md ---
{agents_text[:max_agents]}
--- END AGENTS.md ---

    Decide: approve or reject this PR as it stood at first review. CI result =
the replayed conclusion.

## Required review method
1. Read the patch hunks and tests, not only filenames or line counts.
2. Apply the supplied rules in their stated priority order; distinguish a
   faithful CI label from build-gate evidence and from a merge blocker.
3. A rule that calls missing, pending, or first-review-failed CI a signal to
   weigh must not be converted into an automatic rejection by paraphrase.
4. Cite only concrete findings visible in this first-review input.

## Patch review checklist
Walk the patch mechanically so no visible defect is missed (this checklist adds
no default verdict; the rules block decides how much each item weighs):
- Scope: does every changed file serve the title/description? Note unrelated
  or bundled changes.
- Tests: were tests added/updated for the new or changed behaviour, and do they
  cover the affected path? Note when behaviour-shipping code has no tests.
- Repo-hard liabilities visible at this snapshot: secrets/credentials, build
  artifacts or generated output, unrelated lockfile/binary churn, a new public
  API without docs, or a breaking change without a migration note.
- Delivered content fidelity: does the change, its tests and any doc/changeset
  entries describe the same outcome without contradicting each other?"""


def judge_provider_name():
    """Return the provider bound to the current judge round."""
    import llm_client
    return os.environ.get("JUDGE_PROVIDER", os.environ.get(
        "LLM_PROVIDER", llm_client.DEFAULT_PROVIDER))


def judge_model_name(provider=None):
    """Return the explicit judge model, or the provider's default model."""
    import llm_client
    provider = provider or judge_provider_name()
    return os.environ.get("JUDGE_MODEL", llm_client.PROVIDERS[provider]["model"])


def judge_reasoning_name():
    """Return the reasoning variant bound to the current judge round.

    ``default`` preserves the historical no-extra-parameter behavior. Other
    values are provider model-catalog reasoning efforts, such as ``none`` or
    ``max``, and are passed through in the OpenAI-compatible request.
    """
    return os.environ.get("JUDGE_REASONING", "default")


def judge_reasoning_kwargs():
    """Build the fixed reasoning option for judge and consistency calls."""
    effort = judge_reasoning_name().strip().lower()
    if not effort or effort == "default":
        return {}
    return {"reasoning": {"effort": effort}}


def judge_fallback_enabled():
    """Evaluation rounds may opt out of cross-provider fallback."""
    return os.environ.get("JUDGE_NO_FALLBACK", "0") != "1"


def variant_text(variant):
    if variant == "empty":
        return ""
    if variant == "baseline":
        base = REPO_RULES
        parts = []
        for name in ("CONTRIBUTING.md", "GOVERNANCE.md"):
            p = os.path.join(base, name)
            if os.path.exists(p):
                parts.append(f"<!-- {name} -->\n" + open(p).read())
        p = os.path.join(base, ".github", "workflows", "ci.yml")
        if os.path.exists(p):
            parts.append("<!-- ci.yml -->\n" + open(p).read())
        return "\n\n".join(parts)
    if variant == "ours":
        core = os.path.join(AGENTS_DIR, "AGENTS.md")
        if not os.path.exists(core):
            raise FileNotFoundError(core)
        return open(core).read() + "\n\n" + load_subdocs()
    # raw file path
    with open(variant) as fh:
        return fh.read()


def load_subdocs():
    chunks = []
    for name in sorted(os.listdir(AGENTS_DIR)):
        if name.startswith("AGENTS") or not name.endswith(".md"):
            continue
        chunks.append(f"<!-- {name} -->\n" + open(os.path.join(AGENTS_DIR, name)).read())
    return "\n\n".join(chunks)


def parse(obj, text):
    if isinstance(obj, dict) and "decision" in obj and obj["decision"]:
        return obj
    # fallback: tolerant JSON extraction from text
    import json as _json
    import re
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-z]*\s*|```$", "", t).strip()
    try:
        cand = _json.loads(t)
        if isinstance(cand, dict) and "decision" in cand:
            return cand
    except Exception:
        pass
    m = re.search(r'"decision"\s*:\s*"([A-Za-z]+)"', t)
    decision = m.group(1).lower() if m else None
    m2 = re.search(r'"ci_result"\s*:\s*"([A-Za-z]+)"', t)
    ci = m2.group(1).lower() if m2 else None
    reasons = []
    # reasons array items (quoted strings)
    rm = re.search(r'"reasons"\s*:\s*\[(.*?)\]', t, re.S)
    if rm:
        reasons = re.findall(r'"((?:[^"\\]|\\.)*)"', rm.group(1))
    # also try bullet list after "reasons"
    if not reasons:
        after = t[t.find("reasons"):]
        reasons = [l.strip("- ").strip("\"'") for l in after.splitlines()
                   if l.strip().startswith("-")]
    if not decision:
        if "approve" in t.lower() and "reject" not in t.lower()[:200]:
            decision = "approve"
        else:
            decision = "reject"
    return {"decision": decision, "ci_result": ci or "unknown", "reasons": reasons[:12]}


def main():
    args = sys.argv[1:]
    variant = args[0] if args else "ours"
    split = args[1] if len(args) > 1 else "dev"
    limit = 0
    tag = "untagged"
    for a in args[2:]:
        if a and a.isdigit():
            limit = int(a)
        else:
            tag = a
    os.makedirs(STATE, exist_ok=True)
    os.makedirs(CACHE, exist_ok=True)

    split_file = os.path.join(ROOT, "dataset", "split.json")
    split_data = json.load(open(split_file))
    prs = [p for p in split_data["prs"] if p["split"] == split]
    prs.sort(key=lambda p: p["number"])
    if limit:
        prs = prs[:limit]

    agents_text = variant_text(variant)

    # Output isolation: the judge state directory is bound to (variant, split,
    # tag) so results can never be silently reused across splits or between a
    # stale AGENTS.md and the current one. A manifest records the agents SHA +
    # judge model/reasoning + tag, and stale outputs (different configuration)
    # are ignored.
    import hashlib
    import llm_client
    agents_sha = hashlib.sha256(agents_text.encode("utf-8")).hexdigest()
    # The harness code that renders the prompt is part of the audit contract too:
    # a rendering change (e.g. a patch-budget edit) must invalidate prior per-PR
    # outputs, otherwise a stale-AGENTS_but-new-rendering mix could silently be
    # reused. hash of this file only (versioning of the rendering contract).
    harness_sha = hashlib.sha256(
        open(os.path.abspath(__file__), "rb").read()).hexdigest()
    judge_provider = judge_provider_name()
    judge_model = judge_model_name(judge_provider)
    judge_reasoning = judge_reasoning_name()
    out_dir = os.path.join(STATE, f"judge_{variant.replace('/', '_')}__{split}__{tag}")
    os.makedirs(out_dir, exist_ok=True)
    manifest_path = os.path.join(out_dir, "_manifest.json")

    # stale detection: if a manifest exists with a different agents SHA, judge
    # binding OR harness rendering, the existing per-PR outputs belong to an
    # older configuration -> do not reuse them.
    if os.path.exists(manifest_path):
        try:
            prior = json.load(open(manifest_path))
            stale = (prior.get("agents_sha") != agents_sha or
                      prior.get("judge_provider") != judge_provider or
                      prior.get("judge_model") != judge_model or
                      prior.get("judge_reasoning", "default") != judge_reasoning or
                      prior.get("harness_sha") != harness_sha)
        except Exception:
            stale = True
    else:
        stale = False
    if stale:
        print(f"[judge {variant} {split} {tag}] existing outputs are from a "
              f"different config (prior agents {prior.get('agents_sha','?')[:8]}, "
              f"harness {prior.get('harness_sha','?')[:8]}); "
              f"removing stale per-PR outputs", flush=True)
        for f in os.listdir(out_dir):
            if f.endswith(".json") and not f.startswith("_"):
                os.remove(os.path.join(out_dir, f))

    json.dump({
        "variant": variant, "split": split, "tag": tag,
        "agents_sha": agents_sha, "judge_model": judge_model,
        "judge_provider": judge_provider,
        "judge_reasoning": judge_reasoning,
        "harness_sha": harness_sha,
        "fallback_enabled": judge_fallback_enabled(),
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }, open(manifest_path, "w"), indent=2)

    results = {}
    parse_fails = 0
    for pr in prs:
        n = pr["number"]
        out_path = os.path.join(out_dir, f"{n}.json")
        if os.path.exists(out_path):
            existing = json.load(open(out_path))
            # A transient provider error is not a completed judgment. Keep the
            # state file for auditability, but retry it on a resumed run.
            if existing.get("decision") != "error":
                results[str(n)] = existing
                continue
        snap_path = os.path.join(SNAP, str(n), "first.json")
        if not os.path.exists(snap_path):
            print(f"[skip {n}] no first snapshot")
            continue
        snap = json.load(open(snap_path))
        try:
            # plain-text mode (json_object is unreliable on long prompts);
            # JUDGE_REASONING is a fixed model-catalog effort (for example
            # none or max) and must stay fixed across one evaluation round.
            from llm_client import complete
            # max_tokens high enough that the (reasoning) model has room to emit
            # the full JSON without finish=length; escalation in llm_client still
            # doubles this on empty responses.
            kw = {"max_tokens": 8000, "tag": f"judge-{variant}-{n}",
                  "model": judge_model, "provider": judge_provider,
                  "fallback": judge_fallback_enabled()}
            kw.update(judge_reasoning_kwargs())
            text, _ = complete(
                [{"role": "system", "content": JUDGE_SYSTEM},
                 {"role": "user", "content": build_user_prompt(snap, agents_text)}], **kw)
            obj = parse(text, text)
            if not isinstance(obj, dict) or "decision" not in obj:
                raise ValueError(f"malformed judge output: {text[:200]}")
        except Exception as e:
            print(f"[judge {variant} {n}] LLM error: {e!r}", flush=True)
            parsed = {"pr_number": n, "decision": "error", "ci_result": "unknown",
                      "reasons": [], "_error": repr(e)}
            json.dump(parsed, open(out_path, "w"), indent=2, ensure_ascii=False)
            results[str(n)] = parsed
            continue
        parsed = parse(obj, "")
        parsed["pr_number"] = n
        json.dump(parsed, open(out_path, "w"), indent=2, ensure_ascii=False)
        results[str(n)] = parsed
        print(f"[judge {variant} {n}] decision={parsed.get('decision')} "
              f"ci={parsed.get('ci_result')} reasons={len(parsed.get('reasons', []))}")

    print(f"judge {variant} on {split}: {len(results)} PRs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
