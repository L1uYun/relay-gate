import argparse
from scripts.cliproxy_mgmt import (
    summarize_auth_files,
    summarize_api_keys,
    cliproxy_auth_headers,
    resolve_cliproxy_base_url,
    register_cliproxy_commands,
)


def test_auth_headers_bearer_default():
    headers = cliproxy_auth_headers("abc123")
    assert headers["Authorization"] == "Bearer abc123"


def test_auth_headers_preserves_existing_bearer():
    headers = cliproxy_auth_headers("Bearer abc123")
    assert headers["Authorization"] == "Bearer abc123"


def test_summarize_auth_files():
    data = {
        "files": [
            {
                "name": "a.json",
                "provider": "codex",
                "status": "active",
                "disabled": False,
                "unavailable": False,
                "success": 1,
                "failed": 0,
                "email": "a@example.com",
            },
            {
                "name": "b.json",
                "provider": "xai",
                "status": "disabled",
                "disabled": True,
                "unavailable": True,
                "success": 0,
                "failed": 3,
                "email": "b@example.com",
            },
        ]
    }
    summary = summarize_auth_files(data)
    assert summary["total"] == 2
    assert summary["disabled"] == 1
    assert summary["unavailable"] == 1
    assert summary["providers"]["codex"] == 1
    assert summary["providers"]["xai"] == 1


def test_summarize_api_keys_redacts_values():
    data = {"api_keys": ["super-secret-key-value", {"name": "main", "key": "abcdef123456"}]}
    summary = summarize_api_keys(data)
    assert summary["count"] == 2
    blob = str(summary)
    assert "super-secret-key-value" not in blob
    assert "abcdef123456" not in blob


def test_resolve_base_url_env(monkeypatch):
    monkeypatch.setenv("CLIPROXY_BASE_URL", "http://example:8317/")
    args = argparse.Namespace(cliproxy_base_url="")
    assert resolve_cliproxy_base_url(args) == "http://example:8317"


def test_register_cliproxy_commands():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    register_cliproxy_commands(sub)
    args = parser.parse_args(["cliproxy", "doctor"])
    assert args.command == "cliproxy"
    assert callable(args.func)