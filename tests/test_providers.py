from __future__ import annotations

import json
import sys
import unittest
from io import BytesIO
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
    def _make_provider(self, **kwargs) -> GeminiProvider:
        defaults = {"api_key": "test-api-key"}
        defaults.update(kwargs)
        return GeminiProvider(**defaults)

    def _make_http_error(self, code: int, body: dict, headers: dict | None = None) -> Exception:
        from urllib.error import HTTPError
        body_bytes = json.dumps(body).encode()
        mock_headers = MagicMock()
        mock_headers.get = lambda k, d=None: (headers or {}).get(k, d)
        exc = HTTPError(
            url="https://example.com",
            code=code,
            msg="Error",
            hdrs=mock_headers,
            fp=BytesIO(body_bytes),
        )
        return exc

    def test_raises_on_empty_api_key(self):
        with self.assertRaises(RuntimeError) as ctx:
            GeminiProvider(api_key="")
        self.assertIn("api_key", str(ctx.exception).lower())

    @patch("providers.gemini.urlopen")
    def test_fetch_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = json.dumps({"totalTokens": 1}).encode()
        mock_resp.headers = MagicMock()

        import io
        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self): return json.dumps({"totalTokens": 1}).encode()
            headers = MagicMock()
        mock_urlopen.return_value = FakeResp()

        data = self._make_provider().fetch()
        self.assertTrue(data["ok"])
        self.assertEqual(data["source"], "gemini-api")
        self.assertIsNone(data["shortWindow"]["usedPercent"])
        self.assertIsNone(data["longWindow"]["usedPercent"])
        self.assertIsNone(data["error"])

    @patch("providers.gemini.urlopen")
    def test_fetch_rate_limited_rpm(self, mock_urlopen):
        from urllib.error import HTTPError
        err_body = {
            "error": {
                "code": 429,
                "details": [{
                    "metadata": {"quota_id": "GenerateRequestsPerMinutePerProjectPerModel"}
                }]
            }
        }
        body_bytes = json.dumps(err_body).encode()
        mock_headers = MagicMock()
        mock_headers.get = lambda k, d=None: "30" if k == "Retry-After" else d
        exc = HTTPError("https://x", 429, "Too Many Requests", mock_headers, BytesIO(body_bytes))
        mock_urlopen.side_effect = exc

        data = self._make_provider().fetch()
        self.assertFalse(data["ok"])
        self.assertEqual(data["shortWindow"]["usedPercent"], 100)
        self.assertEqual(data["shortWindow"]["remainingPercent"], 0)
        self.assertEqual(data["rateLimitReachedType"], "rpm")

    @patch("providers.gemini.urlopen")
    def test_fetch_rate_limited_rpd(self, mock_urlopen):
        from urllib.error import HTTPError
        err_body = {"error": {"code": 429, "details": [{"metadata": {"quota_id": "GenerateRequestsPerDay"}}]}}
        body_bytes = json.dumps(err_body).encode()
        mock_headers = MagicMock()
        mock_headers.get = lambda k, d=None: d
        exc = HTTPError("https://x", 429, "Too Many Requests", mock_headers, BytesIO(body_bytes))
        mock_urlopen.side_effect = exc

        data = self._make_provider().fetch()
        self.assertFalse(data["ok"])
        self.assertEqual(data["longWindow"]["usedPercent"], 100)
        self.assertEqual(data["rateLimitReachedType"], "rpd")

    @patch("providers.gemini.urlopen")
    def test_fetch_raises_on_non_429_error(self, mock_urlopen):
        from urllib.error import HTTPError
        body_bytes = b'{"error": {"code": 403, "message": "forbidden"}}'
        mock_headers = MagicMock()
        mock_headers.get = lambda k, d=None: d
        exc = HTTPError("https://x", 403, "Forbidden", mock_headers, BytesIO(body_bytes))
        mock_urlopen.side_effect = exc

        with self.assertRaises(RuntimeError) as ctx:
            self._make_provider().fetch()
        self.assertIn("403", str(ctx.exception))


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
