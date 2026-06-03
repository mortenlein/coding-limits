from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from providers.claude import ClaudeProvider
from providers.codex import CodexProvider
from providers.gemini import GeminiProvider


# ── Codex ─────────────────────────────────────────────────────────────────────

class CodexProviderTests(unittest.TestCase):
    def _make_process(self, responses: list[dict]) -> MagicMock:
        process = MagicMock()
        lines = [json.dumps(r) + "\n" for r in responses] + [""]
        process.stdout.readline.side_effect = lines
        process.stderr.read.return_value = ""
        return process

    @patch("providers.codex.shutil.which", return_value="/usr/bin/codex")
    @patch("providers.codex.subprocess.Popen")
    def test_parses_rate_limit_response(self, popen_mock, _which):
        popen_mock.return_value = self._make_process([
            {"id": "init", "result": {"ok": True}},
            {
                "id": "limits",
                "result": {
                    "rateLimitsByLimitId": {
                        "codex": {
                            "limitId": "codex",
                            "planType": "plus",
                            "primary": {"usedPercent": 51, "windowDurationMins": 300, "resetsAt": 1780390634},
                            "secondary": {"usedPercent": 34, "windowDurationMins": 10080, "resetsAt": 1780847004},
                            "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
                        }
                    }
                },
            },
        ])
        data = CodexProvider().fetch()
        self.assertTrue(data["ok"])
        self.assertEqual(data["planType"], "plus")
        self.assertEqual(data["shortWindow"]["usedPercent"], 51)
        self.assertEqual(data["shortWindow"]["remainingPercent"], 49)
        self.assertEqual(data["longWindow"]["usedPercent"], 34)
        self.assertEqual(data["longWindow"]["remainingPercent"], 66)
        self.assertIsNone(data["error"])

    @patch("providers.codex.shutil.which", return_value="/usr/bin/codex")
    @patch("providers.codex.subprocess.Popen")
    def test_fallback_rate_limits_key(self, popen_mock, _which):
        popen_mock.return_value = self._make_process([
            {"id": "init", "result": {}},
            {
                "id": "limits",
                "result": {
                    "rateLimits": {
                        "planType": "free",
                        "primary": {"usedPercent": 10},
                        "secondary": {"usedPercent": 5},
                        "credits": {},
                    }
                },
            },
        ])
        data = CodexProvider().fetch()
        self.assertEqual(data["planType"], "free")
        self.assertEqual(data["shortWindow"]["remainingPercent"], 90)

    @patch("providers.codex.shutil.which", return_value=None)
    def test_raises_if_codex_not_found(self, _which):
        with self.assertRaises(RuntimeError) as ctx:
            CodexProvider(codex_path="/nonexistent/codex").fetch()
        self.assertIn("not found", str(ctx.exception))

    @patch("providers.codex.shutil.which", return_value="/usr/bin/codex")
    @patch("providers.codex.subprocess.Popen")
    def test_raises_on_timeout(self, popen_mock, _which):
        process = MagicMock()
        process.stdout.readline.return_value = ""
        process.stderr.read.return_value = ""
        popen_mock.return_value = process
        with self.assertRaises(RuntimeError) as ctx:
            CodexProvider(timeout_seconds=0).fetch()
        self.assertIn("no result", str(ctx.exception).lower())

    @patch("providers.codex.shutil.which", return_value="/usr/bin/codex")
    @patch("providers.codex.subprocess.Popen")
    def test_skips_malformed_jsonl_lines(self, popen_mock, _which):
        process = MagicMock()
        process.stdout.readline.side_effect = [
            "not valid json\n",
            json.dumps({"id": "limits", "result": {"rateLimitsByLimitId": {"codex": {"primary": {}, "secondary": {}, "credits": {}}}}}) + "\n",
            "",
        ]
        process.stderr.read.return_value = ""
        popen_mock.return_value = process
        data = CodexProvider().fetch()
        self.assertTrue(data["ok"])

    def test_limit_window_handles_none_used(self):
        from providers.codex import limit_window
        result = limit_window("5h", {"usedPercent": None, "resetsAt": 12345})
        self.assertIsNone(result["usedPercent"])
        self.assertIsNone(result["remainingPercent"])

    def test_limit_window_clamps_over_100(self):
        from providers.codex import limit_window
        result = limit_window("5h", {"usedPercent": 105})
        self.assertEqual(result["remainingPercent"], 0)

    def test_limit_window_clamps_negative(self):
        from providers.codex import limit_window
        result = limit_window("5h", {"usedPercent": -5})
        self.assertEqual(result["remainingPercent"], 100)


# ── Claude ────────────────────────────────────────────────────────────────────

class ClaudeProviderTests(unittest.TestCase):
    def _make_provider(self, **kwargs) -> ClaudeProvider:
        defaults = {"session_key": "sk-ant-test"}
        defaults.update(kwargs)
        return ClaudeProvider(**defaults)

    def _mock_responses(self, provider: ClaudeProvider, responses: dict[str, Any]) -> ClaudeProvider:
        def fake_request(path: str):
            if path not in responses:
                raise KeyError(f"unexpected path: {path}")
            result = responses[path]
            if isinstance(result, Exception):
                raise result
            return result
        provider._request_json = fake_request  # type: ignore[method-assign]
        return provider

    def test_parse_limit_handles_none(self):
        result = ClaudeProvider._parse_limit(None, "7d")
        self.assertEqual(result["label"], "7d")
        self.assertIsNone(result["usedPercent"])
        self.assertIsNone(result["remainingPercent"])
        self.assertIsNone(result["resetsAt"])

    def test_parse_limit_handles_empty_dict(self):
        result = ClaudeProvider._parse_limit({}, "5h")
        self.assertIsNone(result["usedPercent"])

    def test_parse_limit_computes_remaining(self):
        result = ClaudeProvider._parse_limit({"utilization": 30, "resets_at": None}, "5h")
        self.assertEqual(result["usedPercent"], 30)
        self.assertEqual(result["remainingPercent"], 70)

    def test_parse_limit_clamps_remaining(self):
        result = ClaudeProvider._parse_limit({"utilization": 110}, "5h")
        self.assertEqual(result["remainingPercent"], 0)

    def test_fetch_success(self):
        org_uuid = "test-org-uuid"
        provider = self._make_provider()
        self._mock_responses(provider, {
            "/api/organizations": [{"uuid": org_uuid}],
            f"/api/organizations/{org_uuid}/usage": {
                "five_hour": {"utilization": 20, "resets_at": None},
                "seven_day": {"utilization": 40, "resets_at": None},
            },
            f"/api/organizations/{org_uuid}/overage_spend_limit": {
                "is_enabled": True,
                "used_credits": 500,
                "monthly_credit_limit": 10000,
            },
        })
        data = provider.fetch()
        self.assertTrue(data["ok"])
        self.assertEqual(data["shortWindow"]["remainingPercent"], 80)
        self.assertEqual(data["longWindow"]["remainingPercent"], 60)
        self.assertEqual(data["credits"]["enabled"], True)

    def test_fetch_handles_null_usage(self):
        org_uuid = "test-org-uuid"
        provider = self._make_provider()
        self._mock_responses(provider, {
            "/api/organizations": [{"uuid": org_uuid}],
            f"/api/organizations/{org_uuid}/usage": None,
            f"/api/organizations/{org_uuid}/overage_spend_limit": {},
        })
        data = provider.fetch()
        self.assertTrue(data["ok"])
        self.assertIsNone(data["shortWindow"]["usedPercent"])

    def test_fetch_handles_overage_failure(self):
        org_uuid = "test-org-uuid"
        provider = self._make_provider()
        self._mock_responses(provider, {
            "/api/organizations": [{"uuid": org_uuid}],
            f"/api/organizations/{org_uuid}/usage": {},
            f"/api/organizations/{org_uuid}/overage_spend_limit": RuntimeError("403 forbidden"),
        })
        data = provider.fetch()
        self.assertTrue(data["ok"])
        self.assertIsNone(data["credits"])
        self.assertIsNotNone(data["error"])

    def test_fetch_raises_on_no_organizations(self):
        provider = self._make_provider()
        self._mock_responses(provider, {"/api/organizations": []})
        with self.assertRaises(RuntimeError) as ctx:
            provider.fetch()
        self.assertIn("organizations", str(ctx.exception).lower())

    def test_fetch_raises_on_null_organizations(self):
        provider = self._make_provider()
        self._mock_responses(provider, {"/api/organizations": None})
        with self.assertRaises(RuntimeError):
            provider.fetch()

    def test_fetch_raises_on_missing_uuid(self):
        provider = self._make_provider()
        self._mock_responses(provider, {"/api/organizations": [{"name": "no-uuid"}]})
        with self.assertRaises(RuntimeError) as ctx:
            provider.fetch()
        self.assertIn("uuid", str(ctx.exception).lower())

    def test_raises_on_empty_session_key(self):
        with self.assertRaises(RuntimeError) as ctx:
            ClaudeProvider(session_key="")
        self.assertIn("session_key", str(ctx.exception).lower())

    def test_raises_on_invalid_base_url(self):
        with self.assertRaises(RuntimeError) as ctx:
            ClaudeProvider(session_key="sk-test", base_url="not-a-url")
        self.assertIn("base_url", str(ctx.exception).lower())


# ── Gemini ────────────────────────────────────────────────────────────────────

class GeminiProviderTests(unittest.TestCase):
    def _make_provider(self, tmp_home: Path, **kwargs) -> GeminiProvider:
        defaults = {"gemini_home": str(tmp_home), "daily_limit": 100, "rpm_limit": 10}
        defaults.update(kwargs)
        return GeminiProvider(**defaults)

    def _write_logs(self, tmp_home: Path, project: str, entries: list[dict]) -> None:
        log_dir = tmp_home / "tmp" / project
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "logs.json").write_text(json.dumps(entries))

    def _ts(self, delta_seconds: int = 0) -> str:
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        t = now + datetime.timedelta(seconds=delta_seconds)
        return t.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def test_raises_if_gemini_home_missing(self):
        with self.assertRaises(RuntimeError) as ctx:
            GeminiProvider(gemini_home="/nonexistent/path/xyz").fetch()
        self.assertIn("not found", str(ctx.exception))

    @patch("providers.gemini.shutil.which", return_value="/usr/bin/gemini")
    def test_fetch_no_requests(self, _which):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_home = Path(tmp)
            (tmp_home / "tmp").mkdir()
            data = self._make_provider(tmp_home).fetch()
        self.assertTrue(data["ok"])
        self.assertEqual(data["source"], "gemini-cli-logs")
        self.assertEqual(data["shortWindow"]["usedPercent"], 0)
        self.assertEqual(data["longWindow"]["usedPercent"], 0)
        self.assertEqual(data["shortWindow"]["remainingPercent"], 100)
        self.assertIsNone(data["error"])

    @patch("providers.gemini.shutil.which", return_value="/usr/bin/gemini")
    def test_counts_requests_today(self, _which):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_home = Path(tmp)
            self._write_logs(tmp_home, "proj", [
                {"type": "user", "messageId": 0, "message": "hello", "timestamp": self._ts(-3600), "sessionId": "a"},
                {"type": "user", "messageId": 1, "message": "world", "timestamp": self._ts(-120), "sessionId": "a"},
            ])
            data = self._make_provider(tmp_home, daily_limit=10).fetch()
        self.assertEqual(data["longWindow"]["usedPercent"], 20)  # 2/10 = 20%
        self.assertEqual(data["longWindow"]["remainingPercent"], 80)

    @patch("providers.gemini.shutil.which", return_value="/usr/bin/gemini")
    def test_counts_rpm(self, _which):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_home = Path(tmp)
            self._write_logs(tmp_home, "proj", [
                {"type": "user", "messageId": 0, "message": "a", "timestamp": self._ts(-10), "sessionId": "a"},
                {"type": "user", "messageId": 1, "message": "b", "timestamp": self._ts(-5), "sessionId": "a"},
                {"type": "user", "messageId": 2, "message": "c", "timestamp": self._ts(-3600), "sessionId": "a"},  # old
            ])
            data = self._make_provider(tmp_home, rpm_limit=10).fetch()
        self.assertEqual(data["shortWindow"]["usedPercent"], 20)  # 2/10 = 20%

    @patch("providers.gemini.shutil.which", return_value="/usr/bin/gemini")
    def test_ignores_slash_commands(self, _which):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_home = Path(tmp)
            self._write_logs(tmp_home, "proj", [
                {"type": "user", "messageId": 0, "message": "/stats", "timestamp": self._ts(-10), "sessionId": "a"},
                {"type": "user", "messageId": 1, "message": "/quit", "timestamp": self._ts(-5), "sessionId": "a"},
                {"type": "user", "messageId": 2, "message": "real prompt", "timestamp": self._ts(-3), "sessionId": "a"},
            ])
            data = self._make_provider(tmp_home, daily_limit=10, rpm_limit=10).fetch()
        self.assertEqual(data["longWindow"]["usedPercent"], 10)  # 1/10 = 10%

    @patch("providers.gemini.shutil.which", return_value="/usr/bin/gemini")
    def test_aggregates_multiple_projects(self, _which):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_home = Path(tmp)
            self._write_logs(tmp_home, "proj1", [
                {"type": "user", "messageId": 0, "message": "a", "timestamp": self._ts(-10), "sessionId": "a"},
            ])
            self._write_logs(tmp_home, "proj2", [
                {"type": "user", "messageId": 0, "message": "b", "timestamp": self._ts(-5), "sessionId": "b"},
            ])
            data = self._make_provider(tmp_home, daily_limit=10).fetch()
        self.assertEqual(data["longWindow"]["usedPercent"], 20)  # 2/10 = 20%

    @patch("providers.gemini.shutil.which", return_value="/usr/bin/gemini")
    def test_handles_malformed_log_file(self, _which):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_home = Path(tmp)
            bad_dir = tmp_home / "tmp" / "bad"
            bad_dir.mkdir(parents=True)
            (bad_dir / "logs.json").write_text("not valid json")
            data = self._make_provider(tmp_home).fetch()
        self.assertTrue(data["ok"])  # bad file is skipped, not fatal

    @patch("providers.gemini.shutil.which", return_value="/usr/bin/gemini")
    def test_clamps_at_100_percent(self, _which):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_home = Path(tmp)
            entries = [
                {"type": "user", "messageId": i, "message": f"msg {i}", "timestamp": self._ts(-i*5), "sessionId": "a"}
                for i in range(20)
            ]
            self._write_logs(tmp_home, "proj", entries)
            data = self._make_provider(tmp_home, daily_limit=5).fetch()
        self.assertEqual(data["longWindow"]["usedPercent"], 100)
        self.assertEqual(data["longWindow"]["remainingPercent"], 0)


# ── Config ────────────────────────────────────────────────────────────────────

class ConfigTests(unittest.TestCase):
    def test_deep_merge_nested(self):
        from server import deep_merge
        base = {"a": {"x": 1, "y": 2}, "b": 3}
        incoming = {"a": {"y": 99, "z": 100}, "c": 4}
        result = deep_merge(base, incoming)
        self.assertEqual(result["a"]["x"], 1)
        self.assertEqual(result["a"]["y"], 99)
        self.assertEqual(result["a"]["z"], 100)
        self.assertEqual(result["b"], 3)
        self.assertEqual(result["c"], 4)

    def test_validate_config_bad_port(self):
        from server import validate_config
        with self.assertRaises(ValueError):
            validate_config({"listen_port": 99999, "providers": {"codex": {}, "claude": {}, "gemini": {}}})

    def test_env_bool_parsing(self):
        from server import parse_env_bool
        import os
        os.environ["_TEST_BOOL"] = "true"
        self.assertTrue(parse_env_bool("_TEST_BOOL", False))
        os.environ["_TEST_BOOL"] = "0"
        self.assertFalse(parse_env_bool("_TEST_BOOL", True))
        del os.environ["_TEST_BOOL"]


from typing import Any  # noqa: E402

if __name__ == "__main__":
    unittest.main()
