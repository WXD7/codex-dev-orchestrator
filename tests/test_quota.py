from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from orchestrator.agents import AgentRegistry
from orchestrator.models import AgentRunResult, PreflightResult
from orchestrator.quota import (
    CodexQuotaProbe,
    QuotaCache,
    QuotaSnapshot,
    QuotaWindow,
    choose_model_tier,
    merge_claude_rate_limit,
    normalize_codex_snapshot,
    quota_mode,
)


def snapshot(name, used, reset_in=3600, reached=False):
    return QuotaSnapshot(
        executor=name,
        plan="pro",
        windows=(
            QuotaWindow(
                "main",
                used,
                resets_at=int(time.time()) + reset_in,
                window_minutes=10080,
                reached=reached,
            ),
        ),
        source="test",
        confidence="high",
    )


class QuotaExecutor:
    def __init__(self, name, quota):
        self.name = name
        self.label = name
        self.quota = quota
        self.last_model = ""

    def preflight(self):
        return PreflightResult(True, "v1", "subscription", [])

    def quota_snapshot(self, force=False):
        return self.quota

    def model_for(self, tier):
        return "%s-%s" % (self.name, tier)

    def run(self, run_id, role, worktree, prompt, session_id, on_event, model=""):
        self.last_model = model
        return AgentRunResult(0, "complete", {"outcome": "completed"})


class QuotaPolicyTests(unittest.TestCase):
    def test_codex_probe_keeps_transport_open_for_async_rate_limit_response(self):
        with tempfile.TemporaryDirectory() as temp:
            binary = Path(temp) / "fake-codex"
            binary.write_text(
                """#!/usr/bin/env python3
import json
import sys
import threading
import time

def emit(value):
    print(json.dumps(value), flush=True)

def emit_limits():
    time.sleep(0.1)
    emit({"id": 3, "result": {"rateLimits": {
        "limitId": "codex",
        "primary": {"usedPercent": 27, "windowDurationMins": 10080,
                    "resetsAt": 1787330770},
        "planType": "pro"
    }}})

for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if method == "initialize":
        emit({"id": 1, "result": {"userAgent": "fake"}})
    elif method == "account/read":
        emit({"id": 2, "result": {"account": {
            "type": "chatgpt", "planType": "pro"
        }}})
    elif method == "account/rateLimits/read":
        threading.Thread(target=emit_limits, daemon=True).start()
""",
                encoding="utf-8",
            )
            binary.chmod(0o700)
            result = CodexQuotaProbe(
                str(binary), ttl_seconds=5, timeout_seconds=5
            ).read(force=True)

        self.assertTrue(result.observed)
        self.assertEqual(result.plan, "pro")
        self.assertEqual(result.remaining_percent, 73)

    def test_codex_app_server_payload_is_normalized(self):
        result = normalize_codex_snapshot(
            {"account": {"type": "chatgpt", "planType": "pro"}},
            {
                "rateLimits": {
                    "limitId": "codex",
                    "primary": {
                        "usedPercent": 26,
                        "windowDurationMins": 10080,
                        "resetsAt": 1787330770,
                    },
                    "planType": "pro",
                },
                "rateLimitsByLimitId": {
                    "codex": {
                        "primary": {
                            "usedPercent": 26,
                            "windowDurationMins": 10080,
                            "resetsAt": 1787330770,
                        }
                    },
                    "codex_bengalfox": {
                        "limitName": "GPT-5.3-Codex-Spark",
                        "primary": {
                            "usedPercent": 0,
                            "windowDurationMins": 300,
                            "resetsAt": 1787316346,
                        },
                    },
                },
            },
        )
        self.assertEqual(result.plan, "pro")
        self.assertEqual(result.remaining_percent, 74)
        self.assertEqual(len(result.buckets), 2)
        self.assertEqual(result.buckets[1]["label"], "GPT-5.3-Codex-Spark")

    def test_refresh_proximity_relaxes_the_policy_by_one_band(self):
        now = int(time.time())
        quota = QuotaSnapshot(
            executor="codex",
            windows=(QuotaWindow("weekly", 90, resets_at=now + 10 * 60),),
        )
        self.assertEqual(quota_mode(quota, now=now), "cautious")

    def test_expired_reached_window_becomes_unknown_instead_of_blocking_forever(self):
        now = int(time.time())
        expired = QuotaSnapshot(
            executor="claude-code",
            windows=(
                QuotaWindow(
                    "five_hour", 100, resets_at=now - 60, reached=True
                ),
            ),
            source="claude-rate-limit-event",
            confidence="high",
        )
        self.assertEqual(quota_mode(expired, now=now), "cautious")

        claude = QuotaExecutor("claude-code", expired)
        registry = AgentRegistry([claude], "claude-code")
        registry.preflight()
        decision = registry.select({"role": "implementer"}, {})
        self.assertFalse(decision.blocked)
        self.assertFalse(decision.quota.observed)

    def test_role_and_quota_choose_model_tier(self):
        self.assertEqual(choose_model_tier(snapshot("codex", 20), "planner"), "high")
        self.assertEqual(choose_model_tier(snapshot("codex", 90), "planner"), "economy")
        self.assertEqual(choose_model_tier(snapshot("codex", 20), "qa"), "balanced")

    def test_richer_executor_wins_unless_human_pins_one(self):
        codex = QuotaExecutor("codex", snapshot("codex", 20))
        claude = QuotaExecutor("claude-code", snapshot("claude-code", 90))
        registry = AgentRegistry([codex, claude], "claude-code", quota_aware=True)
        registry.preflight()

        automatic = registry.select({"role": "planner", "priority": 50}, {})
        self.assertEqual(automatic.executor, "codex")
        self.assertEqual(automatic.model, "codex-high")
        self.assertEqual(automatic.mode, "generous")

        pinned = registry.select(
            {"role": "planner", "priority": 50, "executor": "claude-code"}, {}
        )
        self.assertEqual(pinned.executor, "claude-code")
        self.assertEqual(pinned.model, "claude-code-economy")

    def test_existing_session_keeps_its_executor_and_model(self):
        codex = QuotaExecutor("codex", snapshot("codex", 10))
        claude = QuotaExecutor("claude-code", snapshot("claude-code", 95))
        registry = AgentRegistry([codex, claude], "codex")
        registry.preflight()
        decision = registry.select(
            {
                "role": "implementer",
                "session_id": "session-1",
                "assigned_executor": "claude-code",
                "assigned_model": "opus-fixed",
            },
            {},
        )
        self.assertEqual(decision.executor, "claude-code")
        self.assertEqual(decision.model, "opus-fixed")

    def test_existing_session_never_falls_across_a_missing_executor(self):
        codex = QuotaExecutor("codex", snapshot("codex", 10))
        registry = AgentRegistry([codex], "codex")
        registry.preflight()
        with self.assertRaisesRegex(RuntimeError, "claude-code.*未启用"):
            registry.select(
                {
                    "role": "implementer",
                    "session_id": "claude-session",
                    "assigned_executor": "claude-code",
                    "assigned_model": "opus-fixed",
                },
                {},
            )

    def test_reached_quota_defers_work_until_reset(self):
        codex = QuotaExecutor("codex", snapshot("codex", 100, reached=True))
        registry = AgentRegistry([codex], "codex")
        registry.preflight()
        decision = registry.select({"role": "implementer"}, {})
        self.assertTrue(decision.blocked)
        self.assertIsNotNone(decision.defer_until)


class ClaudeQuotaTests(unittest.TestCase):
    def test_rate_limit_events_merge_and_persist_without_credentials(self):
        first = merge_claude_rate_limit(
            QuotaSnapshot.unknown("claude-code"),
            {
                "rate_limit_type": "five_hour",
                "status": "allowed_warning",
                "utilization": 0.82,
                "resets_at": 1787330000,
            },
        )
        second = merge_claude_rate_limit(
            first,
            {
                "rate_limit_type": "seven_day",
                "status": "allowed",
                "utilization": 0.35,
                "resets_at": 1787900000,
            },
        )
        self.assertEqual(len(second.windows), 2)
        self.assertEqual(second.remaining_percent, 18)

        with tempfile.TemporaryDirectory() as temp:
            cache = QuotaCache(Path(temp) / "claude.json")
            cache.write(second)
            restored = cache.read("claude-code")
        self.assertEqual(restored.remaining_percent, 18)
        self.assertEqual(restored.source, "claude-rate-limit-event")


if __name__ == "__main__":
    unittest.main()
