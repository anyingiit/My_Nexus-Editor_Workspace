#!/usr/bin/env python3
"""LLM client for the Nexus-Editor eval harness.

Provider priority (root AGENTS.md 4.2): **opencode-go -> openrouter**.

  - opencode-go : model `deepseek-v4-flash` (the same deepseek-v4-flash-0731
    family deployed by OpenCode Go), base https://opencode.ai/zen/go/v1. Cheaper;
    requires a browser-like User-Agent (Cloudflare rejects the bare urllib UA).
  - openrouter   : model `deepseek/deepseek-v4-flash-0731`, used only when
    opencode-go is unavailable / rate-limited / unstable. Every downgrade is
    logged to reports/cost_ledger.jsonl with a `provider_downgrade` event and
    the reason.

All paid calls are logged to reports/cost_ledger.jsonl with stage (tag),
provider, model, usage tokens and cost. Responses are disk-cached keyed by
(provider, model, messages, max_tokens, extra) so replaying an identical call
is free.
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.normpath(os.path.join(HERE, "..", "reports"))
CACHE_DIR = os.path.normpath(os.path.join(HERE, "llmcache"))

_DEFAULT_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

PROVIDERS = {
    "opencode-go": {
        "base": "https://opencode.ai/zen/go/v1/chat/completions",
        "model": "deepseek-v4-flash",
        "key_env": ["OPENCODE_API_KEY", "OPENCODE_GO_API_KEY"],
        "auth_key": "opencode-go",
        "headers": {"User-Agent": _DEFAULT_UA},
    },
    "openrouter": {
        "base": "https://openrouter.ai/api/v1/chat/completions",
        "model": "deepseek/deepseek-v4-flash-0731",
        "key_env": ["OPENROUTER_API_KEY"],
        "auth_key": "openrouter",
        "headers": {},
    },
}

# Default provider for every LLM call unless LLM_PROVIDER is set.
# A single evaluation round MUST keep one provider+model (root AGENTS.md 4.2a).
DEFAULT_PROVIDER = os.environ.get("LLM_PROVIDER", "opencode-go")


def _read_key(pname):
    auth_json = os.path.expanduser("~/.local/share/opencode/auth.json")
    if os.path.exists(auth_json):
        try:
            data = json.load(open(auth_json))
        except Exception:
            data = {}
        key = (data.get(pname) or {}).get("key")
        if key:
            return key
    for env in PROVIDERS[pname]["key_env"]:
        key = os.environ.get(env)
        if key:
            return key
    raise RuntimeError(
        f"No API key for provider '{pname}'. Set {PROVIDERS[pname]['key_env']} "
        f"or provide its key in opencode auth.json.")


def _resolve(provider):
    if provider not in PROVIDERS:
        raise RuntimeError(f"unknown provider {provider!r}")
    conf = PROVIDERS[provider]
    return {"provider": provider,
            "model": conf["model"],
            "base": conf["base"],
            "key": _read_key(provider),
            "extra_headers": dict(conf["headers"])}


def _build_request(endpoint, key, headers, payload):
    h = {"Authorization": f"Bearer {key}",
         "Content-Type": "application/json"}
    h.update(headers)
    return urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=h,
        method="POST",
    )


def _cache_key(provider, model, messages, max_tokens, extra):
    import hashlib
    blob = json.dumps([provider, model, messages, max_tokens, extra],
                      ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _log_paid(entry):
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(os.path.join(REPORT_DIR, "cost_ledger.jsonl"), "a") as fh:
        fh.write(json.dumps(entry) + "\n")


def _log_downgrade(from_prov, to_prov, reason, tag, model=None):
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(os.path.join(REPORT_DIR, "cost_ledger.jsonl"), "a") as fh:
        fh.write(json.dumps({
            "t": time.time(),
            "event": "provider_downgrade",
            "from_provider": from_prov,
            "to_provider": to_prov,
            "reason": reason,
            "tag": tag,
            "model": model,
        }) + "\n")


def _post_once(res, payload, tag):
    """Single POST attempt against a provider; returns (body_dict, None) or
    (None, error_str). Does not retry (retries are handled by complete)."""
    try:
        req = _build_request(res["base"], res["key"], res["extra_headers"], payload)
        with urllib.request.urlopen(req, timeout=300) as resp:
            raw = resp.read().decode("utf-8")
        body = json.loads(raw)
        if body.get("error"):
            return None, f"LLM error: {body['error']}"
        return body, None
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        return None, f"LLM HTTP {e.code}: {body[:400]}"
    except Exception as e:
        return None, f"LLM transport error: {e!r}"


def _extract_content(body):
    ch = body.get("choices") or []
    if not ch:
        return ""
    c = ch[0].get("message") or {}
    content = c.get("content")
    if not content:
        content = ch[0].get("text") or ""
    return content or ""


def complete(messages, model=None, max_tokens=None, temperature=0.0, cache=True,
             json_mode=False, tag=None, provider=None, fallback=True, **extra):
    """Call the LLM. Returns (text, meta). Cached on disk keyed by payload.

    provider defaults to LLM_PROVIDER env or 'opencode-go'. On a hard failure
    of the primary provider, a documented fallback to openrouter is attempted
    (with a provider_downgrade ledger entry)."""
    if provider is None:
        provider = os.environ.get("LLM_PROVIDER", DEFAULT_PROVIDER)

    def _run(prov):
        res = _resolve(prov)
        mmodel = model or res["model"]
        mxt = max_tokens or 4000
        ck = _cache_key(prov, mmodel, messages, mxt, extra)
        os.makedirs(CACHE_DIR, exist_ok=True)
        cache_path = os.path.join(CACHE_DIR, f"{ck}.json")
        if cache and os.path.exists(cache_path):
            with open(cache_path) as fh:
                cached = json.load(fh)
            return cached, True, None

        payload = {
            "model": mmodel,
            "messages": messages,
            "max_tokens": mxt,
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        req_extra = dict(extra)
        if req_extra.get("reasoning"):
            payload["reasoning"] = req_extra.pop("reasoning")

        last_err = None
        for attempt in range(8):
            body, err = _post_once(res, payload, tag)
            if body is not None:
                break
            last_err = err
            if any(c in err for c in ("HTTP 429", "HTTP 500", "HTTP 502",
                                      "HTTP 503", "HTTP 504", "HTTP 408")):
                time.sleep(3 + 2 * attempt)
                continue
            # non-transient -> give up on this provider
            break
        if body is None:
            return None, False, last_err

        content = _extract_content(body)
        # empty content: retry with escalated max_tokens (reasoning budget)
        for attempt in range(6):
            if content:
                break
            finish = (body.get("choices") or [{}])[0].get("finish_reason")
            print(f"[llm empty {prov}] retry {attempt}: finish={finish}",
                  file=sys.stderr, flush=True)
            if payload.get("max_tokens", 0) < 12000:
                payload["max_tokens"] = min((payload.get("max_tokens") or 2000) * 2, 12000)
            body, err = _post_once(res, payload, tag)
            if body is None:
                last_err = err
                break
            content = _extract_content(body)
            time.sleep(2 + attempt)
        if not content:
            return None, False, f"LLM returned empty content after retries: {last_err}"

        usage = body.get("usage", {})
        cost = body.get("cost")
        try:
            cost = float(cost) if cost is not None else 0.0
        except (TypeError, ValueError):
            cost = 0.0
        meta = {"model": body.get("model", mmodel), "provider": prov,
                "usage": usage, "cost": cost}
        _log_paid({
            "t": time.time(), "tag": tag, "model": meta["model"],
            "provider": prov, "cached": False, "usage": usage,
            "cost": meta["cost"], "cache_key": ck,
        })
        if cache:
            with open(cache_path, "w") as fh:
                json.dump({"text": content, "meta": meta}, fh)
        return {"text": content, "meta": meta}, False, None

    result, from_cache, err = _run(provider)
    if result is not None:
        return result["text"], {**result["meta"], "from_cache": from_cache}

    # Primary provider failed hard. If we are on opencode-go, fall back to
    # openrouter and record the downgrade (root AGENTS.md 4.2). Evaluation
    # rounds can disable this path so one round remains bound to one
    # model+provider combination (root AGENTS.md 4.2a).
    if provider == "opencode-go" and fallback:
        reason = f"opencode-go failed: {err}"
        _log_downgrade("opencode-go", "openrouter", reason, tag, model=model)
        print(f"[llm] provider downgrade opencode-go -> openrouter: {reason}",
              file=sys.stderr, flush=True)
        fallback, from_cache2, err2 = _run("openrouter")
        if fallback is not None:
            return fallback["text"], {**fallback["meta"],
                                      "from_cache": from_cache2,
                                      "downgraded_from": "opencode-go"}
        raise RuntimeError(f"LLM failed on opencode-go and openrouter fallback "
                           f"({err2!r})")
    raise RuntimeError(f"LLM failed on {provider}: {err}")


def complete_json(messages, **kwargs):
    """Ask the LLM for a JSON object; parse defensively."""
    extra_fix = kwargs.pop("extra_json_fix", False)
    text, meta = complete(messages, json_mode=True, **kwargs)
    try:
        return json.loads(text), meta
    except json.JSONDecodeError:
        t = text.strip()
        if t.startswith("```"):
            t = t.split("\n", 1)[1]
            if t.rstrip().endswith("```"):
                t = t.rstrip()[:-3]
        try:
            return json.loads(t), meta
        except json.JSONDecodeError:
            if extra_fix:
                fix_msgs = messages + [
                    {"role": "assistant", "content": text[:6000]},
                    {"role": "user",
                     "content": "Your previous response was not valid JSON. "
                                "Output ONLY a valid JSON object now, same schema."},
                ]
                text2, meta2 = complete(fix_msgs, json_mode=True,
                                        max_tokens=kwargs.get("max_tokens", 1200),
                                        cache=True, tag=kwargs.get("tag", "") + "-fix")
                return json.loads(text2), meta2
            raise
