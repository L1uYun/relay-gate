import argparse
import importlib.util
import json
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
        self.assertEqual(updates[0]["status"], 1)
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
        self.assertEqual(updates[0]["status"], 2)
        self.assertEqual(json.loads(updates[0]["other_info"])["relay_gate_quota_hold"]["reason"], mod.QUOTA_HOLD_REASON)

    def test_quota_detector_handles_escaped_chinese_error_payload(self):
        mod = load_relay_gate()
        message = r'upstream error: {"data":{"code":14018,"msg":"\u989d\u5ea6\u5df2\u7528\u5c3d\uff0c\u8bf7\u5145\u503c\u540e\u91cd\u8bd5"}}'
        self.assertTrue(mod.is_quota_exhausted_message(message))
