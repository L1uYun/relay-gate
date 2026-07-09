import argparse
import importlib.util
import json
import sqlite3
from pathlib import Path
import tempfile
import time
import unittest


def load_relay_gate():
    script = Path(__file__).resolve().parents[1] / "scripts" / "relay_gate.py"
    spec = importlib.util.spec_from_file_location("relay_gate", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class RoutingProposalTest(unittest.TestCase):

    def test_channels_optimize_defaults_to_preview(self):
        mod = load_relay_gate()
        parser = mod.build_parser()
        args = parser.parse_args(["--json", "channels", "optimize"])

        self.assertEqual(args.func, mod.command_channels_optimize)
        self.assertFalse(args.apply)
        self.assertFalse(args.dry_run)
        self.assertFalse(mod.effective_apply(args))
    def test_sigil_helper_path_points_to_agentwork_tool_source(self):
        mod = load_relay_gate()

        self.assertEqual(mod.SIGIL, Path(r"D:\AgentWork\tools\sigil\src\sigil.py"))

    def test_reveal_credential_defaults_to_sigil_not_process_env(self):
        mod = load_relay_gate()
        calls = []

        def fake_run_sigil(args, *, input_text=None):
            calls.append(args)
            self.assertEqual(args, ["secret", "show", "TOKEN_NAME", "--reveal"])
            return "sigil-secret"

        old_env = dict(mod.os.environ)
        old_run_sigil = mod.run_sigil
        try:
            mod.os.environ["TOKEN_NAME"] = "process-secret"
            mod.run_sigil = fake_run_sigil

            self.assertEqual(mod.reveal_credential("TOKEN_NAME"), "sigil-secret")
            self.assertEqual(calls, [["secret", "show", "TOKEN_NAME", "--reveal"]])
        finally:
            mod.run_sigil = old_run_sigil
            mod.os.environ.clear()
            mod.os.environ.update(old_env)

    def test_buddy_codex_catalog_entries_keep_reasoning_metadata(self):
        mod = load_relay_gate()
        template = {
            "slug": "gpt-5.5",
            "display_name": "GPT-5.5",
            "default_reasoning_level": "medium",
            "default_reasoning_summary": "auto",
            "supported_reasoning_levels": [{"effort": "low"}, {"effort": "xhigh"}],
            "supports_reasoning_summaries": True,
        }

        for model in ["buddy-kimi2.6", "wb2-glm5.2"]:
            with self.subTest(model=model):
                entry = mod.build_codex_model_entry(model, template, 0)
                self.assertEqual(entry["default_reasoning_level"], "medium")
                self.assertEqual(entry["default_reasoning_summary"], "auto")
                self.assertEqual(entry["supported_reasoning_levels"], template["supported_reasoning_levels"])
                self.assertTrue(entry["supports_reasoning_summaries"])

    def test_non_buddy_responses_bridge_models_keep_reasoning_metadata(self):
        mod = load_relay_gate()
        template = {
            "slug": "gpt-5.5",
            "display_name": "GPT-5.5",
            "default_reasoning_level": "medium",
            "default_reasoning_summary": "none",
            "supported_reasoning_levels": [{"effort": "low"}, {"effort": "xhigh"}],
            "supports_reasoning_summaries": True,
        }

        for model in ["glm-5.2", "deepseek-v4-pro", "deepseek-v4-flash", "stepfun-ai/step-3.7-flash"]:
            with self.subTest(model=model):
                entry = mod.build_codex_model_entry(model, template, 0)
                self.assertEqual(entry["default_reasoning_level"], "medium")
                self.assertEqual(entry["supported_reasoning_levels"], template["supported_reasoning_levels"])
                self.assertTrue(entry["supports_reasoning_summaries"])

    def test_codex_catalog_models_command_reads_only_v1_models(self):
        mod = load_relay_gate()
        calls = []
        emitted = []

        def fake_caller_api_request(args, method, path, *, json_body=None):
            calls.append((method, path, json_body))
            return {
                "data": [
                    {"id": "glm-5.2"},
                    {"id": "deepseek-v4-pro"},
                    {"id": "glm-5.2"},
                    {"id": ""},
                    {"object": "model"},
                ]
            }

        old_caller_api_request = mod.caller_api_request
        old_emit = mod.emit
        try:
            mod.caller_api_request = fake_caller_api_request
            mod.emit = lambda data, as_json: emitted.append(data)
            args = argparse.Namespace(json=True)

            self.assertEqual(mod.command_codex_catalog_models(args), 0)
        finally:
            mod.caller_api_request = old_caller_api_request
            mod.emit = old_emit

        self.assertEqual(calls, [("GET", "/v1/models", None)])
        self.assertEqual(emitted, [{"source": "v1-models", "count": 2, "models": ["deepseek-v4-pro", "glm-5.2"]}])

    def test_codex_catalog_models_parser_does_not_require_catalog_paths(self):
        mod = load_relay_gate()
        parser = mod.build_parser()

        args = parser.parse_args(["--json", "codex-catalog", "models"])

        self.assertEqual(args.func, mod.command_codex_catalog_models)
        self.assertEqual(args.source, "v1-models")
        self.assertTrue(args.json)

    def test_missing_model_overrides_match_provider_surfaces(self):
        mod = load_relay_gate()
        template = {
            "slug": "gpt-5.5",
            "display_name": "GPT-5.5",
            "description": "template",
            "context_window": 256000,
            "max_context_window": 256000,
            "default_reasoning_level": "medium",
            "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}],
            "input_modalities": ["text", "image"],
            "supports_parallel_tool_calls": True,
        }

        expected = {
            "gpt-5.6-sol": (372000, "low", ["low", "medium", "high", "xhigh", "max", "ultra"]),
            "gpt-5.6-terra": (372000, "medium", ["low", "medium", "high", "xhigh", "max", "ultra"]),
            "gpt-5.6-luna": (372000, "medium", ["low", "medium", "high", "xhigh", "max"]),
            "grok-4.5": (500000, "medium", ["low", "medium", "high"]),
            "grok-4.3": (1000000, "medium", ["none", "low", "medium", "high"]),
            "grok-3-mini": (1000000, "medium", ["none", "low", "medium", "high"]),
            "grok-3-mini-fast": (1000000, "medium", ["none", "low", "medium", "high"]),
            "workbuddy-glm-5.2": (400000, "high", ["none", "high"]),
        }

        for model, (context_window, default_effort, efforts) in expected.items():
            with self.subTest(model=model):
                entry = mod.build_codex_model_entry(model, template, 0)
                self.assertEqual(entry["context_window"], context_window)
                self.assertEqual(entry["max_context_window"], context_window)
                self.assertEqual(entry["default_reasoning_level"], default_effort)
                self.assertEqual([item["effort"] for item in entry["supported_reasoning_levels"]], efforts)

        sol = mod.build_codex_model_entry("gpt-5.6-sol", template, 0)
        terra = mod.build_codex_model_entry("gpt-5.6-terra", template, 0)
        luna = mod.build_codex_model_entry("gpt-5.6-luna", template, 0)
        self.assertEqual(luna["visibility"], "hide")
        self.assertEqual(sol["multi_agent_version"], "v2")
        self.assertEqual(terra["multi_agent_version"], "v2")
        self.assertEqual(luna["multi_agent_version"], "v1")

    def test_codexplusplus_projection_updates_active_profile(self):
        mod = load_relay_gate()
        settings = {
            "activeRelayId": "relay-gate",
            "relayProfiles": [
                {"id": "other", "modelList": "old", "configContents": "model = \"old\"\n"},
                {"id": "relay-gate", "modelList": "old", "configContents": "model = \"glm-5.2\"\n"},
            ],
        }

        result = mod.project_codexplusplus_settings(settings, ["gpt-5.6-sol", "grok-4.5"])

        self.assertEqual(settings["relayProfiles"][0]["modelList"], "old")
        profile = settings["relayProfiles"][1]
        self.assertEqual(profile["modelList"], "gpt-5.6-sol\ngrok-4.5")
        self.assertIn('model_catalog_json = "cc-switch-model-catalog.json"', profile["configContents"])
        self.assertEqual(result["profile_id"], "relay-gate")
        self.assertEqual(result["repairs"], ["modelList", "configContents"])

    def test_codex_catalog_task_command_runs_relay_gate_directly(self):
        mod = load_relay_gate()

        command = mod.build_codex_catalog_task_action(
            executable=Path(r"D:\Python3.11.1\Scripts\relay-gate.exe"),
            log_path=Path(r"C:\Users\84618\AppData\Local\RelayGate\codex-catalog-sync.json"),
        )

        self.assertTrue(command.startswith('"D:\\Python3.11.1\\Scripts\\relay-gate.exe" --json codex-catalog sync --apply'))
        self.assertIn('--log-path "C:\\Users\\84618\\AppData\\Local\\RelayGate\\codex-catalog-sync.json"', command)
        self.assertNotIn("powershell", command.lower())
        self.assertNotIn("wscript", command.lower())
    def test_codex_catalog_sync_synthesizes_missing_models_and_updates_plus_settings(self):
        mod = load_relay_gate()
        template = {
            "slug": "gpt-5.5",
            "model": "gpt-5.5",
            "display_name": "GPT-5.5",
            "description": "template",
            "base_instructions": "base",
            "context_window": 256000,
            "max_context_window": 256000,
            "default_reasoning_level": "medium",
            "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}],
            "truncation_policy": {"mode": "tokens", "limit": 10000},
            "input_modalities": ["text", "image"],
            "supports_parallel_tool_calls": True,
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = root / "config.toml"
            catalog_path = root / "catalog.json"
            cache_path = root / "models_cache.json"
            plus_path = root / "settings.json"
            db_path = root / "cc-switch.db"
            pi_path = root / "pi-models.json"
            pi_cache_path = root / "pi-model-cache.json"
            codebuddy_path = root / "codebuddy-models.json"
            config_path.write_text('model = "gpt-5.5"\n[model_providers.custom]\nbase_url = "https://example.test/v1"\n', encoding="utf-8")
            catalog_path.write_text(json.dumps({"models": [template]}), encoding="utf-8")
            cache_path.write_text(json.dumps({"models": [template]}), encoding="utf-8")
            plus_path.write_text(json.dumps({"relayProfiles": [{"id": "relay", "modelList": "", "configContents": "model = \"gpt-5.5\"\n"}]}), encoding="utf-8")
            pi_path.write_text(json.dumps({"providers": {"newapi": {"models": [{"id": "gpt-5.5", "reasoning": True}], "apiKey": "pi-secret"}}}), encoding="utf-8")
            codebuddy_path.write_text(json.dumps({"models": [{"id": "gpt-5.5", "apiKey": "cb-secret", "url": "https://example.test/v1/chat/completions"}], "availableModels": ["gpt-5.5"]}), encoding="utf-8")
            db = sqlite3.connect(db_path)
            try:
                db.execute("create table providers (app_type text, is_current integer, settings_config text)")
                db.execute(
                    "insert into providers values (?, ?, ?)",
                    (
                        "codex",
                        1,
                        json.dumps({"config": 'model = "gpt-5.5"\n[model_providers.custom]\nbase_url = "https://example.test/v1"\n', "modelCatalog": {"models": [{"model": "gpt-5.6-sol", "displayName": "Sol local", "contextWindow": 1}]}}),
                    ),
                )
                db.commit()
            finally:
                db.close()

            emitted = []
            old_live = mod.live_newapi_model_ids
            old_emit = mod.emit_and_optionally_log
            try:
                mod.live_newapi_model_ids = lambda _args: ["gpt-5.5", "gpt-5.6-sol"]
                mod.emit_and_optionally_log = lambda data, as_json, json_log="": emitted.append(data)
                args = argparse.Namespace(
                    config_path=str(config_path),
                    catalog_path=str(catalog_path),
                    models_cache_path=str(cache_path),
                    codex_plus_plus_settings_path=str(plus_path),
                    cc_switch_db_path=str(db_path),
                    caller_token_cred="unused",
                    source="v1-models",
                    include_hidden=False,
                    include_disabled=False,
                    exclude_tag=[],
                    pin_first="gpt-5.5",
                    sync_codex_plus_plus=True,
                    sync_config=True,
                    sync_agent_models=True,
                    pi_models_path=str(pi_path),
                    pi_models_cache_path=str(pi_cache_path),
                    codebuddy_models_path=str(codebuddy_path),
                    log_path="",
                    dry_run=False,
                    apply=True,
                    json=True,
                )
                self.assertEqual(mod.command_codex_catalog_sync(args), 0)
            finally:
                mod.live_newapi_model_ids = old_live
                mod.emit_and_optionally_log = old_emit

            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            sol = next(item for item in catalog["models"] if item["slug"] == "gpt-5.6-sol")
            self.assertEqual(sol["context_window"], 372000)
            self.assertEqual([item["effort"] for item in sol["supported_reasoning_levels"]][-2:], ["max", "ultra"])
            self.assertEqual(sol["base_instructions"], "base")
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertIn("fetched_at", cache)
            self.assertIn("client_version", cache)
            self.assertEqual([item["slug"] for item in cache["models"]], ["gpt-5.5", "gpt-5.6-sol"])
            self.assertIn('model_catalog_json = "cc-switch-model-catalog.json"', config_path.read_text(encoding="utf-8"))
            db = sqlite3.connect(db_path)
            try:
                settings = json.loads(db.execute("select settings_config from providers where is_current=1").fetchone()[0])
            finally:
                db.close()
            self.assertIn('model_catalog_json = "cc-switch-model-catalog.json"', settings["config"])
            plus = json.loads(plus_path.read_text(encoding="utf-8"))
            self.assertEqual(plus["relayProfiles"][0]["modelList"], "gpt-5.5\ngpt-5.6-sol")
            self.assertTrue(emitted[0]["codex_plus_plus"]["written"])
            self.assertEqual([item["id"] for item in json.loads(pi_path.read_text(encoding="utf-8"))["providers"]["newapi"]["models"]], ["gpt-5.5", "gpt-5.6-sol"])
            self.assertEqual(json.loads(codebuddy_path.read_text(encoding="utf-8"))["availableModels"], ["gpt-5.5", "gpt-5.6-sol"])
            self.assertTrue(emitted[0]["agent_models"]["agents"]["pi"]["written"])

    def test_codex_catalog_task_parser_uses_cli_task_manager(self):
        mod = load_relay_gate()
        parser = mod.build_parser()

        args = parser.parse_args(["--json", "codex-catalog", "task", "install", "--interval-minutes", "7", "--dry-run"])

        self.assertEqual(args.func, mod.command_codex_catalog_task)
        self.assertEqual(args.task_action, "install")
        self.assertEqual(args.interval_minutes, 7)
        self.assertTrue(args.dry_run)
    def test_task_install_ends_existing_task_before_replace(self):
        mod = load_relay_gate()
        calls = []
        emitted = []

        class Result:
            returncode = 0
            stdout = "ok"
            stderr = ""

        old_run = mod.subprocess.run
        old_emit = mod.emit
        try:
            mod.subprocess.run = lambda command, **_kwargs: calls.append(command) or Result()
            mod.emit = lambda data, as_json: emitted.append(data)
            args = argparse.Namespace(
                task_action="install",
                task_name="CodexModelMenuCacheWatcher",
                interval_minutes=5,
                executable=r"D:\Python3.11.1\Scripts\relay-gate.exe",
                log_path=r"C:\Temp\catalog.json",
                dry_run=False,
                apply=True,
                json=True,
            )
            self.assertEqual(mod.command_codex_catalog_task(args), 0)
        finally:
            mod.subprocess.run = old_run
            mod.emit = old_emit

        self.assertEqual(calls[0][:4], ["schtasks.exe", "/End", "/TN", "CodexModelMenuCacheWatcher"])
        self.assertEqual(calls[1][0:2], ["schtasks.exe", "/Create"])
        self.assertEqual(emitted[0]["returncode"], 0)
    def test_emit_log_creates_parent_directory(self):
        mod = load_relay_gate()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "nested" / "sync.json"
            mod.emit_and_optionally_log({"ok": True}, False, str(path))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"ok": True})
    def test_codex_catalog_sync_does_not_rewrite_current_files(self):
        mod = load_relay_gate()
        template = {
            "slug": "gpt-5.5",
            "display_name": "GPT-5.5",
            "description": "template",
            "base_instructions": "base",
            "context_window": 256000,
            "max_context_window": 256000,
            "supported_reasoning_levels": [{"effort": "low"}],
            "truncation_policy": {"mode": "tokens", "limit": 10000},
        }
        current = mod.build_projected_codex_catalog({"models": [template]}, {"models": [template]}, ["gpt-5.5"], {})
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = root / "config.toml"
            catalog_path = root / "catalog.json"
            cache_path = root / "cache.json"
            catalog_path.write_text(json.dumps(current), encoding="utf-8")
            cache_path.write_text(json.dumps(mod.build_codex_models_cache(current)), encoding="utf-8")
            writes = []
            emitted = []
            old_live = mod.live_newapi_model_ids
            old_write = mod.write_json_atomic
            old_emit = mod.emit_and_optionally_log
            try:
                mod.live_newapi_model_ids = lambda _args: ["gpt-5.5"]
                mod.write_json_atomic = lambda path, payload: writes.append((path, payload))
                mod.emit_and_optionally_log = lambda data, as_json, json_log="": emitted.append(data)
                args = argparse.Namespace(
                    config_path=str(config_path), catalog_path=str(catalog_path), models_cache_path=str(cache_path),
                    codex_plus_plus_settings_path=str(root / "missing.json"), cc_switch_db_path=str(root / "missing.db"),
                    caller_token_cred="unused", source="v1-models", include_hidden=False, include_disabled=False,
                    exclude_tag=[], pin_first="gpt-5.5", sync_codex_plus_plus=False, sync_config=False, log_path="",
                    dry_run=False, apply=True, json=True,
                )
                self.assertEqual(mod.command_codex_catalog_sync(args), 0)
            finally:
                mod.live_newapi_model_ids = old_live
                mod.write_json_atomic = old_write
                mod.emit_and_optionally_log = old_emit

            self.assertEqual(writes, [])
            self.assertFalse(emitted[0]["written"])

    def test_codex_client_version_uses_resolved_codex_executable(self):
        mod = load_relay_gate()
        calls = []

        class Result:
            returncode = 0
            stdout = "OpenAI Codex v0.142.5\n"
            stderr = ""

        old_env = dict(mod.os.environ)
        old_run = mod.subprocess.run
        old_which = mod.shutil.which
        try:
            mod.os.environ.pop("CODEX_CLI_PATH", None)
            mod.shutil.which = lambda name: r"C:\Tools\codex.exe" if name == "codex" else None
            mod.subprocess.run = lambda command, **_kwargs: calls.append(command) or Result()
            self.assertEqual(mod.codex_client_version_triplet(), "0.142.5")
        finally:
            mod.subprocess.run = old_run
            mod.shutil.which = old_which
            mod.os.environ.clear()
            mod.os.environ.update(old_env)

        self.assertEqual(calls[0], [r"C:\Tools\codex.exe", "--version"])
    def test_codexplusplus_projection_excludes_quarantined_models(self):
        mod = load_relay_gate()
        settings = {"relayProfiles": [{"id": "relay", "modelList": "", "configContents": ""}]}

        selectable = mod.selectable_codex_model_ids(["gpt-5.6-sol", "gpt-5.6-luna", "grok-4.5"])
        mod.project_codexplusplus_settings(settings, selectable)

        self.assertEqual(selectable, ["gpt-5.6-sol", "grok-4.5"])
        self.assertEqual(settings["relayProfiles"][0]["modelList"], "gpt-5.6-sol\ngrok-4.5")
    def test_reveal_credential_requires_explicit_env_prefix_for_process_env(self):
        mod = load_relay_gate()

        old_env = dict(mod.os.environ)
        try:
            mod.os.environ["TOKEN_NAME"] = "process-secret"
            self.assertEqual(mod.reveal_credential("env:TOKEN_NAME"), "process-secret")
        finally:
            mod.os.environ.clear()
            mod.os.environ.update(old_env)

    def test_reveal_credential_or_env_honors_explicit_prefix(self):
        mod = load_relay_gate()
        calls = []

        def fake_run_sigil(args, *, input_text=None):
            calls.append(args)
            if args == ["env", "show", "TOKEN_NAME", "--reveal"]:
                return "sigil-secret"
            raise AssertionError(f"unexpected sigil call: {args}")

        old_env = dict(mod.os.environ)
        old_run_sigil = mod.run_sigil
        try:
            mod.os.environ["TOKEN_NAME"] = "process-secret"
            mod.run_sigil = fake_run_sigil

            self.assertEqual(mod.reveal_credential_or_env("env:TOKEN_NAME"), "process-secret")
            self.assertEqual(mod.reveal_credential_or_env("sigil-env:TOKEN_NAME"), "sigil-secret")
            self.assertEqual(calls, [["env", "show", "TOKEN_NAME", "--reveal"]])
        finally:
            mod.run_sigil = old_run_sigil
            mod.os.environ.clear()
            mod.os.environ.update(old_env)

    def test_healthy_existing_primary_is_preserved_when_another_channel_scores_higher(self):
        mod = load_relay_gate()
        args = argparse.Namespace(
            primary_priority=10,
            primary_weight=100,
            explore=True,

            explore_weight=1,
            min_explore_score=0.5,
            fallback_priority=5,
            fallback_weight=25,
            poor_priority=0,
            poor_weight=1,
            min_primary_score=0.58,
            min_fallback_score=0.42,
            target_group="default",
            require_responses_success=True,
        )

        proposal, reason = mod.channel_proposal_for_channel(
            {"status": 1, "priority": 10, "weight": 100},
            {
                "score": 0.62,
                "budget_blocked": False,
                "responses_protocol_blocked": False,
                "responses_success": 3,
                "last_outcome": "success",
                "freshness": 1.0,
                "test_success": False,
                "preserve_current_primary": True,
            },
            rank=3,
            probe=None,
            args=args,
        )

        self.assertEqual(reason, "tested_responses_success")
        self.assertEqual(proposal["priority"], 10)
        self.assertEqual(proposal["weight"], 4)

    def test_non_primary_does_not_take_primary_when_current_primary_is_healthy(self):
        mod = load_relay_gate()
        args = argparse.Namespace(
            primary_priority=10,
            primary_weight=100,
            explore=True,

            explore_weight=1,
            min_explore_score=0.5,
            fallback_priority=5,
            fallback_weight=25,
            poor_priority=0,
            poor_weight=1,
            min_primary_score=0.58,
            min_fallback_score=0.42,
            target_group="default",
            require_responses_success=True,
        )

        proposal, reason = mod.channel_proposal_for_channel(
            {"status": 1, "priority": 10, "weight": 1},
            {
                "score": 0.99,
                "budget_blocked": False,
                "responses_protocol_blocked": False,
                "responses_success": 3,
                "last_outcome": "success",
                "freshness": 1.0,
                "test_success": True,
                "primary_reserved_by_current": True,
            },
            rank=0,
            probe=None,
            args=args,
        )

        self.assertEqual(reason, "tested_responses_success")
        self.assertEqual(proposal["priority"], 10)
        self.assertEqual(proposal["weight"], 4)

    def test_responses_route_requires_responses_success_before_primary(self):
        mod = load_relay_gate()
        args = argparse.Namespace(
            primary_priority=10,
            primary_weight=100,
            explore=True,

            explore_weight=1,
            min_explore_score=0.5,
            fallback_priority=5,
            fallback_weight=25,
            poor_priority=0,
            poor_weight=1,
            min_primary_score=0.58,
            min_fallback_score=0.42,
            target_group="default",
            require_responses_success=True,
        )
        channel = {"status": 1, "priority": 10, "weight": 1}
        score_data = {
            "score": 0.99,
            "budget_blocked": False,
            "responses_protocol_blocked": False,
            "responses_success": 0,
            "last_outcome": "success",
            "freshness": 1.0,
            "test_success": True,
        }

        proposal, reason = mod.channel_proposal_for_channel(
            channel,
            score_data,
            rank=0,
            probe=None,
            args=args,
        )

        self.assertEqual(reason, "test_passed_but_responses_unverified")
        self.assertEqual(proposal["priority"], 5)
        self.assertEqual(proposal["weight"], 1)

    def test_responses_conversion_failure_marks_channel_protocol_incompatible(self):
        mod = load_relay_gate()
        logs = [
            {
                "type": 5,
                "created_at": 100,
                "channel": 5,
                "model_name": "gpt-5.5",
                "token_id": 1,
                "content": "status_code=500, not implemented",
                "other": {
                    "request_path": "/v1/responses",
                    "error_code": "convert_request_failed",
                    "status_code": 500,
                },
            }
        ]

        stats = mod.build_channel_log_stats(logs, ["gpt-5.5"])
        score_data = mod.score_channel(
            {"status": 1, "test_time": int(time.time()), "response_time": 800},
            stats[5],
            None,
            recent_window_seconds=1800,
        )
        args = argparse.Namespace(
            primary_priority=10,
            primary_weight=100,
            explore=True,

            explore_weight=1,
            min_explore_score=0.5,
            fallback_priority=5,
            fallback_weight=25,
            poor_priority=0,
            poor_weight=1,
            min_primary_score=0.58,
            min_fallback_score=0.42,
            target_group="default",
            require_responses_success=True,
        )

        proposal, reason = mod.channel_proposal_for_channel(
            {"status": 1, "priority": 10, "weight": 1},
            score_data,
            rank=0,
            probe={"success": True, "time": 0.8, "message": ""},
            args=args,
        )

        self.assertTrue(score_data["responses_protocol_blocked"])
        self.assertEqual(reason, "responses_protocol_incompatible")
        self.assertEqual(proposal["priority"], 0)
        self.assertEqual(proposal["weight"], 1)

    def test_probe_success_can_restore_exploration_after_stale_failure(self):
        mod = load_relay_gate()
        args = argparse.Namespace(
            primary_priority=10,
            primary_weight=100,
            explore=True,

            explore_weight=1,
            min_explore_score=0.5,
            fallback_priority=5,
            fallback_weight=25,
            poor_priority=0,
            poor_weight=1,
            min_primary_score=0.58,
            min_fallback_score=0.42,
            target_group="default",
            require_responses_success=False,
        )
        channel = {"status": 1, "priority": 0, "weight": 1}
        score_data = {"score": 0.1, "budget_blocked": False, "last_outcome": "failure", "freshness": 0.0}

        proposal, reason = mod.channel_proposal_for_channel(
            channel,
            score_data,
            rank=0,
            probe={"success": True, "time": 1.0, "message": ""},
            args=args,
        )

        self.assertEqual(reason, "tested_success")
        self.assertEqual(proposal["priority"], 10)
        self.assertEqual(proposal["weight"], 1)

    def test_recent_channel_test_restores_exploration_after_failed_history(self):
        mod = load_relay_gate()
        args = argparse.Namespace(
            primary_priority=10,
            primary_weight=100,
            explore=True,

            explore_weight=1,
            min_explore_score=0.5,
            fallback_priority=5,
            fallback_weight=25,
            poor_priority=0,
            poor_weight=1,
            min_primary_score=0.58,
            min_fallback_score=0.42,
            target_group="default",
            require_responses_success=False,
        )
        channel = {
            "status": 1,
            "priority": 0,
            "weight": 1,
            "test_time": int(time.time()),
            "response_time": 1956,
        }
        stats = {
            "success": 2,
            "failure": 3,
            "success_rate": 0.4,
            "avg_use_time": 3.5,
            "last_outcome": "failure",
            "age_seconds": 2076,
            "budget_errors": 0,
            "status_codes": {"502": 3},
            "last_error": "bad response status code 502",
        }

        score_data = mod.score_channel(channel, stats, None, recent_window_seconds=1800)
        proposal, reason = mod.channel_proposal_for_channel(
            channel,
            score_data,
            rank=7,
            probe=None,
            args=args,
        )

        self.assertGreaterEqual(score_data["score"], 0.5)
        self.assertTrue(score_data["test_success"])
        self.assertEqual(reason, "tested_success")
        self.assertEqual(proposal["priority"], 10)
        self.assertEqual(proposal["weight"], 1)

    def test_history_success_volume_increases_weight_inside_primary_priority(self):
        mod = load_relay_gate()
        args = argparse.Namespace(
            primary_priority=10,
            primary_weight=100,
            explore=True,

            explore_weight=1,
            min_explore_score=0.5,
            fallback_priority=5,
            fallback_weight=25,
            poor_priority=0,
            poor_weight=1,
            min_primary_score=0.58,
            min_fallback_score=0.42,
            target_group="default",
            require_responses_success=True,
        )

        proposal, reason = mod.channel_proposal_for_channel(
            {"status": 1, "priority": 10, "weight": 1},
            {
                "score": 0.95,
                "budget_blocked": False,
                "responses_protocol_blocked": False,
                "responses_success": 60,
                "failure": 0,
                "last_outcome": "success",
                "freshness": 1.0,
                "test_success": False,
            },
            rank=0,
            probe=None,
            args=args,
        )

        self.assertEqual(reason, "tested_responses_success")
        self.assertEqual(proposal["priority"], 10)
        self.assertGreater(proposal["weight"], 50)

    def test_optimizer_groups_candidates_by_exposed_model(self):
        mod = load_relay_gate()
        channels = [
            {
                "id": 4,
                "name": "nycatai",
                "status": 1,
                "models": "gpt-5.5",
                "priority": 10,
                "weight": 94,
                "tag": "openai",
            },
            {
                "id": 10,
                "name": "buddy-chicross",
                "status": 1,
                "models": "buddy-auto,buddy-glm5.2",
                "priority": 10,
                "weight": 40,
                "tag": "buddy",
            },
            {
                "id": 11,
                "name": "buddy-mcp",
                "status": 1,
                "models": "buddy-auto,buddy-glm5.2,buddy-hunyuan2t",
                "priority": 10,
                "weight": 30,
                "tag": "buddy",
            },
        ]
        logs = [
            {
                "type": 2,
                "created_at": 200,
                "channel": 10,
                "model_name": "buddy-glm5.2",
                "token_id": 1,
                "use_time": 1.0,
                "other": {"request_path": "/v1/responses"},
            },
            {
                "type": 2,
                "created_at": 201,
                "channel": 11,
                "model_name": "buddy-glm5.2",
                "token_id": 1,
                "use_time": 1.0,
                "other": {"request_path": "/v1/responses"},
            },
            {
                "type": 2,
                "created_at": 202,
                "channel": 4,
                "model_name": "gpt-5.5",
                "token_id": 1,
                "use_time": 1.0,
                "other": {"request_path": "/v1/responses"},
            },
        ]
        args = argparse.Namespace(
            channel_id=[],
            include_disabled=False,
            all_tags=True,
            tag=[],
            model=[],
            per_model=True,
            log_page_size=200,
            include_admin_tests=False,
            probe=False,
            recent_window_seconds=1800,
            primary_priority=10,
            primary_weight=100,
            explore=True,
            explore_weight=1,
            min_explore_score=0.5,
            fallback_priority=5,
            fallback_weight=25,
            poor_priority=0,
            poor_weight=1,
            min_primary_score=0.58,
            min_fallback_score=0.42,
            target_group="default",
            require_responses_success=True,
            apply_multi_model_channel=False,
            dry_run=True,
            apply=False,
        )

        old_fetch_channels = mod.fetch_existing_channels
        old_fetch_logs = mod.fetch_log_items
        try:
            mod.fetch_existing_channels = lambda _args: channels
            mod.fetch_log_items = lambda _args, *, page_size: logs

            result = mod.run_channel_optimization(args)
        finally:
            mod.fetch_existing_channels = old_fetch_channels
            mod.fetch_log_items = old_fetch_logs

        recs = result["recommendations"]
        self.assertEqual({item["model"] for item in recs}, {"buddy-glm5.2"})
        self.assertEqual({item["id"] for item in recs if item["model"] == "buddy-glm5.2"}, {10, 11})
        self.assertNotIn(4, {item["id"] for item in recs})
        self.assertIn(
            {"model": "gpt-5.5", "channel_id": 4, "name": "nycatai", "reason": "singleton_model"},
            result["skipped"],
        )
        self.assertIn(
            {"model": "buddy-auto", "channel_ids": [10, 11], "reason": "no_model_signal"},
            result["skipped"],
        )

    def test_optimizer_skips_shared_model_without_model_signal(self):
        mod = load_relay_gate()
        channels = [
            {"id": 10, "name": "buddy-a", "status": 1, "models": "buddy-glm5.2", "priority": 10, "weight": 40},
            {"id": 11, "name": "buddy-b", "status": 1, "models": "buddy-glm5.2", "priority": 10, "weight": 30},
        ]
        args = argparse.Namespace(
            channel_id=[],
            include_disabled=False,
            all_tags=True,
            tag=[],
            model=[],
            per_model=True,
            log_page_size=200,
            include_admin_tests=False,
            probe=False,
            recent_window_seconds=1800,
            primary_priority=10,
            primary_weight=100,
            explore=True,
            explore_weight=1,
            min_explore_score=0.5,
            fallback_priority=5,
            fallback_weight=25,
            poor_priority=0,
            poor_weight=1,
            min_primary_score=0.58,
            min_fallback_score=0.42,
            target_group="default",
            require_responses_success=True,
            dry_run=True,
            apply=False,
        )

        old_fetch_channels = mod.fetch_existing_channels
        old_fetch_logs = mod.fetch_log_items
        try:
            mod.fetch_existing_channels = lambda _args: channels
            mod.fetch_log_items = lambda _args, *, page_size: []

            result = mod.run_channel_optimization(args)
        finally:
            mod.fetch_existing_channels = old_fetch_channels
            mod.fetch_log_items = old_fetch_logs

        self.assertEqual(result["recommendations"], [])
        self.assertEqual(result["apply_proposals"], {})
        self.assertIn(
            {"model": "buddy-glm5.2", "channel_ids": [10, 11], "reason": "no_model_signal"},
            result["skipped"],
        )

    def test_per_model_optimizer_does_not_apply_multi_model_channel_weight(self):
        mod = load_relay_gate()
        channels = [
            {"id": 10, "name": "buddy-a", "status": 1, "models": "buddy-glm5.2,buddy-auto", "priority": 10, "weight": 40},
            {"id": 11, "name": "buddy-b", "status": 1, "models": "buddy-glm5.2,buddy-auto", "priority": 10, "weight": 30},
        ]
        logs = [
            {
                "type": 2,
                "created_at": 200,
                "channel": 10,
                "model_name": "buddy-glm5.2",
                "token_id": 1,
                "use_time": 1.0,
                "other": {"request_path": "/v1/responses"},
            },
            {
                "type": 5,
                "created_at": 201,
                "channel": 11,
                "model_name": "buddy-glm5.2",
                "token_id": 1,
                "content": "status_code=502",
                "other": {"request_path": "/v1/responses", "status_code": 502},
            },
        ]
        args = argparse.Namespace(
            channel_id=[],
            include_disabled=False,
            all_tags=True,
            tag=[],
            model=["buddy-glm5.2"],
            per_model=True,
            log_page_size=200,
            include_admin_tests=False,
            probe=False,
            recent_window_seconds=1800,
            primary_priority=10,
            primary_weight=100,
            explore=True,
            explore_weight=1,
            min_explore_score=0.5,
            fallback_priority=5,
            fallback_weight=25,
            poor_priority=0,
            poor_weight=1,
            min_primary_score=0.58,
            min_fallback_score=0.42,
            target_group="default",
            require_responses_success=True,
            dry_run=True,
            apply=False,
        )

        old_fetch_channels = mod.fetch_existing_channels
        old_fetch_logs = mod.fetch_log_items
        try:
            mod.fetch_existing_channels = lambda _args: channels
            mod.fetch_log_items = lambda _args, *, page_size: logs

            result = mod.run_channel_optimization(args)
        finally:
            mod.fetch_existing_channels = old_fetch_channels
            mod.fetch_log_items = old_fetch_logs

        self.assertEqual(result["apply_proposals"], {})
        self.assertEqual({item["id"] for item in result["recommendations"]}, {10, 11})
        self.assertEqual(
            {item["reason"] for item in result["apply_skipped"]},
            {"multi_model_channel_level_weight"},
        )

    def test_per_model_optimizer_can_apply_multi_model_channel_when_enabled(self):
        mod = load_relay_gate()
        channels = [
            {"id": 10, "name": "buddy-a", "status": 1, "models": "buddy-glm5.2,buddy-auto", "priority": 10, "weight": 40},
            {"id": 11, "name": "buddy-b", "status": 1, "models": "buddy-glm5.2,buddy-auto", "priority": 10, "weight": 30},
        ]
        logs = [
            {
                "type": 2,
                "created_at": 200,
                "channel": 10,
                "model_name": "buddy-glm5.2",
                "token_id": 1,
                "use_time": 1.0,
                "other": {"request_path": "/v1/responses"},
            },
            {
                "type": 5,
                "created_at": 201,
                "channel": 11,
                "model_name": "buddy-glm5.2",
                "token_id": 1,
                "content": "status_code=502",
                "other": {"request_path": "/v1/responses", "status_code": 502},
            },
        ]
        args = argparse.Namespace(
            channel_id=[],
            include_disabled=False,
            all_tags=True,
            tag=[],
            model=["buddy-glm5.2"],
            per_model=True,
            log_page_size=200,
            include_admin_tests=False,
            probe=False,
            recent_window_seconds=1800,
            primary_priority=10,
            primary_weight=100,
            explore=True,
            explore_weight=1,
            min_explore_score=0.5,
            fallback_priority=5,
            fallback_weight=25,
            poor_priority=0,
            poor_weight=1,
            min_primary_score=0.58,
            min_fallback_score=0.42,
            target_group="default",
            require_responses_success=True,
            apply_multi_model_channel=True,
            promote_on_recovery=False,
            dry_run=True,
            apply=False,
        )

        old_fetch_channels = mod.fetch_existing_channels
        old_fetch_logs = mod.fetch_log_items
        try:
            mod.fetch_existing_channels = lambda _args: channels
            mod.fetch_log_items = lambda _args, *, page_size: logs

            result = mod.run_channel_optimization(args)
        finally:
            mod.fetch_existing_channels = old_fetch_channels
            mod.fetch_log_items = old_fetch_logs

        self.assertEqual(result["apply_skipped"], [])
        self.assertEqual(set(result["apply_proposals"].keys()), {10, 11})


class ResponsesBridgePolicyTest(unittest.TestCase):
    def test_merge_responses_bridge_policy_preserves_existing_scope(self):
        mod = load_relay_gate()
        current = {
            "enabled": True,
            "all_channels": False,
            "channel_ids": [5, 6, 8, 9],
            "model_patterns": ["^glm-5\\.2$"],
        }

        policy, changes = mod.merge_responses_bridge_policy(
            current,
            channel_ids=[10, 9],
            model_patterns=["^buddy-.*$", "^glm-5\\.2$"],
        )

        self.assertTrue(policy["enabled"])
        self.assertFalse(policy["all_channels"])
        self.assertEqual(policy["channel_ids"], [5, 6, 8, 9, 10])
        self.assertEqual(policy["model_patterns"], ["^glm-5\\.2$", "^buddy-.*$"])
        self.assertEqual(
            changes,
            [
                {"field": "channel_ids", "added": [10]},
                {"field": "model_patterns", "added": ["^buddy-.*$"]},
            ],
        )

    def test_responses_bridge_ensure_updates_newapi_option(self):
        mod = load_relay_gate()
        emitted = []

        def fake_api_request(args, method, path, *, json_body=None, params=None):
            if method == "GET" and path == "/api/option/":
                return {
                    "data": [
                        {
                            "key": "global.responses_to_chat_completions_policy",
                            "value": '{"enabled":true,"all_channels":false,"channel_ids":[5,6,8,9],"model_patterns":["^glm-5\\\\.2$"]}',
                        }
                    ]
                }
            if method == "PUT" and path == "/api/option/":
                self.assertEqual(json_body["key"], "global.responses_to_chat_completions_policy")
                stored = json.loads(json_body["value"])
                self.assertEqual(stored["channel_ids"], [5, 6, 8, 9, 10])
                self.assertEqual(stored["model_patterns"], ["^glm-5\\.2$", "^buddy-.*$"])
                return {"success": True}
            raise AssertionError(f"unexpected api call: {method} {path}")

        old_api_request = mod.api_request
        old_emit = mod.emit
        try:
            mod.api_request = fake_api_request
            mod.emit = lambda data, as_json: emitted.append(data)
            args = argparse.Namespace(channel_id=[10], model_pattern=["^buddy-.*$"], dry_run=False, apply=True, json=True)

            self.assertEqual(mod.command_responses_bridge_ensure(args), 0)
            self.assertEqual(emitted[0]["changes"], [{"field": "channel_ids", "added": [10]}, {"field": "model_patterns", "added": ["^buddy-.*$"]}])
        finally:
            mod.api_request = old_api_request
            mod.emit = old_emit


class AliasMappingTest(unittest.TestCase):
    def test_text_null_model_mapping_is_treated_as_empty(self):
        mod = load_relay_gate()

        self.assertIsNone(mod.parse_model_mapping_value("null"))

    def test_channel_create_payload_preserves_explicit_model_mapping(self):
        mod = load_relay_gate()
        args = argparse.Namespace(
            type="openai",
            models="gpt-5.5,gpt-5.4",
            model_mapping="gpt-5.5=deepseek-chat,gpt-5.4=deepseek-reasoner",
            group="default",
            tag="new-provider",
            priority=0,
            weight=1,
            mode="single",
            multi_key_mode="random",
            test_model="gpt-5.5",
            remark="",
            keep_v1=False,
            base_url_value="https://example.com/v1",
            name="mixed-provider",
            other=None,
            auto_ban=1,
            status=1,
        )

        payload = mod.build_channel_create_payload(args, "sk-test")
        channel = payload["channel"]

        self.assertEqual(channel["models"], "gpt-5.5,gpt-5.4")
        self.assertEqual(channel["test_model"], "gpt-5.5")
        self.assertEqual(
            mod.parse_model_mapping_value(channel["model_mapping"]),
            {"gpt-5.5": "deepseek-chat", "gpt-5.4": "deepseek-reasoner"},
        )


if __name__ == "__main__":
    unittest.main()


class PromoteOnRecoveryTest(unittest.TestCase):
    """Lock the promote-on-recovery decision logic so future edits cannot regress
    the same-model max-priority alignment without an explicit test update."""

    def _make_channel(self, *, cid, name, priority, status, models, test_model=""):
        return {
            "id": cid,
            "name": name,
            "priority": priority,
            "status": status,
            "models": models,
            "test_model": test_model,
        }

    def test_promotes_low_priority_channel_when_probe_passes(self):
        mod = load_relay_gate()
        ns = argparse.Namespace(
            promote_on_recovery=True,
            promote_probe_prompt="ping",
            promote_probe_max_tokens=8,
            caller_token_cred="dummy",
            base_url="http://example",
            timeout=5,
            proxy_url="",
            user_id="1",
            admin_token_cred="dummy",
        )
        channels = [
            self._make_channel(cid=1, name="primary", priority=10, status=1, models="a,b"),
            self._make_channel(cid=2, name="recovering", priority=0, status=1, models="a", test_model="a"),
        ]
        old_probe = mod.sse_probe_via_relay
        try:
            mod.sse_probe_via_relay = lambda *a, **k: {"ok": True, "http_status": 200, "latency_ms": 100, "first_token_ms": 50, "finish_reason": "stop"}
            proposals, audits = mod._promote_on_recovery(ns, channels)
        finally:
            mod.sse_probe_via_relay = old_probe
        self.assertEqual(proposals, {2: {"priority": 10}})
        self.assertEqual(len(audits), 1)
        self.assertTrue(audits[0]["promoted"])
        self.assertEqual(audits[0]["target_priority"], 10)

    def test_promote_on_recovery_skips_multi_model_channel_without_explicit_apply(self):
        mod = load_relay_gate()
        ns = argparse.Namespace(
            promote_on_recovery=True,
            promote_probe_prompt="ping",
            promote_probe_max_tokens=8,
            caller_token_cred="dummy",
            base_url="http://example",
            timeout=5,
            proxy_url="",
            user_id="1",
            admin_token_cred="dummy",
            primary_priority=10,
            apply_multi_model_channel=False,
        )
        channels = [
            self._make_channel(cid=1, name="primary", priority=10, status=1, models="a,b"),
            self._make_channel(cid=2, name="recovering", priority=0, status=1, models="a,b", test_model="a"),
        ]
        old_probe = mod.sse_probe_via_relay
        try:
            mod.sse_probe_via_relay = lambda *a, **k: {"ok": True, "http_status": 200, "latency_ms": 100, "first_token_ms": 50, "finish_reason": "stop"}
            proposals, audits = mod._promote_on_recovery(ns, channels)
        finally:
            mod.sse_probe_via_relay = old_probe
        self.assertEqual(proposals, {})
        self.assertEqual(len(audits), 1)
        self.assertFalse(audits[0]["promoted"])
        self.assertEqual(audits[0]["reason"], "multi_model_channel_level_priority")

    def test_does_not_promote_when_probe_fails(self):
        mod = load_relay_gate()
        ns = argparse.Namespace(
            promote_on_recovery=True,
            promote_probe_prompt="ping",
            promote_probe_max_tokens=8,
            caller_token_cred="dummy",
            base_url="http://example",
            timeout=5,
            proxy_url="",
            user_id="1",
            admin_token_cred="dummy",
        )
        channels = [
            self._make_channel(cid=1, name="primary", priority=10, status=1, models="a"),
            self._make_channel(cid=2, name="recovering", priority=0, status=1, models="a"),
        ]
        old_probe = mod.sse_probe_via_relay
        try:
            mod.sse_probe_via_relay = lambda *a, **k: {"ok": False, "http_status": 500, "latency_ms": 100, "error": "boom"}
            proposals, audits = mod._promote_on_recovery(ns, channels)
        finally:
            mod.sse_probe_via_relay = old_probe
        self.assertEqual(proposals, {})
        self.assertEqual(len(audits), 1)
        self.assertFalse(audits[0]["promoted"])

    def test_promotes_singleton_model_channel_to_primary_priority(self):
        """A channel exposing a model with no same-model competition should
        still be lifted to primary_priority when its probe passes, so that
        hermes-imported singleton channels are not stuck at priority=0."""
        mod = load_relay_gate()
        ns = argparse.Namespace(
            promote_on_recovery=True,
            promote_probe_prompt="ping",
            promote_probe_max_tokens=8,
            caller_token_cred="dummy",
            base_url="http://example",
            timeout=5,
            proxy_url="",
            user_id="1",
            admin_token_cred="dummy",
            primary_priority=10,
        )
        channels = [
            self._make_channel(cid=1, name="primary", priority=10, status=1, models="a"),
            self._make_channel(cid=2, name="alone", priority=0, status=1, models="z"),
        ]
        old_probe = mod.sse_probe_via_relay
        try:
            mod.sse_probe_via_relay = lambda *a, **k: {"ok": True, "http_status": 200, "latency_ms": 100, "finish_reason": "stop"}
            proposals, audits = mod._promote_on_recovery(ns, channels)
        finally:
            mod.sse_probe_via_relay = old_probe
        self.assertEqual(proposals, {2: {"priority": 10}})
        self.assertEqual(len(audits), 1)
        self.assertTrue(audits[0]["promoted"])
        self.assertEqual(audits[0]["target_priority"], 10)

    def test_disabled_when_flag_off(self):
        mod = load_relay_gate()
        ns = argparse.Namespace(
            promote_on_recovery=False,
            base_url="http://example",
            timeout=5,
            proxy_url="",
            user_id="1",
            admin_token_cred="dummy",
        )
        channels = [
            self._make_channel(cid=1, name="primary", priority=10, status=1, models="a"),
            self._make_channel(cid=2, name="recovering", priority=0, status=1, models="a"),
        ]
        proposals, audits = mod._promote_on_recovery(ns, channels)
        self.assertEqual(proposals, {})
        self.assertEqual(audits, [])


class ChannelMaintenanceTest(unittest.TestCase):
    def test_channel_log_stats_parses_json_string_other(self):
        mod = load_relay_gate()
        logs = [
            {
                "id": 1,
                "created_at": 100,
                "channel": 10,
                "channel_name": "buddy-chicross",
                "model_name": "buddy-glm5.2",
                "use_time": 61,
                "other": json.dumps(
                    {
                        "stream_status": {
                            "status": "error",
                            "end_reason": "scanner_error",
                            "end_error": "stream error: stream ID 1; INTERNAL_ERROR; received from peer",
                        }
                    }
                ),
            }
        ]
        stats = mod.channel_log_stats(logs, now=120, window_seconds=60)
        self.assertEqual(stats[10]["scanner_error"], 1)
        self.assertEqual(stats[10]["total"], 1)

    def test_channels_maintain_parser_accepts_json_log_without_wrapper_script(self):
        mod = load_relay_gate()
        parser = mod.build_parser()
        args = parser.parse_args([
            "--json",
            "--base-url",
            "http://127.0.0.1:3000",
            "--admin-token-cred",
            "process-env:NEWAPI_ADMIN_TOKEN",
            "channels",
            "maintain",
            "--caller-token-cred",
            "process-env:NEWAPI_CALLER_TOKEN",
            "--apply",
            "--json-log",
            "/tmp/last-run.json",
        ])

        self.assertEqual(args.func, mod.command_channels_maintain)
        self.assertEqual(args.admin_token_cred, "process-env:NEWAPI_ADMIN_TOKEN")
        self.assertEqual(args.caller_token_cred, "process-env:NEWAPI_CALLER_TOKEN")
        self.assertEqual(args.json_log, "/tmp/last-run.json")
        self.assertTrue(args.apply)
        self.assertFalse(args.dry_run)

    def test_maintenance_weight_adjustments_run_in_light_round(self):
        mod = load_relay_gate()
        weight_audits = []

        def fake_options(_args):
            return []

        def fake_hold_quota(_args):
            return {"dry_run": False, "results": []}

        def fake_stabilize(args):
            weight_audits.append(args)
            return [{"id": 1, "name": "test", "current_weight": 10, "target_weight": 10, "reason": "unchanged", "applied": False}]

        old_options = mod.set_soft_disable_profile
        old_hold_quota = mod.hold_quota_channels
        old_stabilize = mod.stabilize_channel_weights
        try:
            mod.set_soft_disable_profile = fake_options
            mod.hold_quota_channels = fake_hold_quota
            mod.stabilize_channel_weights = fake_stabilize
            result = mod.run_channel_maintenance(
                argparse.Namespace(
                    base_url="http://127.0.0.1:3000",
                    dry_run=False, apply=True,
                    log_page_size=100, log_pages=10,
                    recent_window_seconds=21600,
                    primary_priority=10, primary_weight=100,
                    promote_probe_prompt="ping", promote_probe_max_tokens=8,
                )
            )
        finally:
            mod.set_soft_disable_profile = old_options
            mod.hold_quota_channels = old_hold_quota
            mod.stabilize_channel_weights = old_stabilize

        self.assertEqual(len(weight_audits), 1)
        self.assertIn("weight_adjustments", result)
        self.assertNotIn("optimizer", result)
        self.assertNotIn("buddy_stability", result)
        self.assertEqual(result["recover"]["skipped"], True)

    def test_recover_disabled_requires_stream_probe_success(self):
        mod = load_relay_gate()
        updates = []
        probes = []

        def fake_fetch(_args):
            return [{"id": 1, "name": "beeapi", "status": 3}]

        def fake_api(_args, method, path, *, json_body=None, params=None):
            if method == "GET" and path == "/api/channel/1":
                return {"data": {"id": 1, "name": "beeapi", "status": 3, "test_model": "gpt-5.5", "other_info": ""}}
            if method == "PUT" and path == "/api/channel/":
                updates.append(json_body)
                return {"success": True, "message": ""}
            raise AssertionError((method, path, params))

        def fake_probe(_args, channel_id, model, *, stream=False):
            probes.append({"channel_id": channel_id, "model": model, "stream": stream})
            return {"ok": False, "message": "still unavailable", "stream": stream}

        old_fetch = mod.fetch_existing_channels
        old_api = mod.api_request
        old_probe = mod.channel_test_via_newapi
        try:
            mod.fetch_existing_channels = fake_fetch
            mod.api_request = fake_api
            mod.channel_test_via_newapi = fake_probe
            result = mod.recover_channels(argparse.Namespace(channel_id=[1], model="", dry_run=False, apply=True))
        finally:
            mod.fetch_existing_channels = old_fetch
            mod.api_request = old_api
            mod.channel_test_via_newapi = old_probe

        self.assertEqual(probes, [{"channel_id": 1, "model": "gpt-5.5", "stream": True}])
        self.assertEqual(result["results"][0]["reason"], "probe_failed")
        self.assertEqual(updates, [])

    def test_recover_quota_hold_but_not_plain_manual_disable(self):
        mod = load_relay_gate()
        updates = []
        probes = []

        def fake_fetch(_args):
            return [
                {"id": 10, "name": "buddy-chicross", "status": 2},
                {"id": 12, "name": "manually-off", "status": 2},
            ]

        def fake_api(_args, method, path, *, json_body=None, params=None):
            if method == "GET" and path == "/api/channel/10":
                return {
                    "data": {
                        "id": 10,
                        "name": "buddy-chicross",
                        "status": 2,
                        "test_model": "buddy-glm5.2",
                        "other_info": json.dumps({"relay_gate_quota_hold": {"reason": mod.QUOTA_HOLD_REASON}}),
                    }
                }
            if method == "GET" and path == "/api/channel/12":
                return {"data": {"id": 12, "name": "manually-off", "status": 2, "other_info": ""}}
            if method == "PUT" and path == "/api/channel/":
                updates.append(json_body)
                return {"success": True, "message": ""}
            if method == "POST" and path.startswith("/api/channel/") and path.endswith("/status"):
                return {"success": True, "message": ""}
            raise AssertionError((method, path, params))

        def fake_probe(_args, channel_id, model, *, stream=False):
            probes.append({"channel_id": channel_id, "model": model, "stream": stream})
            return {"ok": True, "message": "", "time": 1.2, "stream": stream}

        old_fetch = mod.fetch_existing_channels
        old_api = mod.api_request
        old_probe = mod.channel_test_via_newapi
        try:
            mod.fetch_existing_channels = fake_fetch
            mod.api_request = fake_api
            mod.channel_test_via_newapi = fake_probe
            result = mod.recover_channels(argparse.Namespace(channel_id=[], model="", dry_run=False, apply=True))
        finally:
            mod.fetch_existing_channels = old_fetch
            mod.api_request = old_api
            mod.channel_test_via_newapi = old_probe

        self.assertEqual(probes, [{"channel_id": 10, "model": "buddy-glm5.2", "stream": True}])
        self.assertEqual([item["reason"] for item in result["results"]], ["probe_passed_recovered", "manual_disabled_not_quota_hold"])
        self.assertEqual(len(updates), 1)
        self.assertNotIn("status", updates[0])
        self.assertNotIn("relay_gate_quota_hold", json.loads(updates[0]["other_info"]))

    def test_hold_quota_can_use_direct_probe_without_recent_log(self):
        mod = load_relay_gate()
        updates = []
        probes = []

        def fake_fetch(_args):
            return [{"id": 10, "name": "buddy-chicross", "status": 2, "tag": "buddy"}]

        def fake_api(_args, method, path, *, json_body=None, params=None):
            if method == "GET" and path == "/api/channel/10":
                return {
                    "data": {
                        "id": 10,
                        "name": "buddy-chicross",
                        "status": 2,
                        "tag": "buddy",
                        "test_model": "buddy-auto",
                        "models": "buddy-auto,buddy-glm5.2",
                        "other_info": json.dumps({"relay_gate": {"protocol_hint": "sse-only"}}),
                    }
                }
            if method == "PUT" and path == "/api/channel/":
                updates.append(json_body)
                return {"success": True, "message": ""}
            if method == "POST" and path.startswith("/api/channel/") and path.endswith("/status"):
                return {"success": True, "message": ""}
            raise AssertionError((method, path, params))

        def fake_probe(_args, channel_id, model, *, stream=False):
            probes.append({"channel_id": channel_id, "model": model, "stream": stream})
            return {"ok": False, "message": "额度已用尽，请充值后重试", "stream": stream}

        old_fetch = mod.fetch_existing_channels
        old_api = mod.api_request
        old_probe = mod.channel_test_via_newapi
        try:
            mod.fetch_existing_channels = fake_fetch
            mod.api_request = fake_api
            mod.channel_test_via_newapi = fake_probe
            result = mod.hold_quota_channels(argparse.Namespace(channel_id=[10], model="", from_logs=False, dry_run=False, apply=True))
        finally:
            mod.fetch_existing_channels = old_fetch
            mod.api_request = old_api
            mod.channel_test_via_newapi = old_probe

        self.assertEqual(probes, [{"channel_id": 10, "model": "buddy-glm5.2", "stream": True}])
        self.assertTrue(result["results"][0]["ok"])
        self.assertEqual(len(updates), 1)
        self.assertNotIn("status", updates[0])
        self.assertEqual(json.loads(updates[0]["other_info"])["relay_gate_quota_hold"]["reason"], mod.QUOTA_HOLD_REASON)

    def test_quota_detector_handles_escaped_chinese_error_payload(self):
        mod = load_relay_gate()
        message = r'upstream error: {"data":{"code":14018,"msg":"\u989d\u5ea6\u5df2\u7528\u5c3d\uff0c\u8bf7\u5145\u503c\u540e\u91cd\u8bd5"}}'
        self.assertTrue(mod.is_quota_exhausted_message(message))

    def test_agent_visible_model_ids_follow_catalog_visibility(self):
        mod = load_relay_gate()
        catalog = {
            "models": [
                {"slug": "gpt-5.6-sol", "visibility": "list"},
                {"slug": "gpt-5.6-luna", "visibility": "hide"},
                {"slug": "grok-4.5"},
            ]
        }

        self.assertEqual(mod.agent_visible_model_ids(catalog), ["gpt-5.6-sol", "grok-4.5"])

    def test_sync_pi_models_reconciles_newapi_only_and_preserves_provider_config(self):
        mod = load_relay_gate()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "models.json"
            path.write_text(json.dumps({
                "providers": {
                    "chat2api": {
                        "api": "openai-completions",
                        "apiKey": "chat-secret",
                        "models": [{"id": "keep-chat", "contextWindow": 10, "maxTokens": 2}],
                    },
                    "newapi": {
                        "api": "openai-completions",
                        "apiKey": "newapi-secret",
                        "baseUrl": "https://example.test/v1",
                        "models": [
                            {"id": "retired-model", "reasoning": False, "contextWindow": 10, "maxTokens": 2},
                            {"id": "gpt-5.5", "reasoning": True, "contextWindow": 20, "maxTokens": 4},
                        ],
                    },
                }
            }), encoding="utf-8")
            old_resolve = mod.ContextMeta.resolve
            try:
                mod.ContextMeta.resolve = classmethod(
                    lambda cls, model_id, for_codex=False: ((256000, 128000) if model_id == "gpt-5.5" else (372000, 128000))
                )
                result = mod._sync_pi_models(path, ["gpt-5.5", "gpt-5.6-sol"], True)
            finally:
                mod.ContextMeta.resolve = old_resolve

            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual([item["id"] for item in saved["providers"]["chat2api"]["models"]], ["keep-chat"])
            self.assertEqual(saved["providers"]["newapi"]["apiKey"], "newapi-secret")
            self.assertEqual(saved["providers"]["newapi"]["baseUrl"], "https://example.test/v1")
            self.assertEqual([item["id"] for item in saved["providers"]["newapi"]["models"]], ["gpt-5.5", "gpt-5.6-sol"])
            self.assertEqual(saved["providers"]["newapi"]["models"][1]["contextWindow"], 372000)
            self.assertTrue(saved["providers"]["newapi"]["models"][1]["reasoning"])
            self.assertEqual(result["added"], ["gpt-5.6-sol"])
            self.assertEqual(result["removed"], ["retired-model"])
            self.assertNotIn("newapi-secret", json.dumps(result))

    def test_sync_pi_models_refreshes_servitor_model_cache(self):
        mod = load_relay_gate()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "models.json"
            cache_path = root / "pi_models.json"
            path.write_text(json.dumps({
                "providers": {
                    "chat2api": {"models": [{"id": "keep-chat"}]},
                    "newapi": {"models": [{"id": "gpt-5.5", "reasoning": True}]},
                }
            }), encoding="utf-8")
            cache_path.write_text(json.dumps(["newapi/retired-model"]), encoding="utf-8")
            old_resolve = mod.ContextMeta.resolve
            try:
                mod.ContextMeta.resolve = classmethod(lambda cls, model_id, for_codex=False: (256000, 128000))
                result = mod._sync_pi_models(path, ["gpt-5.5"], True, cache_path=cache_path)
            finally:
                mod.ContextMeta.resolve = old_resolve

            self.assertEqual(
                json.loads(cache_path.read_text(encoding="utf-8")),
                ["chat2api/keep-chat", "newapi/gpt-5.5"],
            )
            self.assertTrue(result["cache_written"])

    def test_sync_codebuddy_models_reconciles_allowed_set_and_preserves_credentials(self):
        mod = load_relay_gate()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "models.json"
            path.write_text(json.dumps({
                "models": [{
                    "id": "gpt-5.5",
                    "name": "GPT-5.5",
                    "vendor": "OpenAI",
                    "apiKey": "codebuddy-secret",
                    "url": "https://example.test/v1/chat/completions",
                    "maxInputTokens": 20,
                    "maxOutputTokens": 4,
                    "supportsToolCall": True,
                    "supportsImages": True,
                    "supportsReasoning": True,
                }],
                "availableModels": ["gpt-5.5"],
            }), encoding="utf-8")
            old_resolve = mod.ContextMeta.resolve
            try:
                mod.ContextMeta.resolve = classmethod(
                    lambda cls, model_id, for_codex=False: ((256000, 128000) if model_id == "gpt-5.5" else (372000, 128000))
                )
                result = mod._sync_codebuddy_models(path, ["gpt-5.5", "gpt-5.6-sol"], True)
            finally:
                mod.ContextMeta.resolve = old_resolve

            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["availableModels"], ["gpt-5.5", "gpt-5.6-sol"])
            self.assertEqual([item["id"] for item in saved["models"]], saved["availableModels"])
            self.assertEqual(saved["models"][1]["apiKey"], "codebuddy-secret")
            self.assertEqual(saved["models"][1]["url"], "https://example.test/v1/chat/completions")
            self.assertEqual(saved["models"][1]["vendor"], "OpenAI")
            self.assertEqual(saved["models"][1]["maxInputTokens"], 372000)
            self.assertEqual(result["added"], ["gpt-5.6-sol"])
            self.assertNotIn("codebuddy-secret", json.dumps(result))

    def test_sync_codebuddy_models_repairs_available_models_only(self):
        mod = load_relay_gate()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "models.json"
            path.write_text(json.dumps({
                "models": [{
                    "id": "gpt-5.5",
                    "name": "GPT-5.5",
                    "vendor": "OpenAI",
                    "apiKey": "codebuddy-secret",
                    "url": "https://example.test/v1/chat/completions",
                    "maxInputTokens": 256000,
                    "maxOutputTokens": 128000,
                    "supportsToolCall": True,
                    "supportsImages": True,
                    "supportsReasoning": True,
                }],
                "availableModels": [],
            }), encoding="utf-8")
            old_resolve = mod.ContextMeta.resolve
            try:
                mod.ContextMeta.resolve = classmethod(lambda cls, model_id, for_codex=False: (256000, 128000))
                result = mod._sync_codebuddy_models(path, ["gpt-5.5"], True)
            finally:
                mod.ContextMeta.resolve = old_resolve

            self.assertTrue(result["written"])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["availableModels"], ["gpt-5.5"])
