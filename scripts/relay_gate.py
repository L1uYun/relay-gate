#!/usr/bin/env python
"""Manage the user's personal NewAPI gateway.

Secrets stay in Sigil. Bare credential names resolve from Sigil; use explicit
env: / process-env: / sigil-env: prefixes when the source must differ. This
script never prints full admin tokens, NewAPI caller tokens, or upstream API
keys.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


DEFAULT_BASE_URL = "https://newapi.l1uyun.top:8080"
DEFAULT_ADMIN_TOKEN_CRED = "l1uyun-newapi-admin-access-token"
DEFAULT_GENERAL_TOKEN_CRED = "l1uyun-newapi-general-api-key"
DEFAULT_USER_ID = "1"
DEFAULT_CODEX_CONFIG_PATH = Path.home() / ".codex" / "config.toml"
DEFAULT_CODEX_CATALOG_PATH = Path.home() / ".codex" / "cc-switch-model-catalog.json"
DEFAULT_CODEX_MODELS_CACHE_PATH = Path.home() / ".codex" / "models_cache.json"
DEFAULT_CODEXPLUSPLUS_SETTINGS_PATH = Path.home() / ".codex-session-delete" / "settings.json"
DEFAULT_CC_SWITCH_DB_PATH = Path.home() / ".cc-switch" / "cc-switch.db"
DEFAULT_PI_MODELS_PATH = Path.home() / ".pi" / "agent" / "models.json"
DEFAULT_CODEBUDDY_MODELS_PATH = Path.home() / ".codebuddy" / "models.json"
DEFAULT_WORKBUDDY_MODELS_PATH = Path.home() / ".workbuddy" / "models.json"
DEFAULT_SERVITOR_PI_MODELS_CACHE_PATH = Path.home() / ".servitor" / "model_cache" / "pi_models.json"
DEFAULT_CODEX_CATALOG_TASK_NAME = "CodexModelMenuCacheWatcher"
DEFAULT_CODEX_CATALOG_LOG_PATH = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "RelayGate" / "codex-catalog-sync.json"
SIGIL = Path(r"D:\AgentWork\tools\sigil\src\sigil.py")
RESPONSES_BRIDGE_OPTION_KEY = "global.responses_to_chat_completions_policy"
CHANNEL_STATUS_ENABLED = 1
CHANNEL_STATUS_MANUAL_DISABLED = 2
CHANNEL_STATUS_AUTO_DISABLED = 3
QUOTA_HOLD_REASON = "quota_exhausted_manual_hold"
AUTO_PROBE_MODELS = {"auto", "buddy-auto"}
SOFT_DISABLE_KEYWORDS = "\n".join(
    [
        "This organization has been disabled.",
        "Permission denied",
        "The security token included in the request is invalid",
        "Operation not allowed",
        "Your account is not authorized",
    ]
)

CHANNEL_TYPES: dict[str, int] = {
    "openai": 1,
    "anthropic": 14,
    "openrouter": 20,
    "gemini": 24,
    "siliconflow": 40,
    "deepseek": 43,
    "custom": 8,
}

DEFAULT_MODELS = "gpt-5.5,gpt-5.4"



def _reasoning_presets(efforts: list[str]) -> list[dict[str, str]]:
    descriptions = {
        "none": "Disable reasoning",
        "low": "Fast responses with lighter reasoning",
        "medium": "Balances speed and reasoning depth for everyday tasks",
        "high": "Greater reasoning depth for complex problems",
        "xhigh": "Extra high reasoning depth for complex problems",
        "max": "Maximum reasoning depth for the hardest problems",
        "ultra": "Maximum reasoning with automatic task delegation",
    }
    return [{"effort": effort, "description": descriptions[effort]} for effort in efforts]


def _with_codex_reasoning_floor(levels: Any) -> list[dict[str, Any]]:
    floor = ("low", "medium", "high", "xhigh")
    order = ("none", "minimal", *floor, "max", "ultra")
    existing: dict[str, dict[str, Any]] = {}
    unknown: list[dict[str, Any]] = []
    for item in levels if isinstance(levels, list) else []:
        if not isinstance(item, dict):
            continue
        effort = str(item.get("effort") or "").strip()
        if effort in order:
            existing.setdefault(effort, dict(item))
        else:
            unknown.append(dict(item))
    for item in _reasoning_presets(list(floor)):
        existing.setdefault(item["effort"], item)
    return [existing[effort] for effort in order if effort in existing] + unknown


CODEX_MODEL_OVERRIDES: dict[str, dict[str, Any]] = {
    "gpt-5.6-sol": {
        "display_name": "GPT-5.6-Sol",
        "description": "Latest frontier agentic coding model.",
        "default_reasoning_level": "low",
        "supported_reasoning_levels": _reasoning_presets(["low", "medium", "high", "xhigh", "max", "ultra"]),
        "input_modalities": ["text", "image"],
        "supports_parallel_tool_calls": True,
        "shell_type": "shell_command",
        "apply_patch_tool_type": "freeform",
        "web_search_tool_type": "text_and_image",
        "tool_mode": "code_mode_only",
        "multi_agent_version": "v2",
        "use_responses_lite": True,
    },
    "gpt-5.6-terra": {
        "display_name": "GPT-5.6-Terra",
        "description": "Latest frontier agentic coding model.",
        "default_reasoning_level": "medium",
        "supported_reasoning_levels": _reasoning_presets(["low", "medium", "high", "xhigh", "max", "ultra"]),
        "input_modalities": ["text", "image"],
        "supports_parallel_tool_calls": True,
        "shell_type": "shell_command",
        "apply_patch_tool_type": "freeform",
        "web_search_tool_type": "text_and_image",
        "tool_mode": "code_mode_only",
        "multi_agent_version": "v2",
        "use_responses_lite": True,
    },
    "gpt-5.6-luna": {
        "display_name": "GPT-5.6-Luna",
        "description": "Latest frontier agentic coding model.",
        "default_reasoning_level": "medium",
        "supported_reasoning_levels": _reasoning_presets(["low", "medium", "high", "xhigh", "max"]),
        "input_modalities": ["text", "image"],
        "supports_parallel_tool_calls": True,
        "shell_type": "shell_command",
        "apply_patch_tool_type": "freeform",
        "web_search_tool_type": "text_and_image",
        "tool_mode": "code_mode_only",
        "multi_agent_version": "v1",
        "use_responses_lite": True,
    },
    "grok-4.5": {
        "display_name": "Grok 4.5",
        "description": "xAI coding and agentic workflow model.",
        "default_reasoning_level": "medium",
        "supported_reasoning_levels": _reasoning_presets(["low", "medium", "high"]),
    },
    "grok-4.3": {
        "display_name": "Grok 4.3",
        "description": "xAI Responses API model.",
        "default_reasoning_level": "medium",
        "supported_reasoning_levels": _reasoning_presets(["none", "low", "medium", "high"]),
    },
    "grok-3-mini": {
        "display_name": "Grok 3 Mini",
        "description": "Compatibility alias currently resolved by xAI to Grok 4.3.",
        "default_reasoning_level": "medium",
        "supported_reasoning_levels": _reasoning_presets(["none", "low", "medium", "high"]),
    },
    "grok-3-mini-fast": {
        "display_name": "Grok 3 Mini Fast",
        "description": "Fast compatibility alias currently resolved by xAI to Grok 4.3.",
        "default_reasoning_level": "medium",
        "supported_reasoning_levels": _reasoning_presets(["none", "low", "medium", "high"]),
    },
    "workbuddy-glm-5.2": {
        "display_name": "WorkBuddy GLM-5.2",
        "description": "WorkBuddy relay alias for GLM-5.2.",
        "default_reasoning_level": "high",
        "supported_reasoning_levels": _reasoning_presets(["none", "high"]),
    },
}

CODEX_MODEL_QUARANTINE: dict[str, str] = {
    "gpt-5.6-luna": "CPA advertises the id but has no eligible Codex auth; live Responses probe returned auth_unavailable on 2026-07-10.",
}
class CliError(RuntimeError):
    pass


class ContextMeta:
    """Context-window/max-tokens resolution from OpenRouter + local overrides.

    Single authority for model context metadata. Consumed by codex-catalog sync
    (context_window field) and agent-models sync (pi contextWindow/maxTokens,
    codebuddy context_window/max_tokens). OpenRouter /api/v1/models is the
    primary source; UPSTREAM_OVERRIDES encode API-level rated limits from resold
    channels (e.g. xfyun glm-5.2=500K rated limit); CODEX_PRODUCT_OVERRIDES encode
    Codex Desktop soft compaction triggers, set lower than UPSTREAM_OVERRIDES to
    give async compaction headroom before the upstream rejects the request
    (karma #147: Codex Desktop does not hard-truncate at context_window).
    """

    OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
    CACHE_PATH = Path.home() / ".servitor" / "model_cache" / "openrouter_models.json"
    CACHE_TTL = 3600 * 6  # 6 hours

    OR_MAP = {
        "Kimi-K2.6": "moonshotai/kimi-k2.6",
        "GLM-5.1": "z-ai/glm-5.1",
        "MiMo-V2.5-Pro": "xiaomi/mimo-v2.5-pro",
        "MiMo-V2.5": "xiaomi/mimo-v2.5",
        "GLM-5-Turbo": "z-ai/glm-5-turbo",
        "GLM-5V-Turbo": "z-ai/glm-5v-turbo",
        "GLM-5": "z-ai/glm-5",
        "GLM-4.7": "z-ai/glm-4.7",
        "deepseek-v4-flash": "deepseek/deepseek-v4-flash",
        "deepseek-v4-pro": "deepseek/deepseek-v4-pro",
        "claude-fable-5": "anthropic/claude-fable-5",
        "claude-haiku-4-5": "anthropic/claude-haiku-4.5",
        "claude-opus-4-6": "anthropic/claude-opus-4.6",
        "claude-opus-4-7": "anthropic/claude-opus-4.7",
        "claude-opus-4-8": "anthropic/claude-opus-4.8",
        "claude-sonnet-4-6": "anthropic/claude-sonnet-4.6",
        "claude-sonnet-5": "anthropic/claude-sonnet-5",
        "glm-5.2": "z-ai/glm-5.2",
        "gpt-5.4": "openai/gpt-5.4",
        "gpt-5.4-mini": "openai/gpt-5.4-mini",
        "gpt-5.5": "openai/gpt-5.5",
        "grok-4.3": "x-ai/grok-4.3",
        "workbuddy-glm-5.2": "z-ai/glm-5.2",
        "kimi-for-coding": "moonshotai/kimi-k2.6",
        "step-3.7-flash": "stepfun/step-3.7-flash",
    }

    SUFFIXES = ["-think-search", "-think", "-search", "-console", "-fast", "-high", "-low", "-medium"]

    UPSTREAM_OVERRIDES = {
        "glm-5.2": 500_000,
        "gpt-5.5": 256_000,
        "gpt-5.5-openai-compact": 256_000,
        "gpt-5.6-sol": 272_000,  # OpenAI product limit reverted from 372k (2026-07)
        "gpt-5.6-terra": 272_000,  # keep 5.6 family aligned with Sol product limit
        "gpt-5.6-luna": 272_000,
        "grok-4.5": 500_000,
        "grok-4.3": 1_000_000,
        "grok-3-mini": 1_000_000,
        "grok-3-mini-fast": 1_000_000,
        "workbuddy-glm-5.2": 500_000,
    }

    # Codex Desktop uses context_window as an ASYNC compaction trigger, not a hard
    # truncation limit. Context keeps growing between trigger and completion.
    # Set lower than UPSTREAM_OVERRIDES to give compaction headroom before the
    # upstream hard limit rejects the request (karma #147).
    CODEX_PRODUCT_OVERRIDES: dict[str, int] = {
        "glm-5.2": 400_000,
        "workbuddy-glm-5.2": 400_000,
        # Codex async compaction needs headroom under the 500k upstream hard limit.
        # Without this, grok-4.5 triggers at ~475k and can grow past 500k before compact finishes.
        "grok-4.5": 400_000,
        # OpenAI product context for GPT-5.6 family reverted to 272k (from 372k) to reduce over-billing.
        # Leave a small async-compaction cushion under the product limit.
        "gpt-5.6-sol": 216_000,  # 80% of 272k product limit for async compaction headroom
        "gpt-5.6-terra": 216_000,
        "gpt-5.6-luna": 216_000,
    }

    MAX_OUT_FALLBACK = {
        "xiaomi/mimo-v2.5": 131_072,
        "x-ai/grok-4.3": 128_000,
    }

    NO_OR_MATCH = {"MiMo-V2-Flash"}

    _cache: dict[str, dict] | None = None

    @classmethod
    def resolve_or_id(cls, model_id: str) -> str | None:
        if model_id in cls.NO_OR_MATCH:
            return None
        if model_id in cls.OR_MAP:
            return cls.OR_MAP[model_id]
        for suf in cls.SUFFIXES:
            if model_id.endswith(suf):
                base = model_id[:-len(suf)]
                if base in cls.OR_MAP:
                    return cls.OR_MAP[base]
        return None

    @classmethod
    def _fetch_openrouter(cls) -> dict[str, dict]:
        if cls._cache is not None:
            return cls._cache
        try:
            if cls.CACHE_PATH.exists():
                age = time.time() - cls.CACHE_PATH.stat().st_mtime
                if age < cls.CACHE_TTL:
                    data = json.loads(cls.CACHE_PATH.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and data:
                        cls._cache = data
                        return cls._cache
        except Exception:
            pass
        try:
            import urllib.request
            req = urllib.request.Request(cls.OPENROUTER_URL, headers={"User-Agent": "relay-gate/context-meta"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
            models = {}
            for m in raw.get("data", []):
                mid = m.get("id")
                if mid:
                    models[mid] = m
            cls._cache = models
            try:
                cls.CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
                cls.CACHE_PATH.write_text(json.dumps(models, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass
            return cls._cache
        except Exception:
            cls._cache = {}
            return cls._cache

    @classmethod
    def resolve(cls, model_id: str, *, for_codex: bool = False) -> tuple[int | None, int | None]:
        or_id = cls.resolve_or_id(model_id)
        or_model = cls._fetch_openrouter().get(or_id) if or_id else None
        ctx = or_model.get("context_length") if or_model else None
        tp = (or_model.get("top_provider") or {}) if or_model else {}
        max_out = tp.get("max_completion_tokens")
        if not max_out and or_id and or_id in cls.MAX_OUT_FALLBACK:
            max_out = cls.MAX_OUT_FALLBACK[or_id]
        if model_id in cls.UPSTREAM_OVERRIDES:
            ctx = cls.UPSTREAM_OVERRIDES[model_id]
        if for_codex and model_id in cls.CODEX_PRODUCT_OVERRIDES:
            ctx = cls.CODEX_PRODUCT_OVERRIDES[model_id]
        return ctx, max_out


def run_sigil(args: list[str], *, input_text: str | None = None) -> str:
    cmd = [sys.executable, str(SIGIL), *args]
    try:
        proc = subprocess.run(
            cmd,
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            timeout=15,
        )
    except subprocess.TimeoutExpired as exc:
        raise CliError(
            "Sigil credential command timed out; check SIGIL_PASSPHRASE or pass credentials through an explicit env source"
        ) from exc
    if proc.returncode != 0:
        raise CliError(proc.stderr.strip() or proc.stdout.strip())
    return proc.stdout.strip()


def reveal_credential(name: str) -> str:
    if name.startswith("env:") or name.startswith("process-env:"):
        env_name = name.split(":", 1)[1]
        value = os.environ.get(env_name)
        if not value:
            raise CliError(f"process environment variable not found: {env_name}")
        return value.strip()
    if name.startswith("sigil-env:"):
        return reveal_env_secret(name.split(":", 1)[1])
    return run_sigil(["secret", "show", name, "--reveal"]).strip()


def reveal_env_secret(name: str) -> str:
    return run_sigil(["env", "show", name, "--reveal"]).strip()


def reveal_credential_or_env(name: str) -> str:
    try:
        return reveal_credential(name)
    except CliError:
        return reveal_env_secret(name)


def store_credential(name: str, secret: str, *, kind: str, note: str) -> None:
    run_sigil(
        ["secret", "set", name, "--kind", kind, "--note", note, "--secret-stdin"],
        input_text=secret,
    )


def redact(value: str | None, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}...{value[-keep:]}"


def secret_fingerprint(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def effective_apply(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "apply", False)) and not bool(getattr(args, "dry_run", False))


def is_verbose(args: argparse.Namespace) -> bool:
    """Whether the user asked for the verbose human surface.

    Verbose expands the default bounded human output with previews, key
    fingerprints, and diagnostic metadata. Compact (default) human output
    stays an action summary; --json always carries the full schema.
    """
    return bool(getattr(args, "verbose", False))


def emit(data: Any, as_json: bool, human_text: str | None = None) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    if human_text is not None:
        print(human_text)
        return
    if isinstance(data, str):
        print(data)
        return
    print(json.dumps(data, ensure_ascii=False, indent=2))


def emit_and_optionally_log(data: Any, as_json: bool, json_log: str = "") -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if json_log:
        log_path = Path(json_log)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(text + "\n", encoding="utf-8")
    if as_json:
        print(text)
        return
    print(text)


def api_request(
    args: argparse.Namespace,
    method: str,
    path: str,
    *,
    json_body: Any | None = None,
    params: dict[str, Any] | None = None,
) -> Any:
    token = reveal_credential(args.admin_token_cred)
    headers = {
        "Authorization": token,
        "New-Api-User": str(args.user_id),
        "Content-Type": "application/json",
    }
    url = args.base_url.rstrip("/") + path
    proxies = None
    proxy_url = getattr(args, "proxy_url", "") or ""
    if proxy_url:
        proxies = {"http": proxy_url, "https": proxy_url}
    resp = requests.request(
        method,
        url,
        headers=headers,
        json=json_body,
        params=params,
        timeout=args.timeout,
        proxies=proxies,
    )
    try:
        data = resp.json()
    except ValueError as exc:
        raise CliError(f"non-JSON response from {path}: HTTP {resp.status_code}") from exc
    if resp.status_code >= 400:
        raise CliError(f"HTTP {resp.status_code} from {path}: {data}")
    if data.get("success") is False:
        raise CliError(f"NewAPI error from {path}: {data.get('message')}")
    return data


def channel_patch(
    args: argparse.Namespace,
    channel_id: int,
    fields: dict[str, Any],
    *,
    status: int | None = None,
) -> dict[str, Any]:
    """Update a channel via PUT /api/channel/ using PATCH semantics.

    NewAPI UpdateChannel rejects PUT bodies containing the status key
    with 'Invalid parameters'. Status is an operational field managed
    via POST /api/channel/:id/status. This helper sends only id +
    changed fields, then optionally updates status via the dedicated
    endpoint.
    """
    payload: dict[str, Any] = {"id": channel_id}
    # Defense in depth: NewAPI rejects PUT bodies that contain status.
    payload.update({k: v for k, v in fields.items() if v is not None and k != "status"})
    response = api_request(args, "PUT", "/api/channel/", json_body=payload)
    if status is not None:
        api_request(args, "POST", f"/api/channel/{channel_id}/status", json_body={"status": status})
    return response


def caller_api_request(
    args: argparse.Namespace,
    method: str,
    path: str,
    *,
    json_body: Any | None = None,
) -> Any:
    token = reveal_credential(args.caller_token_cred)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    url = args.base_url.rstrip("/") + path
    proxies = None
    proxy_url = getattr(args, "proxy_url", "") or ""
    if proxy_url:
        proxies = {"http": proxy_url, "https": proxy_url}
    resp = requests.request(
        method,
        url,
        headers=headers,
        json=json_body,
        timeout=args.timeout,
        proxies=proxies,
    )
    try:
        data = resp.json()
    except ValueError as exc:
        raise CliError(f"non-JSON response from {path}: HTTP {resp.status_code}") from exc
    if resp.status_code >= 400:
        raise CliError(f"HTTP {resp.status_code} from {path}: {data}")
    return data

def sse_probe_via_relay(
    args: argparse.Namespace,
    *,
    model: str,
    prompt: str = "ping",
    max_tokens: int = 16,
    extra_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send a real streaming chat completion through the NewAPI gateway and
    summarize the SSE result.

    This bypasses NewAPI's internal /api/channel/test/{id}, which assumes the
    upstream returns non-stream JSON. Many real upstreams (e.g. type=8
    passthrough channels like volcengine ark or buddy/codebuddy) only return
    text/event-stream and embed business errors inside SSE frames, so the
    native test endpoint reports false negatives.

    Returns a dict with: ok, http_status, latency_ms, first_token_ms,
    upstream_model, finish_reason, content_preview, prompt_tokens,
    completion_tokens, error.
    """

    token = reveal_credential(args.caller_token_cred)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "stream": True,
    }
    if extra_body:
        body.update(extra_body)

    url = args.base_url.rstrip("/") + "/v1/chat/completions"
    proxies = None
    proxy_url = getattr(args, "proxy_url", "") or ""
    if proxy_url:
        proxies = {"http": proxy_url, "https": proxy_url}

    started = time.monotonic()
    first_token_ms: float | None = None
    content_chunks: list[str] = []
    upstream_model = ""
    finish_reason = ""
    sse_error: dict[str, Any] | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    try:
        resp = requests.post(
            url,
            headers=headers,
            json=body,
            timeout=args.timeout,
            proxies=proxies,
            stream=True,
        )
    except requests.RequestException as exc:
        return {
            "ok": False,
            "http_status": None,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error": f"network: {exc}",
            "via": "relay",
            "model": model,
        }

    http_status = resp.status_code
    if http_status >= 400:
        try:
            err_body = resp.text[:400]
        finally:
            resp.close()
        return {
            "ok": False,
            "http_status": http_status,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error": f"HTTP {http_status}: {err_body}",
            "via": "relay",
            "model": model,
        }

    try:
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw:
                continue
            line = raw.strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].lstrip()
            if payload == "[DONE]":
                break
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and obj.get("error"):
                err = obj["error"]
                if isinstance(err, dict):
                    sse_error = err
                else:
                    sse_error = {"message": str(err)}
                continue
            if not isinstance(obj, dict):
                continue
            if not upstream_model and obj.get("model"):
                upstream_model = str(obj.get("model"))
            choices = obj.get("choices") or []
            if choices and isinstance(choices, list):
                ch0 = choices[0] or {}
                delta = ch0.get("delta") or {}
                piece = delta.get("content")
                if piece:
                    if first_token_ms is None:
                        first_token_ms = (time.monotonic() - started) * 1000.0
                    content_chunks.append(str(piece))
                fr = ch0.get("finish_reason")
                if fr:
                    finish_reason = str(fr)
            usage = obj.get("usage") or {}
            if isinstance(usage, dict):
                if usage.get("prompt_tokens") is not None:
                    prompt_tokens = int(usage.get("prompt_tokens") or 0)
                if usage.get("completion_tokens") is not None:
                    completion_tokens = int(usage.get("completion_tokens") or 0)
    finally:
        resp.close()

    latency_ms = int((time.monotonic() - started) * 1000)
    content = "".join(content_chunks)

    if sse_error:
        return {
            "ok": False,
            "http_status": http_status,
            "latency_ms": latency_ms,
            "first_token_ms": int(first_token_ms) if first_token_ms is not None else None,
            "upstream_model": upstream_model or None,
            "error": preview_text(sse_error.get("message") or sse_error.get("msg") or sse_error, limit=240),
            "sse_error_code": sse_error.get("code") if isinstance(sse_error, dict) else None,
            "via": "relay",
            "model": model,
        }

    ok = bool(finish_reason) and bool(content)
    return {
        "ok": ok,
        "http_status": http_status,
        "latency_ms": latency_ms,
        "first_token_ms": int(first_token_ms) if first_token_ms is not None else None,
        "upstream_model": upstream_model or None,
        "finish_reason": finish_reason or None,
        "content_preview": preview_text(content, limit=120),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "via": "relay",
        "model": model,
    }



def normalize_base_url(value: str, *, strip_v1: bool) -> str:
    url = value.strip().rstrip("/")
    if strip_v1 and url.endswith("/v1"):
        url = url[:-3].rstrip("/")
    return url


def split_list(value: Any, default: str = "") -> list[str]:
    raw = value if value not in (None, "") else default
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    return [item.strip() for item in str(raw).replace("\n", ",").split(",") if item.strip()]


def coerce_channel_type(value: Any) -> int:
    if value in (None, ""):
        return CHANNEL_TYPES["openai"]
    if isinstance(value, int):
        return value
    value_s = str(value).strip().lower()
    if value_s.isdigit():
        return int(value_s)
    if value_s not in CHANNEL_TYPES:
        raise CliError(f"unknown channel type: {value}")
    return CHANNEL_TYPES[value_s]


def default_channel_setting() -> str:
    return json.dumps(
        {
            "force_format": False,
            "thinking_to_content": False,
            "proxy": "",
            "pass_through_body_enabled": False,
            "system_prompt": "",
            "system_prompt_override": False,
        },
        ensure_ascii=False,
    )


def default_channel_settings(channel_type: int) -> str:
    if channel_type == 1:
        return json.dumps(
            {
                "allow_service_tier": False,
                "disable_store": False,
                "allow_safety_identifier": False,
                "allow_include_obfuscation": False,
                "allow_inference_geo": False,
            },
            ensure_ascii=False,
        )
    return json.dumps({}, ensure_ascii=False)


def build_channel_create_payload(args: argparse.Namespace, key: str) -> dict[str, Any]:
    models = ",".join(split_list(args.models))
    if not models:
        raise CliError("provide at least one model")
    channel_type = coerce_channel_type(args.type)
    model_list = split_list(args.models)
    channel = {
        "name": args.name,
        "type": channel_type,
        "base_url": normalize_base_url(args.base_url_value, strip_v1=not args.keep_v1),
        "key": key,
        "models": models,
        "group": ",".join(split_list(args.group)),
        "model_mapping": args.model_mapping or None,
        "priority": args.priority,
        "weight": args.weight,
        "test_model": args.test_model or model_list[0],
        "auto_ban": args.auto_ban,
        "status": args.status,
        "tag": args.tag or None,
        "remark": args.remark or "",
        "setting": default_channel_setting(),
        "settings": default_channel_settings(channel_type),
    }
    return {
        "mode": "single",
        "multi_key_mode": "random",
        "batch_add_set_key_prefix_2_name": False,
        "channel": channel,
    }


def _routing_hint_for_create(channel: dict[str, Any]) -> dict[str, Any]:
    base_url = str(channel.get("base_url") or "")
    hint: dict[str, Any] = {"base_url": base_url}
    chat_completions_path = "/chat/completions"
    responses_path = "/responses"
    if chat_completions_path in base_url or responses_path in base_url:
        hint["passthrough_full_url"] = True
        hint["note"] = (
            "base_url already includes a request path; NewAPI will treat this as a "
            "full-URL passthrough channel (do not re-append /chat/completions). "
            "This is the same shape as type=8 volcengine ark coding channels."
        )
    else:
        hint["passthrough_full_url"] = False
    if int(channel.get("type") or 0) == 1 and not hint["passthrough_full_url"]:
        hint["adapter"] = "OpenAI-compatible (NewAPI appends /chat/completions or /responses)"
    return hint


def preview_channel_create_payload(payload: dict[str, Any]) -> dict[str, Any]:
    channel = dict(payload["channel"])
    channel["key"] = redact_secret_field(channel.get("key"))
    return {
        "mode": payload["mode"],
        "channel": channel,
        "routing_hint": _routing_hint_for_create(channel),
    }


def redact_secret_field(value: Any) -> dict[str, Any]:
    text = "" if value is None else str(value)
    return {
        "present": bool(text),
        "sha256": secret_fingerprint(text),
        "redacted": redact(text),
    }


def preview_text(value: Any, *, limit: int = 300) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def human_field_text(value: Any, *, limit: int = 300) -> str:
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return preview_text(value, limit=limit)


def is_sensitive_field(name: Any) -> bool:
    text = str(name).lower()
    exact = {
        "key",
        "api_key",
        "apikey",
        "openai_api_key",
        "token",
        "api_token",
        "access_token",
        "refresh_token",
        "admin_token",
        "secret",
        "password",
        "authorization",
        "credential",
    }
    if text in exact:
        return True
    return any(marker in text for marker in ["secret", "password", "authorization", "credential"])


def redacted_tree(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if is_sensitive_field(key):
                result[key] = redact_secret_field(item)
            else:
                result[key] = redacted_tree(item)
        return result
    if isinstance(value, list):
        return [redacted_tree(item) for item in value]
    return value


def redacted_channel(channel: dict[str, Any]) -> dict[str, Any]:
    item = dict(channel)
    if "key" in item:
        item["key"] = redact_secret_field(item.get("key"))
    for field in ["header_override", "param_override", "setting", "settings", "other"]:
        if field in item and isinstance(item[field], str):
            item[field] = preview_text(item[field], limit=500)
    return item


def channel_summary(channel: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": channel.get("id"),
        "name": channel.get("name"),
        "type": channel.get("type"),
        "base_url": channel.get("base_url"),
        "models": channel.get("models"),
        "group": channel.get("group"),
        "status": channel.get("status"),
        "priority": channel.get("priority"),
        "weight": channel.get("weight"),
        "tag": channel.get("tag"),
        "response_time": channel.get("response_time"),
        "test_time": channel.get("test_time"),
    }


def redacted_token(token: dict[str, Any]) -> dict[str, Any]:
    item = dict(token)
    if "key" in item:
        item["key"] = redact_secret_field(item.get("key"))
    return item


def token_summary(token: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": token.get("id"),
        "name": token.get("name"),
        "status": token.get("status"),
        "expired_time": token.get("expired_time"),
        "remain_quota": token.get("remain_quota"),
        "unlimited_quota": token.get("unlimited_quota"),
        "model_limits_enabled": token.get("model_limits_enabled"),
        "model_limits": token.get("model_limits"),
        "allow_ips": token.get("allow_ips"),
        "group": token.get("group"),
        "cross_group_retry": token.get("cross_group_retry"),
        "used_quota": token.get("used_quota"),
        "accessed_time": token.get("accessed_time"),
        "key": redact_secret_field(token.get("key")),
    }


def change_summary(before: dict[str, Any], after: dict[str, Any], fields: list[str]) -> list[dict[str, Any]]:
    changes = []
    for field in fields:
        if before.get(field) == after.get(field):
            continue
        old = before.get(field)
        new = after.get(field)
        if is_sensitive_field(field):
            old = redact_secret_field(old)
            new = redact_secret_field(new)
        changes.append({"field": field, "before": old, "after": new})
    return changes


def read_secret_arg(args: argparse.Namespace, *, optional: bool = False) -> str:
    if getattr(args, "api_key_cred", ""):
        return reveal_credential(args.api_key_cred).strip()
    if getattr(args, "key_stdin", False):
        secret = sys.stdin.read().strip()
        if secret:
            return secret
    if optional:
        return ""
    raise CliError("provide --api-key-cred or --key-stdin")


def fetch_existing_channels(args: argparse.Namespace) -> list[dict[str, Any]]:
    first = api_request(args, "GET", "/api/channel/", params={"p": 1, "page_size": 100})
    data = first.get("data", {}) if isinstance(first, dict) else {}
    total = int(data.get("total") or 0)
    items = list(data.get("items") or [])
    page_size = 100
    page = 2
    while len(items) < total:
        resp = api_request(args, "GET", "/api/channel/", params={"p": page, "page_size": page_size})
        batch = resp.get("data", {}).get("items") or []
        if not batch:
            break
        items.extend(batch)
        page += 1
    return items


def _human_doctor(payload: dict[str, Any], verbose: bool) -> str:
    parts = [
        f"service_success={str(payload.get('service_success')).lower()}",
        f"version={human_field_text(payload.get('version'), limit=160)}",
        f"channels={human_field_text(payload.get('channels_total'), limit=80)}",
    ]
    if verbose:
        for key in ("user_id", "admin_api"):
            value = payload.get(key)
            if value is not None:
                parts.append(f"{key}={human_field_text(value, limit=160)}")
    return " ".join(parts)


def _human_channels_list(result: dict[str, Any], page: int, verbose: bool) -> str:
    total = result.get("total")
    items = result.get("items") or []
    lines = [f"channels total={human_field_text(total, limit=80)} showing={len(items)} page={human_field_text(page, limit=80)}"]
    for item in items:
        parts = [
            f"id={human_field_text(item.get('id'), limit=80)}",
            f"status={human_field_text(item.get('status'), limit=80)}",
            f"priority={human_field_text(item.get('priority'), limit=80)}",
            f"weight={human_field_text(item.get('weight'), limit=80)}",
            f"name={human_field_text(item.get('name'), limit=160)}",
        ]
        if verbose:
            for key in ("group", "tag", "models", "response_time", "test_time"):
                value = item.get(key)
                if value is not None:
                    parts.append(f"{key}={human_field_text(value, limit=300)}")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def _human_tokens_list(result: dict[str, Any], verbose: bool) -> str:
    total = result.get("total")
    items = result.get("items") or []
    page = result.get("page")
    lines = [f"tokens total={human_field_text(total, limit=80)} showing={len(items)} page={human_field_text(page, limit=80)}"]
    for item in items:
        quota = "unlimited" if item.get("unlimited_quota") else item.get("remain_quota")
        models = "all" if not item.get("model_limits_enabled") else item.get("model_limits")
        parts = [
            f"id={human_field_text(item.get('id'), limit=80)}",
            f"status={human_field_text(item.get('status'), limit=80)}",
            f"name={human_field_text(item.get('name'), limit=160)}",
            f"group={human_field_text(item.get('group'), limit=160)}",
            f"quota={human_field_text(quota, limit=160)}",
            f"models={human_field_text(models, limit=160)}",
        ]
        if verbose:
            for key in ("expired_time", "used_quota", "remain_quota", "accessed_time", "model_limits", "allow_ips"):
                value = item.get(key)
                if value is not None:
                    parts.append(f"{key}={human_field_text(value, limit=300)}")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def _human_logs_recent(total: Any, items: list[dict[str, Any]], page: int, verbose: bool) -> str:
    lines = [f"logs total={human_field_text(total, limit=80)} showing={len(items)} page={human_field_text(page, limit=80)}"]
    for item in items:
        parts = [
            f"id={human_field_text(item.get('id'), limit=80)}",
            f"type={human_field_text(item.get('type'), limit=80)}",
            f"model={human_field_text(item.get('model_name'), limit=160)}",
            f"channel={human_field_text(item.get('channel_name'), limit=160)}",
            f"token={human_field_text(item.get('token_name'), limit=160)}",
        ]
        if verbose:
            created = item.get("created_at")
            if created is not None:
                parts.append(f"created={human_field_text(created, limit=80)}")
            content = item.get("content")
            if content is not None:
                parts.append(f"content_chars={len(str(content))}")
            other = item.get("other")
            if other is not None:
                parts.append(f"other_chars={len(str(other))}")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def command_doctor(args: argparse.Namespace) -> int:
    status = requests.get(args.base_url.rstrip("/") + "/api/status", timeout=args.timeout).json()
    channels = api_request(args, "GET", "/api/channel/", params={"p": 1, "page_size": 1})
    payload = {
        "ok": True,
        "base_url": args.base_url,
        "service_success": status.get("success"),
        "version": status.get("data", {}).get("version"),
        "setup": status.get("data", {}).get("setup"),
        "admin_api": "raw Authorization access_token + New-Api-User",
        "user_id": str(args.user_id),
        "channels_total": channels.get("data", {}).get("total"),
    }
    emit(payload, args.json, _human_doctor(payload, is_verbose(args)))
    return 0


def command_channels_list(args: argparse.Namespace) -> int:
    data = api_request(
        args,
        "GET",
        "/api/channel/",
        params={
            "p": args.page,
            "page_size": args.page_size,
            "status": args.status,
            "type": args.type_filter,
            "group": args.group_filter,
            "id_sort": str(args.id_sort).lower(),
        },
    )
    items = data.get("data", {}).get("items", [])
    result = {
        "total": data.get("data", {}).get("total"),
        "items": [channel_summary(item) for item in items],
    }
    emit(result, args.json, _human_channels_list(result, args.page, is_verbose(args)))
    return 0


def command_channels_get(args: argparse.Namespace) -> int:
    data = api_request(args, "GET", f"/api/channel/{args.id}")
    channel = data.get("data") or {}
    emit(redacted_channel(channel), args.json)
    return 0


def command_channels_create(args: argparse.Namespace) -> int:
    key = read_secret_arg(args)
    payload = build_channel_create_payload(args, key)
    result: dict[str, Any] = {
        "dry_run": not effective_apply(args),
        "channel": preview_channel_create_payload(payload),
    }
    if effective_apply(args):
        result["api"] = redacted_tree(api_request(args, "POST", "/api/channel/", json_body=payload))
    emit(result, args.json)
    return 0


def command_channels_update(args: argparse.Namespace) -> int:
    data = api_request(args, "GET", f"/api/channel/{args.id}")
    before = data.get("data") or {}
    after: dict[str, Any] = {"id": args.id}
    fields: list[str] = []
    simple_updates = {
        "name": args.name,
        "models": args.models,
        "group": args.group,
        "tag": args.tag,
        "priority": args.priority,
        "weight": args.weight,
        "test_model": args.test_model,
        "model_mapping": args.model_mapping,
        "status_code_mapping": args.status_code_mapping,
        "remark": args.remark,
        "auto_ban": args.auto_ban,
        "other": args.other,
        "param_override": args.param_override,
        "header_override": args.header_override,
        "setting": args.setting,
    }
    for field, value in simple_updates.items():
        if value is None:
            continue
        after[field] = value
        fields.append(field)
    if args.type is not None:
        after["type"] = coerce_channel_type(args.type)
        fields.append("type")
    if args.base_url_value is not None:
        after["base_url"] = normalize_base_url(args.base_url_value, strip_v1=not args.keep_v1)
        fields.append("base_url")
    key = read_secret_arg(args, optional=True)
    if key:
        after["key"] = key
        fields.append("key")
    status = args.status
    if status is not None:
        fields.append("status")
    changes = change_summary(before, {**before, **after}, fields)
    result: dict[str, Any] = {
        "dry_run": not effective_apply(args),
        "id": args.id,
        "name": before.get("name"),
        "changes": changes,
    }
    if effective_apply(args):
        fields_for_put = {k: v for k, v in after.items() if k != "status"}
        result["api"] = redacted_tree(channel_patch(args, args.id, fields_for_put, status=status))
        result["after"] = redacted_channel(api_request(args, "GET", f"/api/channel/{args.id}").get("data") or {})
    emit(result, args.json)
    return 0


def channel_test_via_newapi(args: argparse.Namespace, channel_id: int, model: str, *, stream: bool = False) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if model:
        params["model"] = model
    if stream:
        params["stream"] = "true"
    try:
        data = api_request(args, "GET", f"/api/channel/test/{channel_id}", params=params or None)
        return {
            "via": "newapi",
            "ok": bool(data.get("success")),
            "message": data.get("message"),
            "time": data.get("time"),
            "stream": bool(stream),
        }
    except (CliError, requests.RequestException) as exc:
        return {
            "via": "newapi",
            "ok": False,
            "message": preview_text(str(exc), limit=240),
            "stream": bool(stream),
        }


def _resolve_test_models(channel: dict[str, Any], requested: str) -> list[str]:
    if requested == "*":
        models = split_list(channel.get("models"))
        return models or ([channel.get("test_model")] if channel.get("test_model") else [])
    if requested:
        return [requested]
    if channel.get("test_model"):
        return [str(channel.get("test_model"))]
    models = split_list(channel.get("models"))
    return [models[0]] if models else [""]


def command_channels_test(args: argparse.Namespace) -> int:
    channel_data = api_request(args, "GET", f"/api/channel/{args.id}").get("data") or {}
    models = _resolve_test_models(channel_data, args.model)
    via = args.via

    results: list[dict[str, Any]] = []
    for m in models:
        per_model: dict[str, Any] = {"model": m or None, "results": []}
        if via in ("newapi", "both"):
            per_model["results"].append(channel_test_via_newapi(args, args.id, m, stream=bool(args.stream)))
        if via in ("relay", "both"):
            if not m:
                per_model["results"].append({
                    "via": "relay",
                    "ok": False,
                    "error": "no model resolved; pass --model or set channel test_model",
                })
            else:
                per_model["results"].append(sse_probe_via_relay(args, model=m))
        per_model["ok"] = any(r.get("ok") for r in per_model["results"])
        results.append(per_model)

    overall_ok = all(r["ok"] for r in results) if results else False
    emit(
        {
            "id": args.id,
            "channel_name": channel_data.get("name"),
            "via": via,
            "ok": overall_ok,
            "tested": results,
        },
        args.json,
    )
    return 0 if overall_ok else 1


def dict_from_json_field(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        parsed = parse_json_maybe(value)
        return parsed if isinstance(parsed, dict) else {}
    return {}


def channel_probe_model(channel: dict[str, Any]) -> str:
    other_info = dict_from_json_field(channel.get("other_info"))
    hold = dict_from_json_field(other_info.get("relay_gate_quota_hold"))
    evidence = dict_from_json_field(hold.get("evidence"))
    evidence_model = str(evidence.get("model") or "").strip()
    if evidence_model:
        return evidence_model

    models = split_list(channel.get("models"))
    test_model = str(channel.get("test_model") or "").strip()
    if test_model and test_model not in AUTO_PROBE_MODELS:
        return test_model
    for model in models:
        if model not in AUTO_PROBE_MODELS:
            return model
    if test_model:
        return test_model
    return models[0] if models else ""


def stream_probe_channel(args: argparse.Namespace, channel: dict[str, Any], *, model: str = "") -> dict[str, Any]:
    channel_id = int(channel.get("id") or 0)
    probe_model = model.strip() if model else channel_probe_model(channel)
    if not channel_id or not probe_model:
        return {"ok": False, "message": "no channel id or probe model", "model": probe_model or None, "stream": True}
    return channel_test_via_newapi(args, channel_id, probe_model, stream=True)


def is_quota_exhausted_message(message: str) -> bool:
    text = str(message or "")
    if "\\u" in text or "\\x" in text:
        try:
            text = bytes(text, "utf-8").decode("unicode_escape")
        except UnicodeDecodeError:
            pass
    lower = text.lower()
    return (
        "额度已用尽" in text
        or "余额不足" in text
        or "quota exceeded" in lower
        or "insufficient quota" in lower
        or "insufficient balance" in lower
    )


def build_quota_hold_evidence(source: str, *, message: str, model: str | None = None, log_id: Any = None) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "source": source,
        "message": preview_text(message, limit=240),
    }
    if model:
        evidence["model"] = model
    if log_id is not None:
        evidence["log_id"] = log_id
    return evidence


def set_channel_quota_hold(
    args: argparse.Namespace,
    channel: dict[str, Any],
    *,
    evidence: dict[str, Any],
    apply: bool,
) -> dict[str, Any]:
    before = dict(channel)
    after = dict(channel)
    other_info = dict_from_json_field(after.get("other_info"))
    hold = dict_from_json_field(other_info.get("relay_gate_quota_hold"))
    already_held = int(after.get("status") or 0) == CHANNEL_STATUS_MANUAL_DISABLED and hold.get("reason") == QUOTA_HOLD_REASON
    other_info["relay_gate_quota_hold"] = {
        "reason": QUOTA_HOLD_REASON,
        "held_at": int(time.time()),
        "evidence": evidence,
    }
    other_info["status_reason"] = "quota exhausted; held by relay-gate until stream channel-test succeeds"
    other_info["status_time"] = int(time.time())
    after["other_info"] = json.dumps(other_info, ensure_ascii=False, separators=(",", ":"))
    after["status"] = CHANNEL_STATUS_MANUAL_DISABLED
    changes = change_summary(before, after, ["status", "other_info"])
    result = {
        "id": channel.get("id"),
        "name": channel.get("name"),
        "already_held": already_held,
        "evidence": evidence,
        "changes": changes,
        "applied": False,
    }
    if apply and (changes or not already_held):
        cid = int(channel.get("id") or 0)
        response = channel_patch(args, cid, {"other_info": after["other_info"]}, status=CHANNEL_STATUS_MANUAL_DISABLED)
        result["applied"] = bool(response.get("success"))
        result["message"] = response.get("message") or ""
    return result


def recent_quota_evidence_by_channel(args: argparse.Namespace) -> dict[int, dict[str, Any]]:
    evidence: dict[int, dict[str, Any]] = {}
    logs = fetch_log_items(args, page_size=int(getattr(args, "log_page_size", 100) or 100))
    for item in logs:
        channel_id = int(item.get("channel") or 0)
        if not channel_id:
            continue
        channel_name = str(item.get("channel_name") or "")
        model_name = str(item.get("model_name") or "")
        if not channel_name.startswith("buddy-") and not model_name.startswith("buddy-"):
            continue
        content = str(item.get("content") or item.get("content_preview") or "")
        if not is_quota_exhausted_message(content):
            continue
        evidence[channel_id] = build_quota_hold_evidence("recent_log", message=content, model=model_name or None, log_id=item.get("id"))
    return evidence


def _coerce_int_list(values: Any) -> list[int]:
    items: list[int] = []
    for value in values or []:
        try:
            items.append(int(str(value).strip()))
        except (TypeError, ValueError):
            continue
    return items


def _coerce_str_list(values: Any) -> list[str]:
    items: list[str] = []
    for value in values or []:
        text = str(value).strip()
        if text:
            items.append(text)
    return items


def _append_unique(existing: list[Any], additions: list[Any]) -> tuple[list[Any], list[Any]]:
    seen = set(existing)
    merged = list(existing)
    added: list[Any] = []
    for item in additions:
        if item in seen:
            continue
        seen.add(item)
        merged.append(item)
        added.append(item)
    return merged, added


def merge_responses_bridge_policy(
    current: Any,
    *,
    channel_ids: list[int] | None = None,
    model_patterns: list[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    policy = dict(current) if isinstance(current, dict) else {}
    changes: list[dict[str, Any]] = []

    if policy.get("enabled") is not True:
        policy["enabled"] = True
        changes.append({"field": "enabled", "after": True})
    if "all_channels" not in policy:
        policy["all_channels"] = False
        changes.append({"field": "all_channels", "after": False})

    existing_ids = _coerce_int_list(policy.get("channel_ids"))
    merged_ids, added_ids = _append_unique(existing_ids, _coerce_int_list(channel_ids))
    policy["channel_ids"] = merged_ids
    if added_ids:
        changes.append({"field": "channel_ids", "added": added_ids})

    existing_patterns = _coerce_str_list(policy.get("model_patterns"))
    merged_patterns, added_patterns = _append_unique(existing_patterns, _coerce_str_list(model_patterns))
    policy["model_patterns"] = merged_patterns
    if added_patterns:
        changes.append({"field": "model_patterns", "added": added_patterns})

    if "channel_types" in policy and policy["channel_types"] is not None:
        policy["channel_types"] = _coerce_int_list(policy.get("channel_types"))

    return policy, changes


def _load_responses_bridge_policy(args: argparse.Namespace) -> tuple[dict[str, Any], str]:
    data = api_request(args, "GET", "/api/option/")
    for item in data.get("data") or []:
        if item.get("key") != RESPONSES_BRIDGE_OPTION_KEY:
            continue
        raw = str(item.get("value") or "").strip()
        parsed = parse_json_maybe(raw) if raw else {}
        return parsed if isinstance(parsed, dict) else {}, raw
    return {}, ""


def command_responses_bridge_get(args: argparse.Namespace) -> int:
    policy, raw = _load_responses_bridge_policy(args)
    emit(
        {
            "key": RESPONSES_BRIDGE_OPTION_KEY,
            "value": policy,
            "raw": raw,
        },
        args.json,
    )
    return 0


def command_responses_bridge_ensure(args: argparse.Namespace) -> int:
    before, _ = _load_responses_bridge_policy(args)
    after, changes = merge_responses_bridge_policy(
        before,
        channel_ids=args.channel_id,
        model_patterns=args.model_pattern,
    )
    payload = {
        "key": RESPONSES_BRIDGE_OPTION_KEY,
        "value": json.dumps(after, ensure_ascii=False, separators=(",", ":")),
    }
    result: dict[str, Any] = {
        "dry_run": not effective_apply(args),
        "key": RESPONSES_BRIDGE_OPTION_KEY,
        "changes": changes,
        "before": before,
        "after": after,
    }
    if effective_apply(args):
        result["api"] = redacted_tree(api_request(args, "PUT", "/api/option/", json_body=payload))
        result["stored"] = True
    emit(result, args.json)
    return 0


# ---------------------------------------------------------------------------
# Groups management (karma #337)
# ---------------------------------------------------------------------------

GROUP_RATIO_SETTING_KEY = "group_ratio_setting"
GROUP_RATIO_KEY = "GroupRatio"
USER_USABLE_GROUPS_KEY = "UserUsableGroups"


def _load_option_entry(args, key):
    """Return (parsed_value, raw_string) for an option key; ({}, "") if absent."""
    data = api_request(args, "GET", "/api/option/")
    for item in data.get("data") or []:
        if item.get("key") != key:
            continue
        raw = str(item.get("value") or "").strip()
        parsed = parse_json_maybe(raw) if raw else {}
        return parsed if isinstance(parsed, (dict, list)) else {}, raw
    return {}, ""


def _load_group_state(args):
    """Read the three NewAPI group-related options in one pass."""
    data = api_request(args, "GET", "/api/option/")
    options = {}
    for item in (data.get("data") or []):
        k = item.get("key")
        if k:
            options[k] = str(item.get("value") or "").strip()

    grs_raw = options.get(GROUP_RATIO_SETTING_KEY, "")
    grs = parse_json_maybe(grs_raw) if grs_raw else {}
    if not isinstance(grs, dict):
        grs = {}

    gr_raw = options.get(GROUP_RATIO_KEY, "")
    gr = parse_json_maybe(gr_raw) if gr_raw else {}
    if not isinstance(gr, dict):
        gr = {}

    uug_raw = options.get(USER_USABLE_GROUPS_KEY, "")
    uug = parse_json_maybe(uug_raw) if uug_raw else []
    if not isinstance(uug, list):
        uug = []

    return {
        "group_ratio_setting": {"raw": grs_raw, "value": grs},
        "group_ratio": {"raw": gr_raw, "value": gr},
        "user_usable_groups": {"raw": uug_raw, "value": uug},
    }


def command_groups_list(args):
    state = _load_group_state(args)
    grs = state["group_ratio_setting"]["value"]
    gr = state["group_ratio"]["value"]
    uug = state["user_usable_groups"]["value"]

    grs_ratio = grs.get("group_ratio") if isinstance(grs, dict) else None
    if not isinstance(grs_ratio, dict):
        grs_ratio = {}

    all_names = sorted(set(list(grs_ratio.keys()) + list(gr.keys()) + list(uug)))
    rows = []
    for name in all_names:
        rows.append({
            "name": name,
            "group_ratio_setting_ratio": grs_ratio.get(name),
            "group_ratio": gr.get(name),
            "user_usable": name in uug,
        })

    result = {
        "groups": rows,
    }
    emit(result, args.json, _human_groups_list(result))
    return 0


def _human_groups_list(result):
    lines = []
    for row in result.get("groups") or []:
        name = row["name"]
        usable = "usable" if row["user_usable"] else "not-usable"
        grs_r = row.get("group_ratio_setting_ratio")
        gr_r = row.get("group_ratio")
        lines.append(f"  {name}  ratio_setting={grs_r}  GroupRatio={gr_r}  {usable}")
    if not lines:
        return "groups: (none found)"
    return "groups:\n" + "\n".join(lines)


def _compute_group_ensure_plan(state, group_name, ratio):
    """Compute before/after for the three options. Returns plan with changes list."""
    grs = state["group_ratio_setting"]["value"]
    grs_after = dict(grs) if isinstance(grs, dict) else {}
    grs_ratio_before = dict(grs.get("group_ratio") or {}) if isinstance(grs, dict) else {}
    if not isinstance(grs_ratio_before, dict):
        grs_ratio_before = {}
    grs_ratio_after = dict(grs_ratio_before)
    grs_ratio_after[group_name] = ratio
    grs_after["group_ratio"] = grs_ratio_after

    gr = state["group_ratio"]["value"]
    gr_after = dict(gr) if isinstance(gr, dict) else {}
    gr_before_val = gr_after.get(group_name)
    gr_after[group_name] = ratio

    uug = state["user_usable_groups"]["value"]
    uug_after = list(uug) if isinstance(uug, list) else []
    uug_before_present = group_name in uug_after
    if not uug_before_present:
        uug_after.append(group_name)

    changes = []
    if grs_ratio_before.get(group_name) != ratio:
        changes.append({
            "option": GROUP_RATIO_SETTING_KEY,
            "field": f"group_ratio.{group_name}",
            "before": grs_ratio_before.get(group_name),
            "after": ratio,
        })
    if gr_before_val != ratio:
        changes.append({
            "option": GROUP_RATIO_KEY,
            "field": group_name,
            "before": gr_before_val,
            "after": ratio,
        })
    if not uug_before_present:
        changes.append({
            "option": USER_USABLE_GROUPS_KEY,
            "field": "list",
            "before": uug_before_present,
            "after": True,
        })

    return {
        "changes": changes,
        "before": {
            GROUP_RATIO_SETTING_KEY: grs,
            GROUP_RATIO_KEY: gr,
            USER_USABLE_GROUPS_KEY: uug,
        },
        "after": {
            GROUP_RATIO_SETTING_KEY: grs_after,
            GROUP_RATIO_KEY: gr_after,
            USER_USABLE_GROUPS_KEY: uug_after,
        },
    }


def _put_option(args, key, value):
    payload = {
        "key": key,
        "value": json.dumps(value, ensure_ascii=False, separators=(",", ":")),
    }
    return redacted_tree(api_request(args, "PUT", "/api/option/", json_body=payload))


def command_groups_ensure(args):
    group_name = args.name.strip()
    if not group_name:
        raise CliError("--name is required and must not be empty.")
    ratio = float(args.ratio)

    state = _load_group_state(args)
    plan = _compute_group_ensure_plan(state, group_name, ratio)

    result = {
        "dry_run": not effective_apply(args),
        "group": group_name,
        "ratio": ratio,
        "changes": plan["changes"],
        "before": plan["before"],
        "after": plan["after"],
    }

    if not plan["changes"]:
        result["already_in_sync"] = True
        result["verification"] = "All three options already contain this group with the same ratio."
        emit(result, args.json)
        return 0

    if effective_apply(args):
        writes = []
        try:
            for key in (GROUP_RATIO_SETTING_KEY, GROUP_RATIO_KEY, USER_USABLE_GROUPS_KEY):
                after_val = plan["after"][key]
                api_result = _put_option(args, key, after_val)
                writes.append({"option": key, "ok": True, "api": api_result})
        except (CliError, requests.RequestException) as exc:
            writes.append({"option": "partial_failure", "ok": False, "error": str(exc)})
            result["writes"] = writes
            result["rollback_attempted"] = True
            for key in (GROUP_RATIO_SETTING_KEY, GROUP_RATIO_KEY, USER_USABLE_GROUPS_KEY):
                try:
                    _put_option(args, key, plan["before"][key])
                except Exception:
                    pass
            result["rollback"] = "Attempted to restore previous values. Verify with: relay-gate --json groups list"
            emit(result, args.json)
            return 2

        result["writes"] = writes

        verify_state = _load_group_state(args)
        grs_ok = (
            verify_state["group_ratio_setting"]["value"]
            .get("group_ratio", {})
            .get(group_name) == ratio
        )
        gr_ok = verify_state["group_ratio"]["value"].get(group_name) == ratio
        uug_ok = group_name in verify_state["user_usable_groups"]["value"]
        result["verification"] = {
            "group_ratio_setting": grs_ok,
            "group_ratio": gr_ok,
            "user_usable_groups": uug_ok,
            "all_ok": grs_ok and gr_ok and uug_ok,
        }
        if not (grs_ok and gr_ok and uug_ok):
            result["verification"]["warning"] = (
                "One or more options did not verify after apply. "
                "Check with: relay-gate --json groups list"
            )

    emit(result, args.json)
    return 0


def hold_quota_channels(args: argparse.Namespace) -> dict[str, Any]:
    channels = fetch_existing_channels(args)
    channel_by_id = {int(ch.get("id") or 0): ch for ch in channels}
    target_ids = set(int(i) for i in getattr(args, "channel_id", []) or [])
    log_evidence = recent_quota_evidence_by_channel(args) if getattr(args, "from_logs", False) else {}
    if getattr(args, "from_logs", False):
        target_ids.update(log_evidence.keys())
    results: list[dict[str, Any]] = []
    for channel_id in sorted(target_ids):
        channel = channel_by_id.get(channel_id)
        if not channel:
            results.append({"id": channel_id, "ok": False, "reason": "channel_not_found", "applied": False})
            continue
        full = api_request(args, "GET", f"/api/channel/{channel_id}").get("data") or {}
        evidence = log_evidence.get(channel_id)
        probe = None
        if evidence is None:
            probe = stream_probe_channel(args, full, model=getattr(args, "model", "") or "")
            message = str(probe.get("message") or probe.get("error") or "")
            if is_quota_exhausted_message(message):
                evidence = build_quota_hold_evidence("stream_probe", message=message, model=probe.get("model") or channel_probe_model(full))
        if evidence is None:
            results.append({
                "id": channel_id,
                "name": full.get("name"),
                "ok": False,
                "reason": "quota_not_confirmed",
                "probe": probe,
                "applied": False,
            })
            continue
        item = set_channel_quota_hold(args, full, evidence=evidence, apply=effective_apply(args))
        item["ok"] = True
        if probe is not None:
            item["probe"] = probe
        results.append(item)
    return {"dry_run": not effective_apply(args), "results": results}


def recover_channels(args: argparse.Namespace) -> dict[str, Any]:
    channels = fetch_existing_channels(args)
    target_ids = set(int(i) for i in getattr(args, "channel_id", []) or [])
    if not target_ids:
        for channel in channels:
            status = int(channel.get("status") or 0)
            if status in {CHANNEL_STATUS_AUTO_DISABLED, CHANNEL_STATUS_MANUAL_DISABLED}:
                target_ids.add(int(channel.get("id") or 0))
    channel_by_id = {int(ch.get("id") or 0): ch for ch in channels}
    results: list[dict[str, Any]] = []
    for channel_id in sorted(i for i in target_ids if i):
        channel = channel_by_id.get(channel_id)
        if not channel:
            results.append({"id": channel_id, "ok": False, "reason": "channel_not_found", "applied": False})
            continue
        full = api_request(args, "GET", f"/api/channel/{channel_id}").get("data") or {}
        status = int(full.get("status") or 0)
        other_info = dict_from_json_field(full.get("other_info"))
        hold = dict_from_json_field(other_info.get("relay_gate_quota_hold"))
        is_quota_hold = status == CHANNEL_STATUS_MANUAL_DISABLED and hold.get("reason") == QUOTA_HOLD_REASON
        if status == CHANNEL_STATUS_ENABLED:
            results.append({"id": channel_id, "name": full.get("name"), "ok": True, "reason": "already_enabled", "applied": False})
            continue
        if status == CHANNEL_STATUS_MANUAL_DISABLED and not is_quota_hold:
            results.append({"id": channel_id, "name": full.get("name"), "ok": False, "reason": "manual_disabled_not_quota_hold", "applied": False})
            continue
        probe = stream_probe_channel(args, full, model=getattr(args, "model", "") or "")
        if not probe.get("ok"):
            results.append({
                "id": channel_id,
                "name": full.get("name"),
                "ok": False,
                "reason": "probe_failed",
                "probe": probe,
                "applied": False,
            })
            continue
        before = dict(full)
        other_info.pop("relay_gate_quota_hold", None)
        other_info["status_reason"] = "recovered by relay-gate stream channel-test"
        other_info["status_time"] = int(time.time())
        full["other_info"] = json.dumps(other_info, ensure_ascii=False, separators=(",", ":"))
        full["status"] = CHANNEL_STATUS_ENABLED
        changes = change_summary(before, full, ["status", "other_info"])
        item = {
            "id": channel_id,
            "name": full.get("name"),
            "ok": True,
            "reason": "probe_passed_recovered",
            "probe": probe,
            "changes": changes,
            "applied": False,
        }
        if effective_apply(args):
            response = channel_patch(args, channel_id, {"other_info": full["other_info"]}, status=CHANNEL_STATUS_ENABLED)
            item["applied"] = bool(response.get("success"))
            item["message"] = response.get("message") or ""
        results.append(item)
    return {"dry_run": not effective_apply(args), "results": results}


def command_channels_hold_quota(args: argparse.Namespace) -> int:
    result = hold_quota_channels(args)
    emit(result, args.json)
    return 0 if all(item.get("ok") for item in result["results"]) else 1


def command_channels_recover(args: argparse.Namespace) -> int:
    result = recover_channels(args)
    emit(result, args.json)
    return 0 if all(item.get("ok") for item in result["results"]) else 1


def parse_model_mapping_value(value: Any) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list)):
        return value
    text = str(value).strip()
    if text.lower() == "null":
        return None
    if text.startswith("{") or text.startswith("["):
        return json.loads(text)
    mapping: dict[str, str] = {}
    for item in split_list(text):
        if "=" not in item:
            raise CliError(f"model mapping item must be alias=actual: {item}")
        alias, actual = item.split("=", 1)
        alias = alias.strip()
        actual = actual.strip()
        if not alias or not actual:
            raise CliError(f"model mapping item must be alias=actual: {item}")
        mapping[alias] = actual
    return mapping


def channel_model_summary(channel: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": channel.get("id"),
        "name": channel.get("name"),
        "status": channel.get("status"),
        "tag": channel.get("tag"),
        "models": split_list(channel.get("models")),
        "test_model": channel.get("test_model"),
        "model_mapping": parse_json_maybe(str(channel.get("model_mapping") or "")) if channel.get("model_mapping") else None,
    }


def command_channel_models_list(args: argparse.Namespace) -> int:
    channels = fetch_existing_channels(args)
    items = []
    for channel in channels:
        if args.channel_id and int(channel.get("id") or 0) not in set(args.channel_id):
            continue
        if args.tag and str(channel.get("tag") or "") not in set(args.tag):
            continue
        if not args.include_disabled and int(channel.get("status") or 0) != 1:
            continue
        items.append(channel_model_summary(channel))
    emit({"total": len(items), "items": items}, args.json)
    return 0


def command_channel_models_set(args: argparse.Namespace) -> int:
    data = api_request(args, "GET", f"/api/channel/{args.id}")
    before = data.get("data") or {}
    after = dict(before)
    fields: list[str] = []
    if args.models is not None:
        models = ",".join(split_list(args.models))
        if not models:
            raise CliError("provide at least one model")
        after["models"] = models
        fields.append("models")
        if args.test_model is None and not after.get("test_model"):
            after["test_model"] = split_list(models)[0]
            fields.append("test_model")
    if args.test_model is not None:
        after["test_model"] = args.test_model
        fields.append("test_model")
    if args.model_mapping is not None:
        mapping = parse_model_mapping_value(args.model_mapping)
        after["model_mapping"] = json.dumps(mapping, ensure_ascii=False, separators=(",", ":")) if mapping else ""
        fields.append("model_mapping")
    changes = change_summary(before, after, fields)
    result: dict[str, Any] = {
        "dry_run": not effective_apply(args),
        "id": args.id,
        "name": before.get("name"),
        "changes": changes,
    }
    if effective_apply(args):
        # NewAPI UpdateChannel rejects PUT bodies that include status.
        fields_for_put = {k: v for k, v in after.items() if k != "status"}
        result["api"] = redacted_tree(channel_patch(args, args.id, fields_for_put))
        result["after"] = channel_model_summary(api_request(args, "GET", f"/api/channel/{args.id}").get("data") or {})
    emit(result, args.json)
    return 0


def find_token_by_name(args: argparse.Namespace, name: str) -> dict[str, Any] | None:
    data = api_request(args, "GET", "/api/token/search", params={"keyword": name, "p": 1, "size": 100})
    for item in data.get("data", {}).get("items", []):
        if item.get("name") == name:
            return item
    return None


def build_token_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "name": args.name,
        "expired_time": int(args.expired_time),
        "remain_quota": int(args.remain_quota),
        "unlimited_quota": bool(args.unlimited),
        "model_limits_enabled": bool(args.model_limits_enabled),
        "model_limits": args.model_limits or "",
        "allow_ips": args.allow_ips or "",
        "group": args.group,
        "cross_group_retry": bool(args.cross_group_retry),
    }


def command_tokens_list(args: argparse.Namespace) -> int:
    data = api_request(
        args,
        "GET",
        "/api/token/search",
        params={"keyword": args.keyword, "p": args.page, "size": args.page_size},
    )
    payload = data.get("data", {}) if isinstance(data, dict) else {}
    items = payload.get("items") or []
    result = {
        "total": payload.get("total"),
        "page": payload.get("page"),
        "page_size": payload.get("page_size"),
        "items": [token_summary(item) for item in items],
    }
    emit(result, args.json, _human_tokens_list(result, is_verbose(args)))
    return 0


def command_tokens_get(args: argparse.Namespace) -> int:
    data = api_request(args, "GET", f"/api/token/{args.id}")
    emit(redacted_token(data.get("data") or {}), args.json)
    return 0


def command_tokens_create(args: argparse.Namespace) -> int:
    payload = build_token_payload(args)
    result: dict[str, Any] = {
        "dry_run": not effective_apply(args),
        "token": token_summary(payload),
        "store_cred": args.store_cred or None,
    }
    if effective_apply(args):
        result["api"] = redacted_tree(api_request(args, "POST", "/api/token/", json_body=payload))
        token = find_token_by_name(args, args.name)
        if token:
            result["token"] = token_summary(token)
            if args.store_cred:
                key_resp = api_request(args, "POST", f"/api/token/{token['id']}/key")
                key = key_resp.get("data", {}).get("key")
                if not key:
                    raise CliError("NewAPI did not return token key")
                store_credential(
                    args.store_cred,
                    key,
                    kind="token",
                    note=(
                        f"NewAPI API key for {args.base_url}/v1; token name {args.name}; "
                        "generated/stored by relay-gate; do not reveal/log."
                    ),
                )
                result["stored"] = args.store_cred
                result["key"] = redact_secret_field(key)
    emit(result, args.json)
    return 0


def command_tokens_update(args: argparse.Namespace) -> int:
    data = api_request(args, "GET", f"/api/token/{args.id}")
    before = data.get("data") or {}
    after = dict(before)
    fields: list[str] = []
    updates = {
        "name": args.name,
        "status": args.status,
        "expired_time": args.expired_time,
        "remain_quota": args.remain_quota,
        "unlimited_quota": args.unlimited,
        "model_limits_enabled": args.model_limits_enabled,
        "model_limits": args.model_limits,
        "allow_ips": args.allow_ips,
        "group": args.group,
        "cross_group_retry": args.cross_group_retry,
    }
    for field, value in updates.items():
        if value is None:
            continue
        after[field] = value
        fields.append(field)
    changes = change_summary(before, after, fields)
    result: dict[str, Any] = {
        "dry_run": not effective_apply(args),
        "id": args.id,
        "name": before.get("name"),
        "changes": changes,
    }
    if effective_apply(args):
        result["api"] = redacted_tree(api_request(args, "PUT", "/api/token/", json_body=after))
        result["after"] = redacted_token(api_request(args, "GET", f"/api/token/{args.id}").get("data") or {})
    emit(result, args.json)
    return 0


def command_tokens_key(args: argparse.Namespace) -> int:
    key_resp = api_request(args, "POST", f"/api/token/{args.id}/key")
    key = key_resp.get("data", {}).get("key")
    if not key:
        raise CliError("NewAPI did not return token key")
    result: dict[str, Any] = {
        "id": args.id,
        "key": redact_secret_field(key),
        "stored": None,
    }
    if args.store_cred:
        store_credential(
            args.store_cred,
            key,
            kind="token",
            note=(
                f"NewAPI API key for {args.base_url}/v1; token id {args.id}; "
                "stored by relay-gate; do not reveal/log."
            ),
        )
        result["stored"] = args.store_cred
    emit(result, args.json)
    return 0


def command_tokens_ensure_self(args: argparse.Namespace) -> int:
    token = find_token_by_name(args, args.name)
    created = False
    if token is None:
        payload = {
            "name": args.name,
            "expired_time": -1,
            "remain_quota": int(args.remain_quota),
            "unlimited_quota": args.unlimited,
            "model_limits_enabled": False,
            "model_limits": "",
            "allow_ips": args.allow_ips or "",
            "group": args.group,
            "cross_group_retry": args.cross_group_retry,
        }
        api_request(args, "POST", "/api/token/", json_body=payload)
        token = find_token_by_name(args, args.name)
        created = True
    if token is None:
        raise CliError("token was created but could not be found")
    key_resp = api_request(args, "POST", f"/api/token/{token['id']}/key")
    key = key_resp.get("data", {}).get("key")
    if not key:
        raise CliError("NewAPI did not return token key")
    if args.store:
        store_credential(
            args.cred_name,
            key,
            kind="token",
            note=(
                f"NewAPI self-use API key for {args.base_url}/v1; token name {args.name}; "
                "generated/stored by relay-gate; do not reveal/log."
            ),
        )
    emit(
        {
            "ok": True,
            "created": created,
            "token_id": token.get("id"),
            "name": args.name,
            "base_url": args.base_url.rstrip("/") + "/v1",
            "key": redact(key),
            "stored": args.cred_name if args.store else None,
        },
        args.json,
    )
    return 0


def parse_json_maybe(value: str) -> Any:
    try:
        return json.loads(value)
    except ValueError:
        return value


def summarize_log_item(item: dict[str, Any], *, include_other: bool = False) -> dict[str, Any]:
    result = {
        "id": item.get("id"),
        "created_at": item.get("created_at"),
        "type": item.get("type"),
        "username": item.get("username"),
        "token_name": item.get("token_name"),
        "model_name": item.get("model_name"),
        "quota": item.get("quota"),
        "prompt_tokens": item.get("prompt_tokens"),
        "completion_tokens": item.get("completion_tokens"),
        "use_time": item.get("use_time"),
        "is_stream": item.get("is_stream"),
        "channel": item.get("channel"),
        "channel_name": item.get("channel_name"),
        "token_id": item.get("token_id"),
        "group": item.get("group"),
        "ip": item.get("ip"),
        "content_preview": preview_text(item.get("content"), limit=300),
    }
    other = str(item.get("other") or "")
    if include_other:
        result["other"] = parse_json_maybe(other) if other else None
    else:
        result["other_preview"] = preview_text(other, limit=300)
    return result


def command_logs_recent(args: argparse.Namespace) -> int:
    path = "/api/log/self" if args.self else "/api/log/"
    data = api_request(args, "GET", path, params={"p": args.page, "page_size": args.page_size})
    payload = data.get("data", {}) if isinstance(data, dict) else {}
    items = payload.get("items") or []
    result = {
        "total": payload.get("total"),
        "page": payload.get("page"),
        "page_size": payload.get("page_size"),
        "items": [summarize_log_item(item, include_other=args.include_other) for item in items],
    }
    emit(
        result,
        args.json,
        _human_logs_recent(payload.get("total"), items, args.page, is_verbose(args)),
    )
    return 0


def command_logs_stats(args: argparse.Namespace) -> int:
    data = api_request(args, "GET", "/api/log/stat")
    emit(data.get("data") if isinstance(data, dict) else data, args.json)
    return 0


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def model_set(value: Any) -> set[str]:
    return set(split_list(value))


def channel_matches_optimize_scope(channel: dict[str, Any], args: argparse.Namespace) -> tuple[bool, str]:
    if args.channel_id and int(channel.get("id") or 0) not in set(args.channel_id):
        return False, "channel_id"
    if not args.include_disabled and int(channel.get("status") or 0) != 1:
        return False, "disabled"
    if not args.all_tags and args.tag:
        tags = set(args.tag)
        if str(channel.get("tag") or "") not in tags:
            return False, "tag"
    requested_models = set(args.model)
    supported = model_set(channel.get("models"))
    if requested_models and supported and requested_models.isdisjoint(supported):
        return False, "model"
    return True, "ok"


def probe_model_for_channel(channel: dict[str, Any], models: list[str]) -> str:
    if models:
        return models[0]
    supported = split_list(channel.get("models"))
    return supported[0] if supported else DEFAULT_MODELS


def optimize_models_for_channels(channels: list[dict[str, Any]], requested_models: list[str]) -> list[str]:
    if requested_models:
        return list(dict.fromkeys(requested_models))
    models: list[str] = []
    seen: set[str] = set()
    for channel in channels:
        for model in split_list(channel.get("models")):
            if model and model not in seen:
                seen.add(model)
                models.append(model)
    return models


def channel_supports_model(channel: dict[str, Any], model: str) -> bool:
    supported = model_set(channel.get("models"))
    return not supported or model in supported


def fetch_log_items(args: argparse.Namespace, *, page_size: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    pages = int(getattr(args, "log_pages", 1) or 1)
    for page in range(1, max(1, pages) + 1):
        data = api_request(args, "GET", "/api/log/", params={"p": page, "page_size": page_size})
        payload = data.get("data", {}) if isinstance(data, dict) else {}
        page_items = list(payload.get("items") or [])
        if not page_items:
            break
        items.extend(page_items)
    return items


def log_other(item: dict[str, Any]) -> dict[str, Any]:
    other = item.get("other")
    if isinstance(other, dict):
        return other
    if isinstance(other, str) and other.strip():
        parsed = parse_json_maybe(other)
        return parsed if isinstance(parsed, dict) else {}
    return {}


def build_channel_log_stats(
    logs: list[dict[str, Any]],
    models: list[str],
    *,
    include_admin_tests: bool = False,
) -> dict[int, dict[str, Any]]:
    wanted = set(models)
    stats: dict[int, dict[str, Any]] = {}
    latest_seen = 0
    for item in logs:
        if not include_admin_tests and int(item.get("token_id") or 0) == 0:
            continue
        created_at = int(item.get("created_at") or 0)
        latest_seen = max(latest_seen, created_at)
        channel_id = int(item.get("channel") or 0)
        if not channel_id:
            channel_id = int(log_other(item).get("channel_id") or 0)
        if not channel_id:
            continue
        model = str(item.get("model_name") or "")
        if wanted and model and model not in wanted:
            continue
        bucket = stats.setdefault(
            channel_id,
            {
                "success": 0,
                "failure": 0,
                "use_times": [],
                "quota": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "status_codes": {},
                "budget_errors": 0,
                "responses_success": 0,
                "responses_conversion_failures": 0,
                "last_error": "",
                "last_outcome": "",
                "last_created_at": 0,
            },
        )
        if not bucket["last_created_at"]:
            bucket["last_created_at"] = created_at
        log_type = int(item.get("type") or 0)
        if log_type == 2:
            if not bucket["last_outcome"]:
                bucket["last_outcome"] = "success"
            bucket["success"] += 1
            other = log_other(item)
            if other.get("request_path") == "/v1/responses":
                bucket["responses_success"] += 1
            use_time = item.get("use_time")
            if isinstance(use_time, (int, float)) and use_time > 0:
                bucket["use_times"].append(float(use_time))
            bucket["quota"] += int(item.get("quota") or 0)
            bucket["prompt_tokens"] += int(item.get("prompt_tokens") or 0)
            bucket["completion_tokens"] += int(item.get("completion_tokens") or 0)
        elif log_type == 5:
            if not bucket["last_outcome"]:
                bucket["last_outcome"] = "failure"
            bucket["failure"] += 1
            content = str(item.get("content") or "")
            bucket["last_error"] = content[:180]
            other = log_other(item)
            if other.get("request_path") == "/v1/responses" and other.get("error_code") == "convert_request_failed":
                bucket["responses_conversion_failures"] += 1
                bucket["last_outcome"] = "responses_protocol_error"
            status_code = other.get("status_code")
            if status_code is not None:
                key = str(status_code)
                bucket["status_codes"][key] = int(bucket["status_codes"].get(key, 0)) + 1
                if key in {"402", "403"}:
                    bucket["budget_errors"] += 1
                    if bucket["last_outcome"] == "failure":
                        bucket["last_outcome"] = "budget_error"
            if re.search(r"(?i)(insufficient|balance|quota|billing|余额|额度)", content):
                bucket["budget_errors"] += 1
                if bucket["last_outcome"] == "failure":
                    bucket["last_outcome"] = "budget_error"
    for bucket in stats.values():
        total = int(bucket["success"]) + int(bucket["failure"])
        bucket["total"] = total
        bucket["success_rate"] = (float(bucket["success"]) / total) if total else None
        times = bucket["use_times"]
        bucket["avg_use_time"] = (sum(times) / len(times)) if times else None
        bucket["age_seconds"] = max(0, latest_seen - int(bucket.get("last_created_at") or 0)) if latest_seen else None
    return stats


def _channel_protocol_hint(channel: dict[str, Any]) -> str:
    other = parse_json_maybe(str(channel.get("other_info") or ""))
    if isinstance(other, dict):
        relay_gate = other.get("relay_gate")
        if isinstance(relay_gate, dict):
            hint = relay_gate.get("protocol_hint")
            if isinstance(hint, str) and hint:
                return hint.strip().lower()
    return ""


def probe_channel(args: argparse.Namespace, channel: dict[str, Any], model: str) -> dict[str, Any] | None:
    if not args.probe:
        return None
    channel_id = int(channel.get("id") or 0)
    hint = _channel_protocol_hint(channel)
    # SSE-only upstreams (e.g. type=8 passthrough channels like buddy-chicross)
    # always fail NewAPI's internal /api/channel/test because it expects a JSON
    # body. Skip the probe instead of penalizing them. Logs-based scoring still
    # reflects real success/failure.
    if hint == "sse-only":
        return {
            "success": None,
            "time": None,
            "message": "skipped: protocol_hint=sse-only",
            "skipped": True,
        }
    try:
        data = api_request(args, "GET", f"/api/channel/test/{channel_id}", params={"model": model})
        return {"success": bool(data.get("success")), "time": data.get("time"), "message": data.get("message") or ""}
    except (CliError, requests.RequestException) as exc:
        return {"success": False, "time": None, "message": preview_text(str(exc), limit=240)}


def score_channel(
    channel: dict[str, Any],
    stats: dict[str, Any],
    probe: dict[str, Any] | None,
    *,
    recent_window_seconds: int,
) -> dict[str, Any]:
    success = int(stats.get("success") or 0)
    failure = int(stats.get("failure") or 0)
    total = success + failure
    success_rate = stats.get("success_rate")
    if success_rate is None:
        success_rate = 0.55 if int(channel.get("test_time") or 0) else 0.35
    response_ms = float(channel.get("response_time") or 0)
    if stats.get("avg_use_time"):
        response_ms = float(stats["avg_use_time"]) * 1000
    if probe:
        if probe.get("success") and isinstance(probe.get("time"), (int, float)):
            response_ms = float(probe["time"]) * 1000
            success_rate = max(float(success_rate), 0.92)
        elif probe.get("success") is False:
            failure += 1
            total += 1
            success_rate = success / total if total else 0
    test_time = int(channel.get("test_time") or 0)
    test_age_seconds = max(0, int(time.time()) - test_time) if test_time else None
    probe_skipped = bool(probe and probe.get("skipped"))
    test_success = bool(
        (not probe or probe_skipped)
        and test_time
        and (recent_window_seconds <= 0 or test_age_seconds is not None and test_age_seconds <= recent_window_seconds)
        and response_ms > 0
    )
    if test_success:
        success_rate = max(float(success_rate), 0.88)
    latency_score = 0.45 if response_ms <= 0 else clamp(1.0 - (response_ms / 120000.0), 0.0, 1.0)
    budget_errors = int(stats.get("budget_errors") or 0)
    budget_blocked = budget_errors > 0 and (
        success == 0 or budget_errors >= max(success, 1) or stats.get("last_outcome") == "budget_error"
    )
    responses_conversion_failures = int(stats.get("responses_conversion_failures") or 0)
    responses_success = int(stats.get("responses_success") or 0)
    responses_protocol_blocked = responses_conversion_failures > 0 and responses_success == 0
    score = (float(success_rate) * 0.72) + (latency_score * 0.28)
    age_seconds = stats.get("age_seconds")
    freshness = None
    if isinstance(age_seconds, int) and recent_window_seconds > 0:
        freshness = clamp(1.0 - (age_seconds / float(recent_window_seconds)), 0.0, 1.0)
        if test_success or (probe and probe.get("success")):
            freshness = max(freshness, 1.0)
        score *= 0.55 + (0.45 * freshness)
    if total == 0:
        score -= 0.12
    if failure:
        failure_rate = failure / max(total, 1)
        score -= min(0.12 if test_success or (probe and probe.get("success")) else 0.25, failure_rate * 0.35)
    if budget_blocked:
        score -= 0.45
    if responses_protocol_blocked:
        score -= 0.55
    if int(channel.get("status") or 0) != 1:
        score -= 0.4
    return {
        "score": round(clamp(score, 0.0, 1.0), 3),
        "success": success,
        "failure": failure,
        "total": total,
        "success_rate": round(float(success_rate), 3) if success_rate is not None else None,
        "response_ms": int(response_ms) if response_ms else None,
        "budget_blocked": budget_blocked,
        "budget_errors": budget_errors,
        "responses_protocol_blocked": responses_protocol_blocked,
        "responses_conversion_failures": responses_conversion_failures,
        "responses_success": responses_success,
        "last_outcome": stats.get("last_outcome") or "",
        "age_seconds": age_seconds,
        "freshness": round(float(freshness), 3) if freshness is not None else None,
        "test_success": test_success or bool(probe and probe.get("success")),
        "test_age_seconds": test_age_seconds,
        "status_codes": stats.get("status_codes") or {},
        "last_error": stats.get("last_error") or "",
    }


def history_weight_from_score(score_data: dict[str, Any], args: argparse.Namespace) -> int:
    success_key = "responses_success" if args.require_responses_success else "success"
    successes = int(score_data.get(success_key) or 0)
    failures = int(score_data.get("failure") or 0)
    if successes <= 0:
        return int(args.explore_weight)

    total = successes + failures
    success_rate = (successes / total) if total else 1.0
    if success_rate < 0.8:
        return int(args.explore_weight)

    # NewAPI uses weight only inside the chosen priority layer. Keep this a
    # history-volume signal: a channel that has carried many successful calls
    # gets proportionally more traffic, capped by the configured primary weight.
    weight = int(args.explore_weight) + successes
    if success_rate >= 0.98 and successes >= 20:
        weight += min(successes // 2, 25)
    return max(int(args.explore_weight), min(int(args.primary_weight), weight))


def channel_proposal_for_channel(
    channel: dict[str, Any],
    score_data: dict[str, Any],
    *,
    rank: int,
    probe: dict[str, Any] | None,
    args: argparse.Namespace,
) -> tuple[dict[str, Any], str]:
    budget_blocked = bool(score_data.get("budget_blocked"))
    responses_protocol_blocked = bool(score_data.get("responses_protocol_blocked"))
    responses_unverified = bool(args.require_responses_success) and int(score_data.get("responses_success") or 0) == 0
    freshness = score_data.get("freshness")
    test_passed = bool(probe and probe.get("success")) or bool(score_data.get("test_success"))
    responses_passed = int(score_data.get("responses_success") or 0) > 0
    has_current_evidence = freshness is None or float(freshness) > 0 or test_passed
    latest_failure = score_data.get("last_outcome") in {"failure", "budget_error"}
    preserve_current_primary = bool(score_data.get("preserve_current_primary"))
    channel_enabled = int(channel.get("status") or 0) == 1
    proven_for_primary = channel_enabled and (responses_passed if args.require_responses_success else test_passed)
    if preserve_current_primary and not responses_protocol_blocked and not budget_blocked:
        proven_for_primary = True
    if responses_protocol_blocked:
        priority = args.poor_priority
        weight = args.poor_weight
        reason = "responses_protocol_incompatible"
    elif budget_blocked:
        priority = args.poor_priority
        weight = args.poor_weight
        reason = "budget_or_quota_error"
    elif proven_for_primary:
        priority = args.primary_priority
        weight = history_weight_from_score(score_data, args)
        if weight >= args.primary_weight:
            reason = "tested_and_history_heavy"
        elif int(score_data.get("responses_success") or 0) > 0:
            reason = "tested_responses_success"
        else:
            reason = "tested_success"
    elif responses_unverified and test_passed:
        priority = args.fallback_priority
        weight = args.explore_weight
        reason = "test_passed_but_responses_unverified"
    elif responses_unverified:
        priority = args.poor_priority
        weight = args.poor_weight
        reason = "responses_unverified"
    elif test_passed:
        priority = args.fallback_priority
        weight = args.explore_weight
        reason = "test_passed_fallback"
    elif bool(args.explore) and channel_enabled and not latest_failure and has_current_evidence:
        priority = args.poor_priority
        weight = args.poor_weight
        reason = "unproven_exploration_seed"
    else:
        priority = args.poor_priority
        weight = args.poor_weight
        reason = "poor_or_unproven"
    proposal: dict[str, Any] = {
        "priority": int(priority),
        "weight": int(weight),
    }
    if args.target_group:
        proposal["group"] = args.target_group
    return proposal, reason


def has_model_group_signal(candidates: list[dict[str, Any]]) -> bool:
    for item in candidates:
        score = item.get("score") or {}
        probe = item.get("probe")
        if int(score.get("success") or 0) > 0 or int(score.get("failure") or 0) > 0:
            return True
        if int(score.get("responses_success") or 0) > 0 or int(score.get("responses_conversion_failures") or 0) > 0:
            return True
        if probe and probe.get("success") is not None:
            return True
    return False


def exposed_model_count(channel_or_item: dict[str, Any]) -> int:
    return len(split_list(channel_or_item.get("models")))



def _promote_on_recovery(
    args: argparse.Namespace,
    channels: list[dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    """Pre-ranking pass: for each model, find the healthy max priority among
    status=1 channels exposing that model; for any status=1 channel below that
    bar, run a real SSE probe; if it passes, propose lifting its priority to
    the healthy max. Weight is left untouched.

    Returns (proposals_by_channel_id, audit_records).
    """
    proposals: dict[int, dict[str, Any]] = {}
    audits: list[dict[str, Any]] = []
    if not getattr(args, "promote_on_recovery", False):
        return proposals, audits

    enabled = [c for c in channels if int(c.get("status") or 0) == 1]
    # Build per-model healthy max priority across enabled channels.
    model_max: dict[str, int] = {}
    model_members: dict[str, list[dict[str, Any]]] = {}
    for ch in enabled:
        for m in split_list(ch.get("models")):
            if not m:
                continue
            pri = int(ch.get("priority") or 0)
            model_max[m] = max(model_max.get(m, pri), pri)
            model_members.setdefault(m, []).append(ch)

    # Find candidates: status=1, but priority is below the promotion target.
    # The target is the max of (a) the same-model healthy max across enabled
    # channels exposing each of this channel's models, and (b) the global
    # primary_priority. Clause (b) ensures that channels exposing models with
    # no same-model competition (e.g. hermes-imported singleton channels) are
    # still lifted to the primary tier when they probe healthy, rather than
    # being left at priority=0 indefinitely.
    candidate_ids: dict[int, dict[str, Any]] = {}
    candidate_target: dict[int, int] = {}
    candidate_blocking_models: dict[int, list[str]] = {}
    primary_pri = int(getattr(args, "primary_priority", 10) or 0)
    for ch in enabled:
        ch_pri = int(ch.get("priority") or 0)
        ch_models = [m for m in split_list(ch.get("models")) if m]
        if not ch_models:
            continue
        same_model_max = max((model_max.get(m, ch_pri) for m in ch_models), default=ch_pri)
        target = max(same_model_max, primary_pri)
        if target <= ch_pri:
            continue
        cid = int(ch.get("id") or 0)
        candidate_ids[cid] = ch
        candidate_target[cid] = target
        candidate_blocking_models[cid] = [m for m in ch_models if model_max.get(m, ch_pri) > ch_pri or primary_pri > ch_pri]

    if not candidate_ids:
        return proposals, audits

    for cid, ch in candidate_ids.items():
        target = candidate_target[cid]
        blocking = candidate_blocking_models[cid]
        # Pick a probe model: prefer the channel test_model if it is one of the
        # blocking models, otherwise the first blocking model.
        test_model = (ch.get("test_model") or "").strip()
        probe_model = test_model if test_model in blocking else (blocking[0] if blocking else "")
        if not probe_model:
            audits.append({
                "id": cid,
                "name": ch.get("name"),
                "current_priority": int(ch.get("priority") or 0),
                "target_priority": target,
                "blocking_models": blocking,
                "promoted": False,
                "reason": "no_probe_model",
            })
            continue
        try:
            probe = sse_probe_via_relay(
                args,
                model=probe_model,
                prompt=getattr(args, "promote_probe_prompt", "ping"),
                max_tokens=int(getattr(args, "promote_probe_max_tokens", 8) or 8),
            )
        except Exception as exc:  # noqa: BLE001 - report and continue
            audits.append({
                "id": cid,
                "name": ch.get("name"),
                "current_priority": int(ch.get("priority") or 0),
                "target_priority": target,
                "blocking_models": blocking,
                "probe_model": probe_model,
                "promoted": False,
                "reason": "probe_exception",
                "error": preview_text(str(exc), limit=200),
            })
            continue
        passed = bool(probe.get("ok"))
        channel_model_count = exposed_model_count(ch)
        multi_model_skipped = passed and channel_model_count > 1 and not getattr(args, "apply_multi_model_channel", False)
        record = {
            "id": cid,
            "name": ch.get("name"),
            "current_priority": int(ch.get("priority") or 0),
            "target_priority": target,
            "blocking_models": blocking,
            "probe_model": probe_model,
            "probe": {
                "ok": probe.get("ok"),
                "http_status": probe.get("http_status"),
                "latency_ms": probe.get("latency_ms"),
                "first_token_ms": probe.get("first_token_ms"),
                "finish_reason": probe.get("finish_reason"),
                "error": probe.get("error"),
            },
            "promoted": passed and not multi_model_skipped,
            "reason": "multi_model_channel_level_priority" if multi_model_skipped else ("probe_passed" if passed else "probe_failed"),
        }
        if multi_model_skipped:
            record["models"] = split_list(ch.get("models"))
        if passed and not multi_model_skipped:
            proposals[cid] = {"priority": target}
        audits.append(record)
    return proposals, audits


def run_channel_optimization(args: argparse.Namespace) -> dict[str, Any]:
    channels = fetch_existing_channels(args)
    logs = fetch_log_items(args, page_size=args.log_page_size)
    models = args.model
    skipped = []
    scoped_channels = []
    for channel in channels:
        include, reason = channel_matches_optimize_scope(channel, args)
        if not include:
            skipped.append({"id": channel.get("id"), "name": channel.get("name"), "reason": reason})
            continue
        scoped_channels.append(channel)
    optimize_models = optimize_models_for_channels(scoped_channels, models) if args.per_model else [""]
    recommendations = []
    for optimize_model in optimize_models:
        model_scope = [channel for channel in scoped_channels if not optimize_model or channel_supports_model(channel, optimize_model)]
        if not model_scope:
            continue
        if args.per_model and len(model_scope) < 2:
            skipped.append(
                {
                    "model": optimize_model,
                    "channel_id": model_scope[0].get("id"),
                    "name": model_scope[0].get("name"),
                    "reason": "singleton_model",
                }
            )
            continue
        stats_by_channel = build_channel_log_stats(
            logs,
            [optimize_model] if optimize_model else models,
            include_admin_tests=args.include_admin_tests,
        )
        candidates = []
        for channel in model_scope:
            probe = probe_channel(args, channel, optimize_model or probe_model_for_channel(channel, models))
            score_data = score_channel(
                channel,
                stats_by_channel.get(int(channel["id"]), {}),
                probe,
                recent_window_seconds=args.recent_window_seconds,
            )
            candidates.append({"channel": channel, "score": score_data, "probe": probe})
        if args.per_model and not has_model_group_signal(candidates):
            skipped.append(
                {
                    "model": optimize_model,
                    "channel_ids": [int(item["channel"].get("id") or 0) for item in candidates],
                    "reason": "no_model_signal",
                }
            )
            continue
        current_primary_present = any(
            int(item["channel"].get("priority") or 0) == args.primary_priority
            and int(item["channel"].get("weight") or 0) >= args.primary_weight
            and item["score"]["score"] >= args.min_primary_score
            and not item["score"].get("budget_blocked")
            and not item["score"].get("responses_protocol_blocked")
            and (not args.require_responses_success or int(item["score"].get("responses_success") or 0) > 0)
            for item in candidates
        )
        if current_primary_present:
            for item in candidates:
                channel = item["channel"]
                if int(channel.get("priority") or 0) == args.primary_priority and int(channel.get("weight") or 0) >= args.primary_weight:
                    item["score"]["preserve_current_primary"] = True
        ranked = sorted(candidates, key=lambda item: item["score"]["score"], reverse=True)
        for rank, item in enumerate(ranked):
            channel = item["channel"]
            proposal, reason = channel_proposal_for_channel(
                channel,
                item["score"],
                rank=rank,
                probe=item["probe"],
                args=args,
            )
            fields = list(proposal.keys())
            after = {**channel, **proposal}
            changes = change_summary(channel, after, fields)
            recommendations.append(
                {
                    "model": optimize_model or None,
                    "id": channel.get("id"),
                    "name": channel.get("name"),
                    "models": channel.get("models"),
                    "tag": channel.get("tag"),
                    "current": {
                        "status": channel.get("status"),
                        "group": channel.get("group"),
                        "priority": channel.get("priority"),
                        "weight": channel.get("weight"),
                        "response_time": channel.get("response_time"),
                        "test_time": channel.get("test_time"),
                    },
                    "score": item["score"],
                    "probe": item["probe"],
                    "proposal": proposal,
                    "reason": reason,
                    "changes": changes,
                }
            )
    apply_proposals: dict[int, dict[str, Any]] = {}
    apply_skipped: list[dict[str, Any]] = []
    promote_proposals, promote_audits = _promote_on_recovery(args, channels)
    for cid, prop in promote_proposals.items():
        merged = apply_proposals.get(cid)
        if merged is None:
            apply_proposals[cid] = dict(prop)
        else:
            if int(prop.get('priority', 0)) > int(merged.get('priority', 0)):
                merged['priority'] = prop['priority']
    for item in recommendations:
        if not item["changes"]:
            continue
        channel_id = int(item["id"])
        proposal = item["proposal"]
        if args.per_model and exposed_model_count(item) > 1 and not getattr(args, "apply_multi_model_channel", False):
            apply_skipped.append(
                {
                    "model": item.get("model"),
                    "id": channel_id,
                    "name": item.get("name"),
                    "reason": "multi_model_channel_level_weight",
                    "models": split_list(item.get("models")),
                    "proposal": proposal,
                }
            )
            continue
        current = apply_proposals.get(channel_id)
        if current is None:
            apply_proposals[channel_id] = dict(proposal)
            continue
        # NewAPI stores priority/weight on the channel, not per exposed model.
        # Merge per-model advice conservatively so one poor model does not
        # demote a channel that is still healthy for another shared model.
        if int(proposal.get("priority", 0)) > int(current.get("priority", 0)):
            current["priority"] = proposal["priority"]
        if int(proposal.get("weight", 0)) > int(current.get("weight", 0)):
            current["weight"] = proposal["weight"]
        if "group" in proposal and proposal.get("group"):
            current["group"] = proposal["group"]
    apply_results = []
    if effective_apply(args):
        for channel_id, proposal in apply_proposals.items():
            full = api_request(args, "GET", f"/api/channel/{channel_id}").get("data") or {}
            full.update(proposal)
            updated = channel_patch(args, channel_id, {k: full[k] for k in proposal})
            apply_results.append(
                {
                    "id": channel_id,
                    "name": full.get("name"),
                    "success": updated.get("success"),
                    "message": updated.get("message"),
                }
            )
    return {
        "dry_run": not effective_apply(args),
        "scope": {
            "tags": "all" if args.all_tags or not args.tag else args.tag,
            "models": models or "all",
            "include_disabled": args.include_disabled,
            "target_group": args.target_group or None,
            "probe": bool(args.probe),
            "include_admin_tests": bool(args.include_admin_tests),
            "log_page_size": args.log_page_size,
            "recent_window_seconds": args.recent_window_seconds,
            "channel_ids": args.channel_id or None,
            "per_model": bool(args.per_model),
            "explore": bool(args.explore),
            "explore_weight": args.explore_weight,
            "min_explore_score": args.min_explore_score,
            "require_responses_success": bool(args.require_responses_success),
        },
        "candidates": len(recommendations),
        "skipped": skipped,
        "recommendations": recommendations,
        "apply_proposals": apply_proposals,
        "apply_skipped": apply_skipped,
        "applied": apply_results,
        "promote_audits": promote_audits,
    }


def command_channels_optimize(args: argparse.Namespace) -> int:
    emit(run_channel_optimization(args), args.json)
    return 0


def summarize_channel_optimizer_result(result: dict[str, Any]) -> dict[str, Any]:
    recommendations = result.get("recommendations") or []
    changed = [item for item in recommendations if item.get("changes")]
    return {
        "ok": True,
        "time": int(time.time()),
        "dry_run": result.get("dry_run"),
        "candidates": result.get("candidates"),
        "changed_channels": len(changed),
        "applied": result.get("applied") or [],
        "apply_skipped": result.get("apply_skipped") or [],
        "promote_audits": result.get("promote_audits") or [],
        "top": [
            {
                "model": item.get("model"),
                "id": item.get("id"),
                "name": item.get("name"),
                "reason": item.get("reason"),
                "score": item.get("score", {}).get("score"),
                "proposal": item.get("proposal"),
                "changes": item.get("changes"),
            }
            for item in recommendations[:5]
        ],
    }


def set_soft_disable_profile(args: argparse.Namespace) -> list[dict[str, Any]]:
    """Keep NewAPI from casually banning temporary quota/rate-limit channels."""
    desired = {
        "AutomaticDisableChannelEnabled": "false",
        "AutomaticEnableChannelEnabled": "false",
        "AutomaticDisableStatusCodes": "401",
        "AutomaticDisableKeywords": SOFT_DISABLE_KEYWORDS,
        "ChannelDisableThreshold": "600",
    }
    data = api_request(args, "GET", "/api/option/")
    current = {item.get("key"): str(item.get("value", "")) for item in data.get("data") or []}
    changes: list[dict[str, Any]] = []
    for key, value in desired.items():
        before = current.get(key, "")
        if before == value:
            continue
        change = {"key": key, "before": before, "after": value, "applied": False}
        if effective_apply(args):
            response = api_request(args, "PUT", "/api/option/", json_body={"key": key, "value": value})
            change["applied"] = bool(response.get("success"))
            change["message"] = response.get("message") or ""
        changes.append(change)
    return changes


def channel_log_stats(logs: list[dict[str, Any]], *, now: int, window_seconds: int) -> dict[int, dict[str, Any]]:
    stats: dict[int, dict[str, Any]] = {}
    cutoff = now - window_seconds
    for item in logs:
        created = int(item.get("created_at") or 0)
        if created < cutoff:
            continue
        channel_id = int(item.get("channel") or 0)
        channel_name = str(item.get("channel_name") or "")
        model_name = str(item.get("model_name") or "")
        if not channel_id:
            continue
        bucket = stats.setdefault(
            channel_id,
            {
                "channel_name": channel_name,
                "total": 0,
                "ok": 0,
                "scanner_error": 0,
                "client_gone": 0,
                "upstream_error": 0,
                "quota_or_429": 0,
                "long_ok": 0,
                "examples": [],
            },
        )
        bucket["total"] += 1
        other = item.get("other") or {}
        if isinstance(other, str):
            parsed = parse_json_maybe(other) if other.strip() else {}
            other = parsed if isinstance(parsed, dict) else {}
        stream_status = other.get("stream_status") or {}
        status = stream_status.get("status")
        end_reason = stream_status.get("end_reason")
        end_error = str(stream_status.get("end_error") or "")
        content = str(item.get("content_preview") or item.get("content") or "")
        use_time = float(item.get("use_time") or 0)
        if status == "ok":
            bucket["ok"] += 1
            if use_time >= 45:
                bucket["long_ok"] += 1
        if end_reason == "scanner_error" or "INTERNAL_ERROR" in end_error:
            bucket["scanner_error"] += 1
        if end_reason == "client_gone":
            bucket["client_gone"] += 1
        if item.get("type") == 5 or "status_code=500" in content or "status_code=503" in content or "上游没有返回计费信息" in content:
            bucket["upstream_error"] += 1
        if "status_code=429" in content or "quota" in content.lower() or "TooManyRequests" in content:
            bucket["quota_or_429"] += 1
        if len(bucket["examples"]) < 3 and (end_reason or content):
            bucket["examples"].append(
                {
                    "id": item.get("id"),
                    "model": model_name,
                    "use_time": item.get("use_time"),
                    "end_reason": end_reason,
                    "end_error": end_error[:160],
                    "content": content[:160],
                }
            )
    return stats


def stabilize_channel_weights(args: argparse.Namespace) -> list[dict[str, Any]]:
    """Adjust channel weight based on recent log success/error rates.

    Only weight is adjusted; priority stays static.  Channels with recent
    scanner_error or upstream errors get their weight capped; healthy channels
    gradually restore.  This runs every light round (every 10 min) but only
    writes when weight actually needs to change."""
    channels = fetch_existing_channels(args)
    logs = fetch_log_items(args, page_size=int(getattr(args, "log_page_size", 100) or 100))
    stats = channel_log_stats(logs, now=int(time.time()), window_seconds=int(getattr(args, "buddy_window_seconds", 10800) or 10800))
    audits: list[dict[str, Any]] = []
    for channel in channels:
        channel_id = int(channel.get("id") or 0)
        if int(channel.get("status") or 0) != CHANNEL_STATUS_ENABLED:
            continue
        current_weight = int(channel.get("weight") or 0)
        target_weight = current_weight
        stat = stats.get(channel_id, {"total": 0, "ok": 0, "scanner_error": 0, "client_gone": 0, "upstream_error": 0, "quota_or_429": 0, "long_ok": 0, "examples": []})
        reason = "unchanged"
        if int(stat.get("scanner_error") or 0) > 0:
            target_weight = min(current_weight, int(getattr(args, "buddy_scanner_cap", 8) or 8))
            reason = "scanner_error_cap"
        elif int(stat.get("upstream_error") or 0) >= int(getattr(args, "buddy_error_threshold", 2) or 2):
            target_weight = min(current_weight, int(getattr(args, "buddy_error_cap", 12) or 12))
            reason = "upstream_error_cap"
        elif (
            int(stat.get("ok") or 0) >= int(getattr(args, "buddy_restore_min_ok", 20) or 20)
            and int(stat.get("scanner_error") or 0) == 0
            and int(stat.get("upstream_error") or 0) == 0
        ):
            target_weight = min(
                int(getattr(args, "buddy_max_weight", 100) or 100),
                max(
                    int(getattr(args, "buddy_healthy_floor", 20) or 20),
                    current_weight + int(getattr(args, "buddy_restore_step", 4) or 4),
                ),
            )
            reason = "healthy_restore"

        change: dict[str, Any] = {
            "id": channel_id,
            "name": channel.get("name"),
            "current_weight": current_weight,
            "target_weight": target_weight,
            "reason": reason,
            "stats": stat,
            "applied": False,
        }
        if reason not in {"unchanged"} and target_weight != current_weight:
            if effective_apply(args):
                full = api_request(args, "GET", f"/api/channel/{channel_id}").get("data") or {}
                response = channel_patch(args, channel_id, {"weight": target_weight})
                change["applied"] = bool(response.get("success"))
                change["message"] = response.get("message") or ""
        audits.append(change)
    return audits


def _maintenance_namespace(args: argparse.Namespace, **overrides: Any) -> argparse.Namespace:
    data = vars(args).copy()
    data.update(overrides)
    return argparse.Namespace(**data)


def run_channel_maintenance(args: argparse.Namespace) -> dict[str, Any]:
    now = int(time.time())
    # Heavy round (option sync only) runs at most once per hour.
    # Light rounds (every 10 min): quota hold + weight stabilization.
    # Weight adjustment is read-log-then-write; no upstream probes.
    heavy_interval = int(getattr(args, "heavy_round_seconds", 3600) or 3600)
    last_heavy_path = Path("/opt/newapi-maintainer/.last-heavy-round")
    is_heavy = True
    try:
        if last_heavy_path.exists():
            last_heavy = int(last_heavy_path.read_text().strip() or 0)
            if now - last_heavy < heavy_interval:
                is_heavy = False
    except Exception:
        pass

    result: dict[str, Any] = {
        "time": now,
        "apply": effective_apply(args),
        "base_url": args.base_url,
        "round": "heavy" if is_heavy else "light",
    }

    # --- every round: quota hold + weight stabilization ---
    try:
        result["quota_holds"] = hold_quota_channels(_maintenance_namespace(args, channel_id=[], model="", from_logs=True))
    except Exception as exc:  # noqa: BLE001
        result["quota_holds_error"] = preview_text(str(exc), limit=500)

    try:
        result["weight_adjustments"] = stabilize_channel_weights(args)
    except Exception as exc:  # noqa: BLE001
        result["weight_adjustments_error"] = preview_text(str(exc), limit=500)

    result["recover"] = {
        "skipped": True,
        "reason": "auto_recovery_disabled; use channels recover manually after operator approval",
    }

    if not is_heavy:
        return result

    # --- heavy round: option sync only ---
    try:
        last_heavy_path.parent.mkdir(parents=True, exist_ok=True)
        last_heavy_path.write_text(str(now))
    except Exception:
        pass

    try:
        result["option_changes"] = set_soft_disable_profile(args)
    except Exception as exc:  # noqa: BLE001
        result["option_error"] = preview_text(str(exc), limit=500)

    return result

def command_channels_maintain(args: argparse.Namespace) -> int:
    result = run_channel_maintenance(args)
    emit_and_optionally_log(result, args.json, getattr(args, "json_log", "") or "")
    return 0


def display_name_for_model(model: str) -> str:
    overrides = {
        "gpt-5.5": "GPT-5.5",
        "gpt-5.4": "GPT-5.4",
        "gpt-5.4-mini": "GPT-5.4-Mini",
        "gpt-5.4-openai-compact": "GPT-5.4 OpenAI Compact",
        "gpt-5.5-openai-compact": "GPT-5.5 OpenAI Compact",
        "gpt-5.3-codex": "GPT-5.3-Codex",
        "gpt-5.3-codex-spark": "GPT-5.3-Codex Spark",
        "gpt-5.2": "GPT-5.2",
        "glm-5.2": "GLM-5.2",
        "gemini-2.5-flash": "Gemini 2.5 Flash",
        "deepseek-v4-pro": "DeepSeek V4 Pro",
        "deepseek-v4-flash": "DeepSeek V4 Flash",
        "codex-auto-review": "Codex Auto Review",
        "stepfun-ai/step-3.7-flash": "StepFun Step 3.7 Flash",
    }
    if model in overrides:
        return overrides[model]
    return model.replace("/", " / ").replace("-", " ").title()


def model_description(model: str) -> str:
    if model.startswith("gpt-"):
        return "Relay-provided GPT-compatible coding model."
    if model.startswith("glm-"):
        return "Relay-provided GLM coding model through NewAPI."
    if model.startswith("gemini-"):
        return "Relay-provided Gemini model through NewAPI."
    if model.startswith("deepseek-"):
        return "Relay-provided DeepSeek model through NewAPI."
    return "Relay-provided model exposed by NewAPI."


def catalog_template_from_existing(existing: dict[str, Any]) -> dict[str, Any]:
    models = existing.get("models") if isinstance(existing, dict) else None
    if isinstance(models, list):
        for model in models:
            if isinstance(model, dict) and model.get("slug") == "gpt-5.5":
                return dict(model)
        for model in models:
            if isinstance(model, dict):
                return dict(model)
    raise CliError("Codex catalog template has no usable model entries")



def apply_codex_model_override(model: str, entry: dict[str, Any]) -> dict[str, Any]:
    override = CODEX_MODEL_OVERRIDES.get(model)
    if not override:
        return entry
    for key, value in override.items():
        if key == "context_window":
            entry["context_window"] = value
            entry["max_context_window"] = value
            entry["contextWindow"] = value
            entry["effective_context_window_percent"] = 95
        else:
            entry[key] = value
    return entry


def _set_top_level_toml_string(config_text: str, key: str, value: str) -> str:
    desired = f'{key} = {json.dumps(value)}'
    lines = config_text.splitlines()
    key_re = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for index, line in enumerate(lines):
        if line.lstrip().startswith("["):
            break
        if key_re.match(line):
            lines[index] = desired
            return "\n".join(lines).rstrip() + "\n"
    insert_at = next((index for index, line in enumerate(lines) if line.lstrip().startswith("[")), len(lines))
    prefix = lines[:insert_at]
    suffix = lines[insert_at:]
    while prefix and not prefix[-1].strip():
        prefix.pop()
    if prefix:
        prefix.extend(["", desired, ""])
    else:
        prefix.extend([desired, ""])
    return "\n".join(prefix + suffix).rstrip() + "\n"



def selectable_codex_model_ids(model_ids: list[str]) -> list[str]:
    return [model for model in model_ids if model not in CODEX_MODEL_QUARANTINE]


def agent_visible_model_ids(catalog: dict[str, Any]) -> list[str]:
    models = catalog.get("models") if isinstance(catalog, dict) else None
    if not isinstance(models, list):
        return []
    return [
        slug
        for item in models
        if isinstance(item, dict)
        and (slug := _model_slug(item))
        and str(item.get("visibility") or "list") != "hide"
    ]


def project_codexplusplus_settings(settings: dict[str, Any], model_ids: list[str]) -> dict[str, Any]:
    profiles = settings.get("relayProfiles")
    if not isinstance(profiles, list) or not profiles:
        return {"profile_id": "", "repairs": [], "reason": "no-relay-profiles"}
    active_id = str(settings.get("activeRelayId") or "")
    profile = next((item for item in profiles if isinstance(item, dict) and str(item.get("id") or "") == active_id), None)
    if profile is None:
        profile = next((item for item in profiles if isinstance(item, dict)), None)
    if profile is None:
        return {"profile_id": "", "repairs": [], "reason": "no-relay-profiles"}

    repairs: list[str] = []
    desired_model_list = "\n".join(model_ids)
    if str(profile.get("modelList") or "").strip() != desired_model_list:
        profile["modelList"] = desired_model_list
        repairs.append("modelList")

    current_config = str(profile.get("configContents") or "")
    desired_config = _set_top_level_toml_string(current_config, "model_catalog_json", DEFAULT_CODEX_CATALOG_PATH.name)
    if desired_config != current_config:
        profile["configContents"] = desired_config
        repairs.append("configContents")

    return {
        "profile_id": str(profile.get("id") or ""),
        "repairs": repairs,
        "reason": "repaired" if repairs else "already-current",
    }


def build_codex_catalog_task_action(
    *,
    pythonw_executable: Path,
    script_path: Path,
    log_path: Path,
) -> str:
    return (
        f'"{pythonw_executable}" "{script_path}" --json codex-catalog sync --apply '
        f'--log-path "{log_path}"'
    )
def build_codex_model_entry(model: str, template: dict[str, Any], priority: int) -> dict[str, Any]:
    entry = dict(template)
    entry["slug"] = model
    entry["display_name"] = display_name_for_model(model)
    entry["description"] = model_description(model)
    entry["priority"] = priority
    entry["visibility"] = "hide" if model == "codex-auto-review" or model in CODEX_MODEL_QUARANTINE else "list"
    entry["availability_nux"] = None
    entry["upgrade"] = None
    entry["service_tiers"] = []
    entry["additional_speed_tiers"] = []
    ctx, _max_out = ContextMeta.resolve(model, for_codex=True)
    if ctx is None:
        ctx = 1_048_576  # fallback when OpenRouter unreachable and no override
    # Ratchet (karma #147): Codex Desktop uses context_window as an async compaction
    # trigger, not a hard truncation limit. When a CODEX_PRODUCT_OVERRIDE exists
    # (codex ctx < upstream ctx), verify >=20% headroom for compaction to complete.
    upstream_ctx, _ = ContextMeta.resolve(model, for_codex=False)
    min_headroom = int(upstream_ctx * 0.2) if upstream_ctx else 200_000
    if (
        upstream_ctx and ctx
        and ctx < upstream_ctx
        and (upstream_ctx - ctx) < min_headroom
    ):
        sys.stderr.write(
            f"WARNING: {model} codex context_window={ctx} has only "
            f"{upstream_ctx - ctx} headroom below upstream limit={upstream_ctx}; "
            f"need >={min_headroom} for async compaction (karma #147)\n"
        )
    entry["context_window"] = ctx
    entry["max_context_window"] = ctx
    entry["effective_context_window_percent"] = 95
    entry = apply_codex_model_override(model, entry)
    entry["supported_reasoning_levels"] = _with_codex_reasoning_floor(entry.get("supported_reasoning_levels"))
    return entry


def codex_client_version_triplet() -> str:
    version_text = "0.140.0"
    try:
        executable = os.environ.get("CODEX_CLI_PATH") or shutil.which("codex") or "codex"
        proc = subprocess.run(
            [executable, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
        if proc.returncode == 0:
            match = re.search(r"(\d+\.\d+\.\d+)", proc.stdout)
            if match:
                version_text = match.group(1)
    except Exception:
        pass
    return version_text


def build_codex_models_cache(catalog: dict[str, Any]) -> dict[str, Any]:
    return {
        "fetched_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "etag": None,
        "client_version": codex_client_version_triplet(),
        "models": catalog.get("models", []),
    }


def configured_channel_model_ids(args: argparse.Namespace) -> list[str]:
    channels = fetch_existing_channels(args)
    model_ids: list[str] = []
    for channel in channels:
        if channel.get("status") != 1 and not getattr(args, "include_disabled", False):
            continue
        if getattr(args, "exclude_tag", None) and channel.get("tag") in args.exclude_tag:
            continue
        model_ids.extend(split_list(channel.get("models")))
    return sorted(set(model_ids))


def live_newapi_model_ids(args: argparse.Namespace) -> list[str]:
    data = caller_api_request(args, "GET", "/v1/models")
    models = data.get("data") if isinstance(data, dict) else None
    if not isinstance(models, list):
        raise CliError("/v1/models response does not contain a data list")
    return sorted(
        {
            str(item.get("id")).strip()
            for item in models
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        }
    )


def command_codex_catalog_models(args: argparse.Namespace) -> int:
    model_ids = live_newapi_model_ids(args)
    emit({"source": getattr(args, "source", "v1-models"), "count": len(model_ids), "models": model_ids}, args.json)
    return 0



def _model_slug(model: dict[str, Any]) -> str:
    for key in ("slug", "model", "id", "name"):
        value = str(model.get(key) or "").strip()
        if value:
            return value
    return ""


def read_cc_switch_model_specs(db_path: Path) -> dict[str, dict[str, Any]]:
    if not db_path.is_file():
        return {}
    try:
        connection = sqlite3.connect(db_path)
        try:
            row = connection.execute(
                "select settings_config from providers where app_type='codex' and is_current=1 limit 1"
            ).fetchone()
        finally:
            connection.close()
        if not row or not str(row[0] or "").strip():
            return {}
        settings = json.loads(row[0])
        models = ((settings.get("modelCatalog") or {}).get("models") or [])
        return {
            slug: dict(item)
            for item in models
            if isinstance(item, dict) and (slug := _model_slug(item))
        }
    except (sqlite3.Error, ValueError, TypeError):
        return {}


def _apply_cc_switch_spec(entry: dict[str, Any], spec: dict[str, Any] | None) -> dict[str, Any]:
    if not spec:
        return entry
    display_name = str(spec.get("displayName") or spec.get("display_name") or "").strip()
    if display_name:
        entry["display_name"] = display_name
        entry["displayName"] = display_name
    context = spec.get("contextWindow") or spec.get("context_window") or spec.get("max_context_window")
    if context is not None:
        entry["context_window"] = int(context)
        entry["max_context_window"] = int(context)
        entry["contextWindow"] = int(context)
    parallel = spec.get("supportsParallelToolCalls")
    if isinstance(parallel, bool):
        entry["supports_parallel_tool_calls"] = parallel
    modalities = spec.get("inputModalities")
    if isinstance(modalities, list) and modalities:
        entry["input_modalities"] = modalities
    base_instructions = str(spec.get("baseInstructions") or spec.get("base_instructions") or "").strip()
    if base_instructions:
        entry["base_instructions"] = base_instructions
    return entry


def build_projected_codex_catalog(
    existing: dict[str, Any],
    models_cache: dict[str, Any],
    model_ids: list[str],
    cc_switch_specs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    existing_models = [
        item for payload in (existing, models_cache)
        for item in (payload.get("models") if isinstance(payload, dict) else []) or []
        if isinstance(item, dict)
    ]
    existing_by_slug = {_model_slug(item): item for item in existing_models if _model_slug(item)}
    base_template = catalog_template_from_existing(existing)
    projected: list[dict[str, Any]] = []
    for index, model in enumerate(model_ids):
        template = existing_by_slug.get(model)
        if template is None and model == "workbuddy-glm-5.2":
            template = existing_by_slug.get("glm-5.2")
        if template is None:
            template = base_template
        seeded = _apply_cc_switch_spec(dict(template), cc_switch_specs.get(model))
        entry = build_codex_model_entry(model, seeded, index * 2)
        entry["model"] = model
        entry["displayName"] = entry["display_name"]
        entry["contextWindow"] = entry["context_window"]
        for field in ("base_instructions", "supported_reasoning_levels", "truncation_policy"):
            if field not in entry or entry[field] in (None, ""):
                raise CliError(f"projected Codex model {model!r} is missing required field {field!r}")
        projected.append(entry)
    return {"models": projected}


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise



def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def sync_config_catalog_entry(config_path: Path, apply: bool) -> dict[str, Any]:
    if not config_path.is_file():
        return {"missing": True, "written": False, "needs_repair": False, "path": str(config_path)}
    current = config_path.read_text(encoding="utf-8-sig")
    desired = _set_top_level_toml_string(current, "model_catalog_json", DEFAULT_CODEX_CATALOG_PATH.name)
    needs_repair = desired != current
    if needs_repair and apply:
        write_text_atomic(config_path, desired)
    return {
        "missing": False,
        "written": bool(needs_repair and apply),
        "needs_repair": needs_repair,
        "path": str(config_path),
    }


def sync_cc_switch_provider_config(db_path: Path, apply: bool) -> dict[str, Any]:
    if not db_path.is_file():
        return {"missing": True, "written": False, "needs_repair": False, "path": str(db_path)}
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(
            "select rowid, settings_config from providers where app_type='codex' and is_current=1 limit 1"
        ).fetchone()
        if not row:
            return {"missing": False, "written": False, "needs_repair": False, "reason": "no-current-provider", "path": str(db_path)}
        rowid, raw = row
        settings = json.loads(raw)
        current = settings.get("config")
        if not isinstance(current, str):
            return {"missing": False, "written": False, "needs_repair": False, "reason": "no-config-source", "path": str(db_path)}
        desired = _set_top_level_toml_string(current, "model_catalog_json", DEFAULT_CODEX_CATALOG_PATH.name)
        needs_repair = desired != current
        if needs_repair and apply:
            settings["config"] = desired
            connection.execute(
                "update providers set settings_config=? where rowid=?",
                (json.dumps(settings, ensure_ascii=False, separators=(",", ":")), rowid),
            )
            connection.commit()
        return {
            "missing": False,
            "written": bool(needs_repair and apply),
            "needs_repair": needs_repair,
            "reason": "repaired" if needs_repair else "already-current",
            "path": str(db_path),
        }
    finally:
        connection.close()
def sync_codexplusplus_settings_file(settings_path: Path, model_ids: list[str], apply: bool) -> dict[str, Any]:
    if not settings_path.is_file():
        return {"missing": True, "written": False, "repairs": [], "path": str(settings_path)}
    settings = json.loads(settings_path.read_text(encoding="utf-8-sig"))
    projection = project_codexplusplus_settings(settings, model_ids)
    written = bool(projection["repairs"] and apply)
    if written:
        write_json_atomic(settings_path, settings)
    return {
        "missing": False,
        "written": written,
        "needs_repair": bool(projection["repairs"]),
        "path": str(settings_path),
        **projection,
    }


def _resolve_pythonw_executable(explicit: str = "") -> Path:
    candidate = Path(explicit).expanduser() if explicit else Path(sys.executable).with_name("pythonw.exe")
    resolved = candidate.resolve()
    if not resolved.is_file():
        raise CliError(f"hidden Python executable not found: {resolved}")
    return resolved


def build_codex_catalog_task_create_command(
    *,
    pythonw_executable: Path,
    script_path: Path,
    task_name: str,
    interval_minutes: int,
    log_path: Path,
) -> list[str]:
    action = build_codex_catalog_task_action(
        pythonw_executable=pythonw_executable,
        script_path=script_path,
        log_path=log_path,
    )
    return [
        "schtasks.exe",
        "/Create",
        "/TN",
        task_name,
        "/SC",
        "MINUTE",
        "/MO",
        str(interval_minutes),
        "/TR",
        action,
        "/F",
    ]


def command_codex_catalog_task(args: argparse.Namespace) -> int:
    task_name = args.task_name
    pythonw_executable = _resolve_pythonw_executable(getattr(args, "pythonw_executable", ""))
    script_path = Path(__file__).resolve()
    if not script_path.is_file():
        raise CliError(f"relay-gate script not found: {script_path}")
    log_path = Path(getattr(args, "log_path", DEFAULT_CODEX_CATALOG_LOG_PATH)).expanduser()
    action = args.task_action
    if action == "install":
        command = build_codex_catalog_task_create_command(
            pythonw_executable=pythonw_executable,
            script_path=script_path,
            task_name=task_name,
            interval_minutes=args.interval_minutes,
            log_path=log_path,
        )
    elif action == "status":
        command = ["schtasks.exe", "/Query", "/TN", task_name, "/FO", "LIST", "/V"]
    elif action == "remove":
        command = ["schtasks.exe", "/Delete", "/TN", task_name, "/F"]
    else:
        raise CliError(f"unsupported task action: {action}")

    apply = action == "status" or effective_apply(args)
    result: dict[str, Any] = {
        "action": action,
        "task_name": task_name,
        "dry_run": not apply,
        "task_action": build_codex_catalog_task_action(
            pythonw_executable=pythonw_executable,
            script_path=script_path,
            log_path=log_path,
        ),
        "command": command,
    }
    if apply:
        if action == "install":
            end_proc = subprocess.run(
                ["schtasks.exe", "/End", "/TN", task_name],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            result["end_existing_returncode"] = end_proc.returncode
        proc = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        result["returncode"] = proc.returncode
        result["stdout"] = proc.stdout.strip()
        result["stderr"] = proc.stderr.strip()
        if proc.returncode != 0:
            emit(result, args.json)
            return proc.returncode
    emit(result, args.json)
    return 0


def command_codex_catalog_sync(args: argparse.Namespace) -> int:
    config_path = Path(getattr(args, "config_path", DEFAULT_CODEX_CONFIG_PATH)).expanduser()
    catalog_path = Path(args.catalog_path).expanduser()
    if not catalog_path.is_absolute():
        catalog_path = Path.home() / ".codex" / catalog_path
    models_cache_path = Path(args.models_cache_path).expanduser()
    if not models_cache_path.is_absolute():
        models_cache_path = Path.home() / ".codex" / models_cache_path
    plus_settings_path = Path(args.codex_plus_plus_settings_path).expanduser()
    cc_switch_db_path = Path(args.cc_switch_db_path).expanduser()

    existing = json.loads(catalog_path.read_text(encoding="utf-8-sig"))
    if models_cache_path.is_file():
        existing_cache = json.loads(models_cache_path.read_text(encoding="utf-8-sig"))
    else:
        existing_cache = {"models": []}
    if args.source == "channels":
        model_ids = configured_channel_model_ids(args)
    else:
        model_ids = live_newapi_model_ids(args)
    if not args.include_hidden:
        model_ids = [model for model in model_ids if model != "codex-auto-review"]
    pinned = split_list(args.pin_first)
    ordered = [model for model in pinned if model in model_ids]
    ordered.extend(model for model in model_ids if model not in set(ordered))

    cc_switch_specs = read_cc_switch_model_specs(cc_switch_db_path)
    new_catalog = build_projected_codex_catalog(existing, existing_cache, ordered, cc_switch_specs)
    before = [item.get("slug") for item in existing.get("models", []) if isinstance(item, dict)]
    after = [item.get("slug") for item in new_catalog["models"]]
    apply = effective_apply(args)
    result: dict[str, Any] = {
        "dry_run": not apply,
        "catalog_path": str(catalog_path),
        "models_cache_path": str(models_cache_path),
        "source": args.source,
        "before_count": len(before),
        "after_count": len(after),
        "added": sorted(set(after) - set(before)),
        "removed": sorted(set(before) - set(after)),
        "models": after,
        "cc_switch_specs": sorted(set(after) & set(cc_switch_specs)),
        "synthesized_models": sorted(set(after) - set(cc_switch_specs)),
    }
    catalog_changed = existing != new_catalog
    expected_client_version = codex_client_version_triplet()
    cache_shape_ok = all(key in existing_cache for key in ("fetched_at", "client_version", "models"))
    cache_models_equal = existing_cache.get("models") == new_catalog["models"]
    cache_version_equal = existing_cache.get("client_version") == expected_client_version
    cache_changed = not (cache_shape_ok and cache_models_equal and cache_version_equal)
    new_models_cache = build_codex_models_cache(new_catalog) if cache_changed else existing_cache
    if cache_changed:
        new_models_cache["client_version"] = expected_client_version
    written_files: list[str] = []
    if apply:
        if catalog_changed:
            write_json_atomic(catalog_path, new_catalog)
            written_files.append(str(catalog_path))
        if cache_changed:
            write_json_atomic(models_cache_path, new_models_cache)
            written_files.append(str(models_cache_path))
        result["written"] = bool(written_files)
        result["written_files"] = written_files

    selectable_models = selectable_codex_model_ids(after)
    result["quarantined_models"] = sorted(set(after) & set(CODEX_MODEL_QUARANTINE))
    result["selectable_models"] = selectable_models

    if getattr(args, "sync_config", True):
        result["config"] = sync_config_catalog_entry(config_path, apply)
        result["cc_switch_config"] = sync_cc_switch_provider_config(cc_switch_db_path, apply)
    else:
        result["config"] = {"skipped": True, "written": False}
        result["cc_switch_config"] = {"skipped": True, "written": False}

    if args.sync_codex_plus_plus:
        result["codex_plus_plus"] = sync_codexplusplus_settings_file(plus_settings_path, selectable_models, apply)
    else:
        result["codex_plus_plus"] = {"skipped": True, "written": False}

    if getattr(args, "sync_agent_models", False):
        result["agent_models"] = sync_agent_model_files(
            agent_visible_model_ids(new_catalog),
            pi_path=Path(getattr(args, "pi_models_path", DEFAULT_PI_MODELS_PATH)).expanduser(),
            pi_cache_path=Path(getattr(args, "pi_models_cache_path", DEFAULT_SERVITOR_PI_MODELS_CACHE_PATH)).expanduser(),
            codebuddy_path=Path(getattr(args, "codebuddy_models_path", DEFAULT_CODEBUDDY_MODELS_PATH)).expanduser(),
            workbuddy_path=Path(getattr(args, "workbuddy_models_path", DEFAULT_WORKBUDDY_MODELS_PATH)).expanduser(),
            base_url=getattr(args, "base_url", DEFAULT_BASE_URL),
            agent="all",
            apply=apply,
        )
    else:
        result["agent_models"] = {"skipped": True, "agents": {}}

    emit_and_optionally_log(result, args.json, getattr(args, "log_path", "") or "")
    return 0


def _model_supports_reasoning(model_id: str) -> bool:
    override = CODEX_MODEL_OVERRIDES.get(model_id) or {}
    levels = _with_codex_reasoning_floor(override.get("supported_reasoning_levels"))
    return any(str(item.get("effort") or "none") != "none" for item in levels if isinstance(item, dict))


def _model_vendor(model_id: str) -> str:
    lower = model_id.lower()
    if lower.startswith("gpt-"):
        return "OpenAI"
    if lower.startswith("grok-"):
        return "xAI"
    if lower.startswith("deepseek-"):
        return "DeepSeek"
    if lower.startswith(("glm-", "workbuddy-glm-")):
        return "Zhipu"
    if lower.startswith("kimi-"):
        return "Moonshot"
    if lower.startswith("step-"):
        return "StepFun"
    return "Unknown"


def _sync_pi_models(
    pi_path: Path,
    model_ids: list[str],
    apply: bool,
    *,
    cache_path: Path | None = None,
) -> dict[str, Any]:
    """Reconcile Pi's NewAPI provider and refresh context metadata."""
    config = json.loads(pi_path.read_text(encoding="utf-8"))
    providers = config.get("providers") if isinstance(config, dict) else None
    provider = providers.get("newapi") if isinstance(providers, dict) else None
    if not isinstance(provider, dict) or not isinstance(provider.get("models"), list):
        return {"path": str(pi_path), "error": "newapi provider models list not found", "written": False}

    original_models = [item for item in provider["models"] if isinstance(item, dict) and item.get("id")]
    existing_by_id = {str(item["id"]): item for item in original_models}
    before_ids = list(existing_by_id)
    desired_models: list[dict[str, Any]] = []
    metadata_changed = 0
    for model_id in model_ids:
        entry = dict(existing_by_id.get(model_id) or {"id": model_id})
        if model_id not in existing_by_id:
            entry["reasoning"] = _model_supports_reasoning(model_id)
        old_ctx = entry.get("contextWindow")
        old_max = entry.get("maxTokens")
        ctx, max_out = ContextMeta.resolve(model_id, for_codex=False)
        if ctx is not None:
            entry["contextWindow"] = ctx
        if max_out is not None:
            entry["maxTokens"] = max_out
        if entry.get("contextWindow") != old_ctx or entry.get("maxTokens") != old_max:
            metadata_changed += 1
        desired_models.append(entry)

    provider["models"] = desired_models
    changed = original_models != desired_models
    if changed and apply:
        write_json_atomic(pi_path, config)
    cache_changed = False
    cache_written = False
    if cache_path is not None:
        cache_models = [
            f"{provider_name}/{item['id']}"
            for provider_name, provider_config in providers.items()
            if isinstance(provider_config, dict) and isinstance(provider_config.get("models"), list)
            for item in provider_config["models"]
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        ]
        try:
            existing_cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.is_file() else []
        except (OSError, ValueError, TypeError):
            existing_cache = []
        cache_changed = existing_cache != cache_models
        if cache_changed and apply:
            write_json_atomic(cache_path, cache_models)
            cache_written = True
    return {
        "path": str(pi_path),
        "total": len(desired_models),
        "added": [model for model in model_ids if model not in existing_by_id],
        "removed": [model for model in before_ids if model not in set(model_ids)],
        "metadata_changed": metadata_changed,
        "written": bool(changed and apply),
        "needs_repair": changed,
        "cache_path": str(cache_path) if cache_path is not None else "",
        "cache_written": cache_written,
        "cache_needs_repair": cache_changed,
    }


def _sync_codebuddy_models(
    cb_path: Path,
    model_ids: list[str],
    apply: bool,
    *,
    url_override: str = "",
) -> dict[str, Any]:
    """Reconcile CodeBuddy custom models and refresh context metadata."""
    config = json.loads(cb_path.read_text(encoding="utf-8"))
    models = config.get("models") if isinstance(config, dict) else config
    if not isinstance(models, list):
        return {"path": str(cb_path), "error": "no models list found", "written": False}

    original_models = [item for item in models if isinstance(item, dict) and item.get("id")]
    original_available = list(config.get("availableModels") or []) if isinstance(config, dict) else []
    existing_by_id = {str(item["id"]): item for item in original_models}
    before_ids = list(existing_by_id)
    desired_models: list[dict[str, Any]] = []
    metadata_changed = 0
    for model_id in model_ids:
        vendor = _model_vendor(model_id)
        template = next((item for item in original_models if str(item.get("vendor") or "") == vendor), None)
        if template is None:
            template = original_models[0] if original_models else None
        if model_id in existing_by_id:
            entry = dict(existing_by_id[model_id])
        elif template is not None:
            entry = dict(template)
        else:
            return {"path": str(cb_path), "error": "no existing model template with credentials", "written": False}
        old_ctx = entry.get("maxInputTokens")
        old_max = entry.get("maxOutputTokens")
        entry["id"] = model_id
        entry["name"] = display_name_for_model(model_id)
        entry["vendor"] = vendor
        if url_override:
            entry["url"] = url_override
        entry.setdefault("supportsToolCall", True)
        modalities = (CODEX_MODEL_OVERRIDES.get(model_id) or {}).get("input_modalities") or []
        if modalities:
            entry["supportsImages"] = "image" in modalities
        if _model_supports_reasoning(model_id):
            entry["supportsReasoning"] = True
        elif model_id not in existing_by_id:
            entry.pop("supportsReasoning", None)
        ctx, max_out = ContextMeta.resolve(model_id, for_codex=False)
        if ctx is not None:
            entry["maxInputTokens"] = ctx
        if max_out is not None:
            entry["maxOutputTokens"] = max_out
        if entry.get("maxInputTokens") != old_ctx or entry.get("maxOutputTokens") != old_max:
            metadata_changed += 1
        desired_models.append(entry)

    if isinstance(config, dict):
        config["models"] = desired_models
        config["availableModels"] = list(model_ids)
        desired_payload: Any = config
    else:
        desired_payload = desired_models
    changed = original_models != desired_models or (isinstance(config, dict) and original_available != model_ids)
    if changed and apply:
        write_json_atomic(cb_path, desired_payload)
    return {
        "path": str(cb_path),
        "total": len(desired_models),
        "added": [model for model in model_ids if model not in existing_by_id],
        "removed": [model for model in before_ids if model not in set(model_ids)],
        "metadata_changed": metadata_changed,
        "written": bool(changed and apply),
        "needs_repair": changed,
    }


def _sync_workbuddy_models(
    workbuddy_path: Path,
    model_ids: list[str],
    base_url: str,
    apply: bool,
) -> dict[str, Any]:
    """Reconcile WorkBuddy's top-level custom model array against NewAPI."""
    config = json.loads(workbuddy_path.read_text(encoding="utf-8"))
    if not isinstance(config, list):
        return {
            "path": str(workbuddy_path),
            "error": "WorkBuddy models file must be a top-level array",
            "written": False,
        }
    normalized_base = base_url.rstrip("/")
    endpoint = (
        f"{normalized_base}/chat/completions"
        if normalized_base.endswith("/v1")
        else f"{normalized_base}/v1/chat/completions"
    )
    return _sync_codebuddy_models(
        workbuddy_path,
        model_ids,
        apply,
        url_override=endpoint,
    )


def sync_agent_model_files(
    model_ids: list[str],
    *,
    pi_path: Path,
    pi_cache_path: Path,
    codebuddy_path: Path,
    workbuddy_path: Path,
    base_url: str,
    agent: str,
    apply: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {"model_count": len(model_ids), "models": model_ids, "agents": {}}
    if agent in ("pi", "all"):
        result["agents"]["pi"] = (
            _sync_pi_models(pi_path, model_ids, apply, cache_path=pi_cache_path)
            if pi_path.is_file()
            else {"path": str(pi_path), "error": "not found", "written": False}
        )
    if agent in ("codebuddy", "all"):
        result["agents"]["codebuddy"] = (
            _sync_codebuddy_models(codebuddy_path, model_ids, apply)
            if codebuddy_path.is_file()
            else {"path": str(codebuddy_path), "error": "not found", "written": False}
        )
    if agent in ("workbuddy", "all"):
        result["agents"]["workbuddy"] = (
            _sync_workbuddy_models(workbuddy_path, model_ids, base_url, apply)
            if workbuddy_path.is_file()
            else {"path": str(workbuddy_path), "error": "not found", "written": False}
        )
    return result


def command_agent_models_sync(args: argparse.Namespace) -> int:
    apply = effective_apply(args)
    catalog_path = Path(args.catalog_path).expanduser()
    if not catalog_path.is_file():
        raise CliError(f"Codex model catalog not found: {catalog_path}")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8-sig"))
    result: dict[str, Any] = {
        "dry_run": not apply,
        "catalog_path": str(catalog_path),
        **sync_agent_model_files(
            agent_visible_model_ids(catalog),
            pi_path=Path(args.pi_models_path).expanduser(),
            pi_cache_path=Path(args.pi_models_cache_path).expanduser(),
            codebuddy_path=Path(args.codebuddy_models_path).expanduser(),
            workbuddy_path=Path(getattr(args, "workbuddy_models_path", DEFAULT_WORKBUDDY_MODELS_PATH)).expanduser(),
            base_url=getattr(args, "base_url", DEFAULT_BASE_URL),
            agent=args.agent,
            apply=apply,
        ),
    }
    emit(result, args.json)
    return 0


def add_common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--admin-token-cred", default=DEFAULT_ADMIN_TOKEN_CRED)
    parser.add_argument("--user-id", default=DEFAULT_USER_ID)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--proxy-url", default="", help="Optional HTTP proxy for NewAPI admin requests, e.g. http://127.0.0.1:7890")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--verbose", action="store_true", help="Expand compact human output with diagnostic fields.")


def add_channel_secret_flags(parser: argparse.ArgumentParser, *, required: bool) -> None:
    group = parser.add_mutually_exclusive_group(required=required)
    group.add_argument("--api-key-cred", default="", help="Sigil credential containing the upstream API key.")
    group.add_argument("--key-stdin", action="store_true", help="Read the upstream API key from stdin.")


def add_optimizer_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--channel-id", type=int, action="append", default=[], help="Only optimize this channel id; repeatable.")
    parser.add_argument("--tag", action="append", default=[], help="Optimize channels with this tag; repeatable. Defaults to all tags.")
    parser.add_argument("--all-tags", action="store_true", help="Ignore tag filters.")
    parser.add_argument("--model", action="append", default=[], help="Model to optimize for; repeatable. Defaults to all channel models.")
    add_bool_pair(
        parser,
        "per-model",
        dest="per_model",
        default=True,
        help_on="Optimize each model only within channels that expose that same model.",
        help_off="Use legacy channel-wide optimization across the selected scope.",
    )
    parser.add_argument("--include-disabled", action="store_true")
    parser.add_argument("--target-group", default="default", help="Set matching channels to this group; pass empty string to keep groups.")
    parser.add_argument("--log-page-size", type=int, default=200)
    parser.add_argument("--log-pages", type=int, default=1, help="Number of NewAPI log pages to scan. NewAPI may cap each page at 100 rows.")
    parser.add_argument("--recent-window-seconds", type=int, default=1800, help="Freshness window for treating usage logs as current evidence.")
    parser.add_argument("--include-admin-tests", action="store_true", help="Count NewAPI admin model-test logs as usage evidence.")
    parser.add_argument("--probe", action="store_true", help="Run NewAPI native channel tests before scoring.")
    parser.add_argument("--primary-priority", type=int, default=10)
    parser.add_argument("--primary-weight", type=int, default=100)
    add_bool_pair(
        parser,
        "explore",
        dest="explore",
        default=True,
        help_on="Keep unproven but enabled channels available as low-weight seeds.",
        help_off="Do not keep unproven channels as exploration seeds.",
    )
    parser.add_argument("--explore-weight", type=int, default=1)
    parser.add_argument("--min-explore-score", type=float, default=0.5)
    add_bool_pair(
        parser,
        "require-responses-success",
        dest="require_responses_success",
        default=True,
        help_on="Only promote channels with observed /v1/responses success; keeps Responses routes off chat-only channels.",
        help_off="Allow chat/test-only evidence to promote channels.",
    )
    parser.add_argument("--fallback-priority", type=int, default=5)
    parser.add_argument("--fallback-weight", type=int, default=25)
    parser.add_argument("--poor-priority", type=int, default=0)
    parser.add_argument("--poor-weight", type=int, default=1)
    parser.add_argument("--min-primary-score", type=float, default=0.58)
    parser.add_argument("--min-fallback-score", type=float, default=0.42)
    parser.add_argument("--promote-on-recovery", action="store_true", help="Before ranking, run a real SSE probe on any status=1 channel whose priority is below the same-model healthy max; if the probe passes, raise its priority to that max so it competes on the top tier with its existing weight.")
    parser.add_argument("--promote-probe-prompt", default="ping", help="Prompt used by the recovery probe.")
    parser.add_argument("--promote-probe-max-tokens", type=int, default=8, help="max_tokens for the recovery probe.")
    parser.add_argument("--caller-token-cred", default=DEFAULT_GENERAL_TOKEN_CRED, help="Sigil cred for the caller token used when --promote-on-recovery runs the SSE probe through the gateway.")
    parser.add_argument("--apply-multi-model-channel", action="store_true", help="Allow per-model recommendations to update channel-level priority/weight for channels exposing multiple models. Use only for the automated maintainer where channel-level tradeoffs are intentional.")
    parser.add_argument("--dry-run", action="store_true", help="Preview only. This is the default unless --apply is set.")
    parser.add_argument("--apply", action="store_true", help="Actually update NewAPI channel priority/weight fields.")


def add_bool_pair(parser: argparse.ArgumentParser, name: str, *, dest: str, default: bool | None, help_on: str, help_off: str) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(f"--{name}", action="store_true", dest=dest, default=default, help=help_on)
    group.add_argument(f"--no-{name}", action="store_false", dest=dest, help=help_off)



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage the personal NewAPI gateway."
    )
    add_common_flags(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="Check service and native admin API access.")
    p.set_defaults(func=command_doctor)

    catalog = sub.add_parser("codex-catalog", help="Sync Codex model catalog from NewAPI models.")
    catalog_sub = catalog.add_subparsers(dest="codex_catalog_command", required=True)

    p = catalog_sub.add_parser("models", help="List model ids exposed by NewAPI for Codex catalog filtering.")
    p.add_argument("--caller-token-cred", default=DEFAULT_GENERAL_TOKEN_CRED)
    p.add_argument("--source", choices=["v1-models"], default="v1-models")
    p.set_defaults(func=command_codex_catalog_models)

    p = catalog_sub.add_parser("sync", help="Project the live NewAPI model set into Codex, Codex++, Pi, CodeBuddy, and WorkBuddy local catalogs.")
    p.add_argument("--config-path", default=str(DEFAULT_CODEX_CONFIG_PATH))
    p.add_argument("--catalog-path", default=str(DEFAULT_CODEX_CATALOG_PATH))
    p.add_argument("--models-cache-path", default=str(DEFAULT_CODEX_MODELS_CACHE_PATH))
    p.add_argument("--codex-plus-plus-settings-path", default=str(DEFAULT_CODEXPLUSPLUS_SETTINGS_PATH))
    p.add_argument("--cc-switch-db-path", default=str(DEFAULT_CC_SWITCH_DB_PATH))
    p.add_argument("--caller-token-cred", default=DEFAULT_GENERAL_TOKEN_CRED)
    p.add_argument("--source", choices=["v1-models", "channels"], default="v1-models")
    p.add_argument("--include-hidden", action="store_true", help="Include hidden operational models such as codex-auto-review.")
    p.add_argument("--include-disabled", action="store_true", help="When --source channels, include disabled channels.")
    p.add_argument("--exclude-tag", action="append", default=[], help="When --source channels, skip channels with this tag.")
    p.add_argument("--pin-first", default="gpt-5.5,gpt-5.4,gpt-5.4-mini,gpt-5.3-codex,gpt-5.2,glm-5.2")
    p.add_argument("--sync-codex-plus-plus", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--sync-config", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--sync-agent-models", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--pi-models-path", default=str(DEFAULT_PI_MODELS_PATH))
    p.add_argument("--pi-models-cache-path", default=str(DEFAULT_SERVITOR_PI_MODELS_CACHE_PATH))
    p.add_argument("--codebuddy-models-path", default=str(DEFAULT_CODEBUDDY_MODELS_PATH))
    p.add_argument("--workbuddy-models-path", default=str(DEFAULT_WORKBUDDY_MODELS_PATH))
    p.add_argument("--log-path", default="", help="Write the latest sync result JSON for scheduled runs.")
    p.add_argument("--dry-run", action="store_true", help="Preview only. This is the default unless --apply is set.")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=command_codex_catalog_sync)

    task = catalog_sub.add_parser("task", help="Manage the Windows scheduled task that runs catalog sync through relay-gate.")
    task_sub = task.add_subparsers(dest="task_action", required=True)

    p = task_sub.add_parser("install", help="Create or replace the relay-gate Codex catalog sync task.")
    p.add_argument("--task-name", default=DEFAULT_CODEX_CATALOG_TASK_NAME)
    p.add_argument("--interval-minutes", type=int, default=5)
    p.add_argument("--pythonw-executable", default="", help="Hidden Python runtime; defaults to pythonw.exe beside the active interpreter.")
    p.add_argument("--log-path", default=str(DEFAULT_CODEX_CATALOG_LOG_PATH))
    p.add_argument("--dry-run", action="store_true", help="Preview only. This is the default unless --apply is set.")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=command_codex_catalog_task)

    p = task_sub.add_parser("status", help="Query the relay-gate Codex catalog sync task.")
    p.add_argument("--task-name", default=DEFAULT_CODEX_CATALOG_TASK_NAME)
    p.add_argument("--pythonw-executable", default="")
    p.add_argument("--log-path", default=str(DEFAULT_CODEX_CATALOG_LOG_PATH))
    p.set_defaults(func=command_codex_catalog_task, dry_run=False, apply=True)

    p = task_sub.add_parser("remove", help="Delete the relay-gate Codex catalog sync task.")
    p.add_argument("--task-name", default=DEFAULT_CODEX_CATALOG_TASK_NAME)
    p.add_argument("--pythonw-executable", default="")
    p.add_argument("--log-path", default=str(DEFAULT_CODEX_CATALOG_LOG_PATH))
    p.add_argument("--dry-run", action="store_true", help="Preview only. This is the default unless --apply is set.")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=command_codex_catalog_task)

    agent_models = sub.add_parser("agent-models", help="Sync Pi, CodeBuddy, and WorkBuddy model lists from the normalized local NewAPI catalog.")
    agent_models_sub = agent_models.add_subparsers(dest="agent_models_command", required=True)

    p = agent_models_sub.add_parser("sync", help="Reconcile model ids and context metadata in Pi, CodeBuddy, and WorkBuddy configs.")
    p.add_argument("--agent", choices=["pi", "codebuddy", "workbuddy", "all"], default="all", help="Which agent config to sync.")
    p.add_argument("--catalog-path", default=str(DEFAULT_CODEX_CATALOG_PATH))
    p.add_argument("--pi-models-path", default=str(DEFAULT_PI_MODELS_PATH))
    p.add_argument("--pi-models-cache-path", default=str(DEFAULT_SERVITOR_PI_MODELS_CACHE_PATH))
    p.add_argument("--codebuddy-models-path", default=str(DEFAULT_CODEBUDDY_MODELS_PATH))
    p.add_argument("--workbuddy-models-path", default=str(DEFAULT_WORKBUDDY_MODELS_PATH))
    p.add_argument("--dry-run", action="store_true", help="Preview only. This is the default unless --apply is set.")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=command_agent_models_sync)

    channels = sub.add_parser("channels", help="Manage gateway channels.")
    channels_sub = channels.add_subparsers(dest="channels_command", required=True)

    p = channels_sub.add_parser("list", help="List channels without revealing upstream keys.")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=20)
    p.add_argument("--status", default="")
    p.add_argument("--type-filter", type=int, default=None)
    p.add_argument("--group-filter", default="")
    p.add_argument("--id-sort", action="store_true")
    p.set_defaults(func=command_channels_list)

    p = channels_sub.add_parser("get", help="Read one channel by id without revealing its upstream key.")
    p.add_argument("--id", type=int, required=True)
    p.set_defaults(func=command_channels_get)

    p = channels_sub.add_parser("create", help="Create one upstream channel via NewAPI API.")
    p.add_argument("--name", required=True)
    p.add_argument("--upstream-base-url", dest="base_url_value", required=True)
    add_channel_secret_flags(p, required=True)
    p.add_argument("--type", default="openai", help="Channel type name or id.")
    p.add_argument("--models", default=DEFAULT_MODELS)
    p.add_argument("--group", default="default")
    p.add_argument("--tag", default="relay-gate")
    p.add_argument("--priority", type=int, default=0)
    p.add_argument("--weight", type=int, default=0)
    p.add_argument("--test-model", default="")
    p.add_argument("--model-mapping", default="")
    p.add_argument("--remark", default="")
    p.add_argument("--status", type=int, default=1)
    p.add_argument("--auto-ban", type=int, default=1)
    p.add_argument("--keep-v1", action="store_true", help="Do not strip a trailing /v1 from upstream base_url.")
    p.add_argument("--dry-run", action="store_true", help="Preview only. This is the default unless --apply is set.")
    p.add_argument("--apply", action="store_true", help="Actually create the channel.")
    p.set_defaults(func=command_channels_create)

    p = channels_sub.add_parser("update", help="Update safe channel fields via NewAPI API.")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--name", default=None)
    p.add_argument("--type", default=None, help="Channel type name or id.")
    p.add_argument("--upstream-base-url", dest="base_url_value", default=None)
    p.add_argument("--models", default=None)
    p.add_argument("--group", default=None)
    p.add_argument("--tag", default=None)
    p.add_argument("--priority", type=int, default=None)
    p.add_argument("--weight", type=int, default=None)
    p.add_argument("--status", type=int, default=None)
    p.add_argument("--test-model", default=None)
    p.add_argument("--model-mapping", default=None)
    p.add_argument("--status-code-mapping", default=None)
    p.add_argument("--remark", default=None)
    p.add_argument("--auto-ban", type=int, default=None)
    p.add_argument("--other", default=None)
    p.add_argument("--param-override", default=None, help="JSON string for param_override (replaces entire field).")
    p.add_argument("--header-override", default=None, help="JSON string for header_override (replaces entire field).")
    p.add_argument("--setting", default=None, help="JSON string for setting field (replaces entire field).")
    p.add_argument("--keep-v1", action="store_true", help="Do not strip a trailing /v1 from upstream base_url.")
    add_channel_secret_flags(p, required=False)
    p.add_argument("--dry-run", action="store_true", help="Preview only. This is the default unless --apply is set.")
    p.add_argument("--apply", action="store_true", help="Actually update the channel.")
    p.set_defaults(func=command_channels_update)

    p = channels_sub.add_parser("test", help="Probe channel health via NewAPI internal test, real relay traffic, or both.")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--model", default="", help="Model to test. Default uses channel.test_model or first models entry. Use '*' to test every configured model.")
    p.add_argument("--via", choices=["newapi", "relay", "both"], default="both", help="newapi: NewAPI's internal /api/channel/test against this channel id. relay: real streaming /v1/chat/completions through the gateway model router, SSE-aware but not channel-pinned. both: run both and require either to pass.")
    p.add_argument("--stream", action="store_true", help="Pass stream=true to NewAPI's channel test. Use for SSE-capable upstreams and disabled-channel recovery probes.")
    p.add_argument("--caller-token-cred", default=DEFAULT_GENERAL_TOKEN_CRED, help="Sigil cred for the caller token used in --via relay/both.")
    p.set_defaults(func=command_channels_test)

    p = channels_sub.add_parser("hold-quota", help="Manually hold quota-exhausted channels until a pinned stream test succeeds.")
    p.add_argument("--channel-id", type=int, action="append", default=[], help="Channel id to hold; repeatable.")
    p.add_argument("--model", default="", help="Probe model override when a channel id needs live quota confirmation.")
    p.add_argument("--from-logs", action="store_true", help="Also scan recent logs for quota-exhausted Buddy/WorkBuddy failures.")
    p.add_argument("--log-page-size", type=int, default=100)
    p.add_argument("--log-pages", type=int, default=5)
    p.add_argument("--dry-run", action="store_true", help="Preview only. This is the default unless --apply is set.")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=command_channels_hold_quota)

    p = channels_sub.add_parser("recover", help="Recover disabled or quota-held channels only after a pinned stream channel-test passes.")
    p.add_argument("--channel-id", type=int, action="append", default=[], help="Channel id to recover; repeatable.")
    p.add_argument("--model", default="", help="Probe model override.")
    p.add_argument("--dry-run", action="store_true", help="Preview only. This is the default unless --apply is set.")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=command_channels_recover)

    models = channels_sub.add_parser("models", help="List or configure channel model exposure.")
    models_sub = models.add_subparsers(dest="channel_models_command", required=True)

    p = models_sub.add_parser("list", help="List configured models for channels.")
    p.add_argument("--channel-id", type=int, action="append", default=[], help="Only include this channel id; repeatable.")
    p.add_argument("--tag", action="append", default=[], help="Only include channels with this tag; repeatable.")
    p.add_argument("--include-disabled", action="store_true")
    p.set_defaults(func=command_channel_models_list)

    p = models_sub.add_parser("set", help="Set one channel's models/test model/model mapping.")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--models", default=None, help="Comma-separated model names to expose on this NewAPI channel.")
    p.add_argument("--test-model", default=None)
    p.add_argument("--model-mapping", default=None, help="JSON object or alias=actual comma list.")
    p.add_argument("--dry-run", action="store_true", help="Preview only. This is the default unless --apply is set.")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=command_channel_models_set)

    p = channels_sub.add_parser("optimize", help="Recommend or apply NewAPI channel priority and weight from logs/tests.")
    add_optimizer_flags(p)
    p.set_defaults(func=command_channels_optimize)

    p = channels_sub.add_parser("maintain", help="Run one NewAPI maintenance round: soft options, quota holds, stream recovery, optimizer, and Buddy damping.")
    p.add_argument("--log-page-size", type=int, default=100)
    p.add_argument("--log-pages", type=int, default=10)
    p.add_argument("--recent-window-seconds", type=int, default=21600)
    p.add_argument("--primary-priority", type=int, default=10)
    p.add_argument("--primary-weight", type=int, default=100)
    p.add_argument("--promote-probe-prompt", default="ping")
    p.add_argument("--promote-probe-max-tokens", type=int, default=8)
    p.add_argument("--buddy-window-seconds", type=int, default=10800)
    p.add_argument("--buddy-scanner-cap", type=int, default=8)
    p.add_argument("--buddy-error-cap", type=int, default=12)
    p.add_argument("--buddy-error-threshold", type=int, default=2)
    p.add_argument("--buddy-restore-min-ok", type=int, default=20)
    p.add_argument("--buddy-restore-step", type=int, default=4)
    p.add_argument("--buddy-healthy-floor", type=int, default=20)
    p.add_argument("--buddy-max-weight", type=int, default=40)
    p.add_argument("--caller-token-cred", default=DEFAULT_GENERAL_TOKEN_CRED)
    p.add_argument("--json-log", default="", help="Write the maintenance JSON result to this file.")
    p.add_argument("--dry-run", action="store_true", help="Preview only. This is the default unless --apply is set.")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=command_channels_maintain)

    bridge = sub.add_parser("responses-bridge", help="Read or ensure the global Responses→ChatCompletions bridge policy.")
    bridge_sub = bridge.add_subparsers(dest="responses_bridge_command", required=True)

    p = bridge_sub.add_parser("get", help="Read the current global Responses→ChatCompletions policy.")
    p.set_defaults(func=command_responses_bridge_get)

    p = bridge_sub.add_parser("ensure", help="Enable bridge scope for listed channels/models without dropping existing entries.")
    p.add_argument("--channel-id", type=int, action="append", default=[], help="Channel id to include; repeatable.")
    p.add_argument("--model-pattern", action="append", default=[], help="Regex to include; repeatable.")
    p.add_argument("--dry-run", action="store_true", help="Preview only. This is the default unless --apply is set.")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=command_responses_bridge_ensure)


    groups = sub.add_parser("groups", help="Manage NewAPI groups: list and atomically ensure group ratio + usable groups.")
    groups_sub = groups.add_subparsers(dest="groups_command", required=True)

    p = groups_sub.add_parser("list", help="List all groups across the three NewAPI group options.")
    p.set_defaults(func=command_groups_list)

    p = groups_sub.add_parser("ensure", help="Atomically ensure a group exists in group_ratio_setting.group_ratio, GroupRatio, and UserUsableGroups with read-back verification.")
    p.add_argument("--name", required=True, help="Group name to ensure.")
    p.add_argument("--ratio", type=float, default=1.0, help="Ratio value for this group.")
    p.add_argument("--dry-run", action="store_true", help="Preview only. This is the default unless --apply is set.")
    p.add_argument("--apply", action="store_true", help="Actually write the three options.")
    p.set_defaults(func=command_groups_ensure)


    tokens = sub.add_parser("tokens", help="Manage NewAPI caller tokens.")
    tokens_sub = tokens.add_subparsers(dest="tokens_command", required=True)

    p = tokens_sub.add_parser("list", help="List caller tokens without revealing keys.")
    p.add_argument("--keyword", default="")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=20)
    p.set_defaults(func=command_tokens_list)

    p = tokens_sub.add_parser("get", help="Read one caller token without revealing its key.")
    p.add_argument("--id", type=int, required=True)
    p.set_defaults(func=command_tokens_get)

    p = tokens_sub.add_parser("create", help="Create one NewAPI caller token.")
    p.add_argument("--name", required=True)
    p.add_argument("--group", default="default")
    p.add_argument("--expired-time", type=int, default=-1)
    p.add_argument("--remain-quota", type=int, default=500000)
    add_bool_pair(
        p,
        "unlimited",
        dest="unlimited",
        default=False,
        help_on="Create the token with unlimited quota.",
        help_off="Create the token with finite quota.",
    )
    add_bool_pair(
        p,
        "model-limits-enabled",
        dest="model_limits_enabled",
        default=False,
        help_on="Enable token model allow-list.",
        help_off="Disable token model allow-list.",
    )
    p.add_argument("--model-limits", default="")
    p.add_argument("--allow-ips", default="")
    add_bool_pair(
        p,
        "cross-group-retry",
        dest="cross_group_retry",
        default=False,
        help_on="Allow NewAPI cross-group retry for this token.",
        help_off="Disable NewAPI cross-group retry for this token.",
    )
    p.add_argument("--store-cred", default="", help="Store generated key into this Sigil credential after --apply.")
    p.add_argument("--dry-run", action="store_true", help="Preview only. This is the default unless --apply is set.")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=command_tokens_create)

    p = tokens_sub.add_parser("update", help="Update safe caller token fields.")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--name", default=None)
    p.add_argument("--status", type=int, default=None)
    p.add_argument("--expired-time", type=int, default=None)
    p.add_argument("--remain-quota", type=int, default=None)
    add_bool_pair(
        p,
        "unlimited",
        dest="unlimited",
        default=None,
        help_on="Set unlimited quota.",
        help_off="Set finite quota.",
    )
    add_bool_pair(
        p,
        "model-limits-enabled",
        dest="model_limits_enabled",
        default=None,
        help_on="Enable token model allow-list.",
        help_off="Disable token model allow-list.",
    )
    p.add_argument("--model-limits", default=None)
    p.add_argument("--allow-ips", default=None)
    p.add_argument("--group", default=None)
    add_bool_pair(
        p,
        "cross-group-retry",
        dest="cross_group_retry",
        default=None,
        help_on="Allow NewAPI cross-group retry for this token.",
        help_off="Disable NewAPI cross-group retry for this token.",
    )
    p.add_argument("--dry-run", action="store_true", help="Preview only. This is the default unless --apply is set.")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=command_tokens_update)

    p = tokens_sub.add_parser("key", help="Regenerate/read a caller token key and optionally store it in Sigil.")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--store-cred", default="")
    p.set_defaults(func=command_tokens_key)

    p = tokens_sub.add_parser("ensure-self", help="Ensure the default self-use caller token and store its key.")
    p.add_argument("--name", default="l1uyun-general-cli")
    p.add_argument("--group", default="default")
    p.add_argument("--remain-quota", type=int, default=500000)
    add_bool_pair(
        p,
        "unlimited",
        dest="unlimited",
        default=True,
        help_on="Use unlimited quota.",
        help_off="Use finite quota.",
    )
    p.add_argument("--allow-ips", default="")
    add_bool_pair(
        p,
        "cross-group-retry",
        dest="cross_group_retry",
        default=False,
        help_on="Allow NewAPI cross-group retry for this token.",
        help_off="Disable NewAPI cross-group retry for this token.",
    )
    add_bool_pair(
        p,
        "store",
        dest="store",
        default=True,
        help_on="Store generated key in Sigil.",
        help_off="Do not store generated key.",
    )
    p.add_argument("--cred-name", default=DEFAULT_GENERAL_TOKEN_CRED)
    p.set_defaults(func=command_tokens_ensure_self)

    logs = sub.add_parser("logs", help="Read NewAPI usage/admin logs without secrets.")
    logs_sub = logs.add_subparsers(dest="logs_command", required=True)

    p = logs_sub.add_parser("recent", help="Read recent logs for channel/model/token diagnosis.")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=20)
    p.add_argument("--self", action="store_true", help="Use /api/log/self instead of admin-wide /api/log/.")
    p.add_argument("--include-other", action="store_true", help="Include parsed log metadata instead of only a preview.")
    p.set_defaults(func=command_logs_recent)

    p = logs_sub.add_parser("stats", help="Read aggregate NewAPI log stats.")
    p.set_defaults(func=command_logs_stats)

    return parser


def _write_cli_failure_log(args: argparse.Namespace, exc: Exception, exit_code: int) -> None:
    log_path = str(getattr(args, "log_path", "") or "").strip()
    if not log_path:
        return
    payload = {
        "ok": False,
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "error_type": type(exc).__name__,
        "error": preview_text(str(exc), limit=2000),
        "exit_code": exit_code,
    }
    try:
        write_json_atomic(Path(log_path).expanduser(), payload)
    except OSError as log_exc:
        print(f"failure log error: {log_exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except CliError as exc:
        _write_cli_failure_log(args, exc, 2)
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except requests.RequestException as exc:
        _write_cli_failure_log(args, exc, 3)
        print(f"network error: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:
        if str(getattr(args, "log_path", "") or "").strip():
            _write_cli_failure_log(args, exc, 1)
            print(f"unexpected error: {exc}", file=sys.stderr)
            return 1
        raise


if __name__ == "__main__":
    raise SystemExit(main())
