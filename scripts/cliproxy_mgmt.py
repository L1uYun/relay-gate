"""CLIProxyAPI (CPA) management surface for relay-gate.

CPA listens on xiaolab-japan 127.0.0.1:8317 (docker cli-proxy-api).
Management auth uses sigil secret cliproxy-mgmt-key as Authorization
(raw key or Bearer). This module never prints full management keys,
caller api-keys, or OAuth tokens.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
from typing import Any

import requests

# Imported late-style constants from parent module via register() helpers.
DEFAULT_CLIPROXY_BASE_URL = os.environ.get("CLIPROXY_BASE_URL", "http://127.0.0.1:8317")
DEFAULT_CLIPROXY_MGMT_CRED = "cliproxy-mgmt-key"
DEFAULT_CLIPROXY_SSH_HOST = os.environ.get("CLIPROXY_SSH_HOST", "xiaolab-japan")


def _helpers():
    # Local import avoids circular import at module load.
    from scripts import relay_gate as rg

    return rg


def cliproxy_auth_headers(mgmt_key: str) -> dict[str, str]:
    # Live CPA v7.2.73 accepts raw Authorization value; management UI uses Bearer.
    # Prefer Bearer to match the official management SPA.
    key = mgmt_key.strip()
    if key.lower().startswith("bearer "):
        value = key
    else:
        value = f"Bearer {key}"
    return {
        "Authorization": value,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def resolve_cliproxy_base_url(args: argparse.Namespace) -> str:
    explicit = (getattr(args, "cliproxy_base_url", None) or "").strip()
    if explicit:
        return explicit.rstrip("/")
    env = (os.environ.get("CLIPROXY_BASE_URL") or "").strip()
    if env:
        return env.rstrip("/")
    return DEFAULT_CLIPROXY_BASE_URL.rstrip("/")


def resolve_cliproxy_transport(args: argparse.Namespace) -> str:
    transport = (getattr(args, "transport", None) or "auto").strip().lower()
    if transport in {"auto", "direct", "ssh"}:
        return transport
    return "auto"


def cliproxy_request_direct(
    *,
    base_url: str,
    mgmt_key: str,
    method: str,
    path: str,
    timeout: int,
    json_body: Any | None = None,
    params: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    url = base_url.rstrip("/") + path
    key = mgmt_key.strip()
    if key.lower().startswith("bearer "):
        auth_values = [key, key.split(" ", 1)[1]]
    else:
        auth_values = [f"Bearer {key}", key]
    last_status = 0
    last_data: Any = None
    for auth in auth_values:
        headers = {
            "Authorization": auth,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        resp = requests.request(
            method,
            url,
            headers=headers,
            json=json_body,
            params=params,
            timeout=timeout,
        )
        try:
            data: Any = resp.json()
        except ValueError:
            data = {"raw": (resp.text or "")[:500]}
        last_status, last_data = resp.status_code, data
        if resp.status_code != 401:
            return resp.status_code, data
    return last_status, last_data


def cliproxy_request_ssh(
    *,
    ssh_host: str,
    base_url: str,
    mgmt_key: str,
    method: str,
    path: str,
    timeout: int,
    json_body: Any | None = None,
    params: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    """Run the HTTP call on the SSH host so localhost:8317 is reachable."""
    import base64

    rg = _helpers()
    payload = {
        "base_url": base_url,
        "method": method,
        "path": path,
        "timeout": timeout,
        "json_body": json_body,
        "params": params or {},
        "mgmt_key": mgmt_key,
    }
    remote_src = """
import json, sys, urllib.error, urllib.parse, urllib.request
spec = json.loads(sys.stdin.read())
key = str(spec.get("mgmt_key") or "").strip()
auths = [key if key.lower().startswith("bearer ") else ("Bearer " + key), key]
base = str(spec.get("base_url") or "").rstrip("/")
path = str(spec.get("path") or "")
params = spec.get("params") or {}
if params:
    path = path + (("&" if "?" in path else "?") + urllib.parse.urlencode(params))
url = base + path
body = spec.get("json_body")
data = None if body is None else json.dumps(body).encode("utf-8")
code = 0
raw = b""
for auth in auths:
    headers = {
        "Authorization": auth,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method=str(spec.get("method") or "GET").upper(),
    )
    try:
        with urllib.request.urlopen(req, timeout=int(spec.get("timeout") or 30)) as resp:
            raw = resp.read()
            code = int(getattr(resp, "status", 200) or 200)
            break
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        code = int(exc.code)
        if code != 401:
            break
    except Exception as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__ + ": " + str(exc)}))
        raise SystemExit(0)
try:
    parsed = json.loads(raw.decode("utf-8", "replace") or "null")
except Exception:
    parsed = {"raw": raw.decode("utf-8", "replace")[:500]}
print(json.dumps({"ok": True, "status": code, "data": parsed}, ensure_ascii=False))
"""
    remote_b64 = base64.b64encode(remote_src.encode("utf-8")).decode("ascii")
    remote_cmd = f"import base64; exec(base64.b64decode('{remote_b64}').decode())"
    try:
        # Quote for the remote POSIX shell; bare multi-token -c breaks under ssh bash -c.
        remote = "python3 -c " + shlex.quote(remote_cmd)
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=12", ssh_host, remote],
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            timeout=max(30, timeout + 20),
        )
    except subprocess.TimeoutExpired as exc:
        raise rg.CliError(f"cliproxy ssh request timed out via {ssh_host}") from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise rg.CliError(f"cliproxy ssh request failed via {ssh_host}: {err[:300]}")
    try:
        result = json.loads(proc.stdout or "{}")
    except ValueError as exc:
        raise rg.CliError(f"cliproxy ssh returned non-JSON: {(proc.stdout or '')[:200]}") from exc
    if not result.get("ok"):
        raise rg.CliError(f"cliproxy ssh transport error: {result.get('error')}")
    return int(result.get("status") or 0), result.get("data")


def cliproxy_request(
    args: argparse.Namespace,
    method: str,
    path: str,
    *,
    json_body: Any | None = None,
    params: dict[str, Any] | None = None,
) -> tuple[int, Any, str]:
    rg = _helpers()
    mgmt_key = rg.reveal_credential(getattr(args, "mgmt_cred", DEFAULT_CLIPROXY_MGMT_CRED))
    base_url = resolve_cliproxy_base_url(args)
    transport = resolve_cliproxy_transport(args)
    timeout = int(getattr(args, "timeout", 30) or 30)
    ssh_host = (getattr(args, "ssh_host", None) or DEFAULT_CLIPROXY_SSH_HOST).strip()

    errors: list[str] = []
    if transport in {"auto", "direct"}:
        try:
            code, data = cliproxy_request_direct(
                base_url=base_url,
                mgmt_key=mgmt_key,
                method=method,
                path=path,
                timeout=timeout,
                json_body=json_body,
                params=params,
            )
            return code, data, "direct"
        except (requests.RequestException, OSError) as exc:
            errors.append(f"direct:{type(exc).__name__}:{exc}")
            if transport == "direct":
                raise rg.CliError(f"cliproxy direct request failed: {exc}") from exc

    if transport in {"auto", "ssh"}:
        if not ssh_host:
            raise rg.CliError("cliproxy ssh transport requires --ssh-host")
        # On the remote host, CPA is bound to 127.0.0.1:8317.
        remote_base = base_url
        if "127.0.0.1" not in remote_base and "localhost" not in remote_base:
            remote_base = "http://127.0.0.1:8317"
        code, data = cliproxy_request_ssh(
            ssh_host=ssh_host,
            base_url=remote_base,
            mgmt_key=mgmt_key,
            method=method,
            path=path,
            timeout=timeout,
            json_body=json_body,
            params=params,
        )
        return code, data, "ssh"

    raise rg.CliError("cliproxy request failed: " + "; ".join(errors))


def summarize_auth_files(data: Any) -> dict[str, Any]:
    files = []
    if isinstance(data, dict):
        files = data.get("files") or data.get("data") or []
    if not isinstance(files, list):
        files = []
    providers: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    disabled = 0
    unavailable = 0
    samples: list[dict[str, Any]] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        provider = str(item.get("provider") or item.get("type") or "unknown")
        providers[provider] = providers.get(provider, 0) + 1
        status = str(item.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        if item.get("disabled"):
            disabled += 1
        if item.get("unavailable"):
            unavailable += 1
        if len(samples) < 8:
            samples.append(
                {
                    "name": item.get("name") or item.get("id"),
                    "provider": provider,
                    "status": status,
                    "disabled": bool(item.get("disabled")),
                    "unavailable": bool(item.get("unavailable")),
                    "email": item.get("email") or item.get("account") or item.get("label"),
                    "success": item.get("success"),
                    "failed": item.get("failed"),
                }
            )
    return {
        "total": len(files),
        "disabled": disabled,
        "unavailable": unavailable,
        "providers": providers,
        "status_counts": status_counts,
        "samples": samples,
    }


def summarize_api_keys(data: Any) -> dict[str, Any]:
    keys = data
    if isinstance(data, dict):
        keys = (
            data.get("api_keys")
            or data.get("api-keys")
            or data.get("keys")
            or data.get("data")
            or data.get("items")
            or []
        )
    if not isinstance(keys, list):
        keys = [keys] if keys else []
    out = []
    for i, item in enumerate(keys):
        if isinstance(item, str):
            rg = _helpers()
            out.append(
                {
                    "index": i,
                    "present": True,
                    "length": len(item),
                    "sha256_prefix": rg.secret_fingerprint(item),
                }
            )
        elif isinstance(item, dict):
            val = item.get("key") or item.get("api_key") or item.get("value")
            rg = _helpers()
            out.append(
                {
                    "index": i,
                    "name": item.get("name") or item.get("id") or item.get("label"),
                    "present": bool(val),
                    "length": len(str(val)) if val else 0,
                    "sha256_prefix": rg.secret_fingerprint(str(val)) if val else None,
                }
            )
        else:
            out.append({"index": i, "type": type(item).__name__})
    return {"count": len(out), "items": out}


def redact_cliproxy_payload(data: Any) -> Any:
    rg = _helpers()
    return rg.redacted_tree(data)


def ensure_ok(code: int, path: str, data: Any) -> None:
    rg = _helpers()
    if code >= 400:
        raise rg.CliError(f"HTTP {code} from {path}: {data}")


def command_cliproxy_doctor(args: argparse.Namespace) -> int:
    rg = _helpers()
    code, data, transport = cliproxy_request(args, "GET", "/v0/management/auth-files")
    ensure_ok(code, "/v0/management/auth-files", data)
    summary = summarize_auth_files(data)
    # secondary probes for surface health
    surfaces: dict[str, Any] = {}
    for path in (
        "/v0/management/api-keys",
        "/v0/management/config",
        "/v0/management/logs",
        "/v0/management/debug",
        "/v0/management/latest-version",
        "/v0/management/get-auth-status",
    ):
        try:
            c, d, _ = cliproxy_request(args, "GET", path)
            surfaces[path] = {"status": c, "ok": c < 400}
        except Exception as exc:  # noqa: BLE001 - surface map should continue
            surfaces[path] = {"status": None, "ok": False, "error": type(exc).__name__}

    payload = {
        "ok": True,
        "transport": transport,
        "base_url": resolve_cliproxy_base_url(args),
        "ssh_host": getattr(args, "ssh_host", DEFAULT_CLIPROXY_SSH_HOST),
        "mgmt_cred": getattr(args, "mgmt_cred", DEFAULT_CLIPROXY_MGMT_CRED),
        "auth_files": summary,
        "surfaces": surfaces,
    }
    human = (
        f"cliproxy doctor ok transport={transport} auth_files={summary['total']} "
        f"disabled={summary['disabled']} unavailable={summary['unavailable']} "
        f"providers={summary['providers']}"
    )
    rg.emit(payload, rg.output_mode(args), human)
    return 0


def command_cliproxy_auth_files_list(args: argparse.Namespace) -> int:
    rg = _helpers()
    code, data, transport = cliproxy_request(args, "GET", "/v0/management/auth-files")
    ensure_ok(code, "/v0/management/auth-files", data)
    summary = summarize_auth_files(data)
    payload = {
        "ok": True,
        "transport": transport,
        "summary": summary,
        "data": redact_cliproxy_payload(data) if rg.is_verbose(args) else None,
    }
    if not rg.is_verbose(args):
        payload.pop("data")
    human_lines = [
        f"auth-files total={summary['total']} disabled={summary['disabled']} unavailable={summary['unavailable']} transport={transport}",
        f"providers={summary['providers']}",
        f"status={summary['status_counts']}",
    ]
    for sample in summary["samples"]:
        human_lines.append(
            f"- {sample.get('provider')}/{sample.get('name')} status={sample.get('status')} "
            f"ok={sample.get('success')} fail={sample.get('failed')} disabled={sample.get('disabled')}"
        )
    rg.emit(payload, rg.output_mode(args), "\n".join(human_lines))
    return 0


def command_cliproxy_auth_files_set_status(args: argparse.Namespace) -> int:
    rg = _helpers()
    name = (args.name or "").strip()
    if not name:
        raise rg.CliError("--name is required")
    disabled = bool(args.disabled)
    body = {"name": name, "disabled": disabled}
    if not rg.effective_apply(args):
        payload = {"ok": True, "dry_run": True, "planned": {"method": "PATCH", "path": "/v0/management/auth-files/status", "body": body}}
        rg.emit(payload, rg.output_mode(args), f"dry-run patch auth-files status name={name} disabled={disabled}")
        return 0
    code, data, transport = cliproxy_request(args, "PATCH", "/v0/management/auth-files/status", json_body=body)
    ensure_ok(code, "/v0/management/auth-files/status", data)
    payload = {"ok": True, "transport": transport, "result": redact_cliproxy_payload(data)}
    rg.emit(payload, rg.output_mode(args), f"patched auth-files status name={name} disabled={disabled} transport={transport}")
    return 0


def command_cliproxy_api_keys_list(args: argparse.Namespace) -> int:
    rg = _helpers()
    code, data, transport = cliproxy_request(args, "GET", "/v0/management/api-keys")
    ensure_ok(code, "/v0/management/api-keys", data)
    summary = summarize_api_keys(data)
    payload = {"ok": True, "transport": transport, "summary": summary}
    rg.emit(payload, rg.output_mode(args), f"api-keys count={summary['count']} transport={transport}")
    return 0


def command_cliproxy_config_get(args: argparse.Namespace) -> int:
    rg = _helpers()
    code, data, transport = cliproxy_request(args, "GET", "/v0/management/config")
    ensure_ok(code, "/v0/management/config", data)
    redacted = redact_cliproxy_payload(data)
    payload = {"ok": True, "transport": transport, "config": redacted}
    # human compact: top-level keys only
    keys = sorted(redacted.keys()) if isinstance(redacted, dict) else []
    rg.emit(payload, rg.output_mode(args), f"config keys={keys} transport={transport}")
    return 0


def command_cliproxy_logs(args: argparse.Namespace) -> int:
    rg = _helpers()
    path = "/v0/management/logs"
    if getattr(args, "errors", False):
        path = "/v0/management/request-error-logs"
    code, data, transport = cliproxy_request(args, "GET", path)
    ensure_ok(code, path, data)
    redacted = redact_cliproxy_payload(data)
    # bound human output
    preview = redacted
    if isinstance(redacted, list):
        preview = redacted[: int(getattr(args, "limit", 20) or 20)]
    elif isinstance(redacted, dict):
        for k in ("logs", "items", "data", "entries"):
            if isinstance(redacted.get(k), list):
                preview = {**redacted, k: redacted[k][: int(getattr(args, "limit", 20) or 20)]}
                break
    payload = {"ok": True, "transport": transport, "path": path, "data": preview}
    rg.emit(payload, rg.output_mode(args), f"{path} transport={transport}")
    return 0


def command_cliproxy_debug(args: argparse.Namespace) -> int:
    rg = _helpers()
    code, data, transport = cliproxy_request(args, "GET", "/v0/management/debug")
    ensure_ok(code, "/v0/management/debug", data)
    payload = {"ok": True, "transport": transport, "debug": redact_cliproxy_payload(data)}
    rg.emit(payload, rg.output_mode(args), f"debug ok transport={transport}")
    return 0


def add_cliproxy_transport_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--cliproxy-base-url",
        default=DEFAULT_CLIPROXY_BASE_URL,
        help="CPA management base URL (default http://127.0.0.1:8317 or CLIPROXY_BASE_URL).",
    )
    parser.add_argument(
        "--mgmt-cred",
        default=DEFAULT_CLIPROXY_MGMT_CRED,
        help="Sigil secret name for CPA management key (default cliproxy-mgmt-key).",
    )
    parser.add_argument(
        "--ssh-host",
        default=DEFAULT_CLIPROXY_SSH_HOST,
        help="SSH host used when transport=ssh/auto (default xiaolab-japan).",
    )
    parser.add_argument(
        "--transport",
        choices=["auto", "direct", "ssh"],
        default="auto",
        help="auto tries direct then ssh; ssh runs the request on the CPA host.",
    )


def register_cliproxy_commands(sub: argparse._SubParsersAction) -> None:
    cliproxy = sub.add_parser("cliproxy", help="Manage CLIProxyAPI (CPA) management API.")
    add_cliproxy_transport_flags(cliproxy)
    cliproxy_sub = cliproxy.add_subparsers(dest="cliproxy_command", required=True)

    p = cliproxy_sub.add_parser("doctor", help="Probe CPA management auth and key surfaces.")
    p.set_defaults(func=command_cliproxy_doctor)

    auth_files = cliproxy_sub.add_parser("auth-files", help="CPA auth file inventory and status.")
    auth_sub = auth_files.add_subparsers(dest="cliproxy_auth_files_command", required=True)
    p = auth_sub.add_parser("list", help="List auth files (summary by default).")
    p.set_defaults(func=command_cliproxy_auth_files_list)
    p = auth_sub.add_parser("set-status", help="Enable/disable one auth file (dry-run default).")
    p.add_argument("--name", required=True, help="Auth file name, e.g. EUR-xxx_cpa.json")
    p.add_argument("--disabled", action=argparse.BooleanOptionalAction, required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=command_cliproxy_auth_files_set_status)

    api_keys = cliproxy_sub.add_parser("api-keys", help="CPA caller API keys.")
    api_sub = api_keys.add_subparsers(dest="cliproxy_api_keys_command", required=True)
    p = api_sub.add_parser("list", help="List API keys without revealing full secrets.")
    p.set_defaults(func=command_cliproxy_api_keys_list)

    config = cliproxy_sub.add_parser("config", help="CPA runtime config.")
    config_sub = config.add_subparsers(dest="cliproxy_config_command", required=True)
    p = config_sub.add_parser("get", help="Get redacted management config.")
    p.set_defaults(func=command_cliproxy_config_get)

    logs = cliproxy_sub.add_parser("logs", help="CPA management logs.")
    logs_sub = logs.add_subparsers(dest="cliproxy_logs_command", required=True)
    p = logs_sub.add_parser("recent", help="Fetch management logs or request-error-logs.")
    p.add_argument("--errors", action="store_true", help="Use /request-error-logs instead of /logs.")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=command_cliproxy_logs)

    p = cliproxy_sub.add_parser("debug", help="Fetch /v0/management/debug.")
    p.set_defaults(func=command_cliproxy_debug)
