from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class GeminiProvider:
    BASE_URL = "https://generativelanguage.googleapis.com"

    def __init__(
        self,
        api_key: str = "",
        model: str = "gemini-2.0-flash",
        timeout_seconds: int = 15,
    ) -> None:
        if not api_key:
            raise RuntimeError(
                "gemini api_key is not configured. "
                "Set GEMINI_API_KEY to your Google AI Studio API key."
            )
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    def _post(self, path: str, body: dict[str, Any]) -> tuple[dict[str, Any], Any]:
        url = f"{self.BASE_URL}{path}?key={self.api_key}"
        data = json.dumps(body).encode("utf-8")
        req = Request(
            url,
            data=data,
            headers={
                "content-type": "application/json",
                "user-agent": "coding-limits-gateway/0.3.0",
            },
            method="POST",
        )
        try:
            with urlopen(req, timeout=self.timeout_seconds) as resp:
                return json.load(resp), resp.headers
        except HTTPError as exc:
            raise exc
        except URLError as exc:
            raise RuntimeError(f"gemini connection error: {exc}") from exc

    @staticmethod
    def _empty_window(label: str, duration_mins: int | None = None) -> dict[str, Any]:
        return {
            "label": label,
            "usedPercent": None,
            "remainingPercent": None,
            "windowDurationMins": duration_mins,
            "resetsAt": None,
        }

    @staticmethod
    def _exhausted_window(label: str, resets_at: int | None, duration_mins: int) -> dict[str, Any]:
        return {
            "label": label,
            "usedPercent": 100,
            "remainingPercent": 0,
            "windowDurationMins": duration_mins,
            "resetsAt": resets_at,
        }

    def _parse_rate_limit_error(self, exc: HTTPError) -> dict[str, Any]:
        retry_after = exc.headers.get("Retry-After")
        resets_at = int(time.time()) + int(retry_after) if retry_after else None

        try:
            body = json.loads(exc.read().decode("utf-8", errors="replace"))
            err_details = body.get("error", {})
            details_list = err_details.get("details", [])
            quota_id = None
            for d in details_list:
                meta = d.get("metadata", {})
                quota_id = meta.get("quota_id") or meta.get("quotaId")
                if quota_id:
                    break
            is_minute = quota_id and "PerMinute" in quota_id if quota_id else False
        except Exception:
            is_minute = False

        # Report the rate-limited window; the other window is unknown
        if is_minute:
            return {
                "enabled": True,
                "ok": False,
                "source": "gemini-api",
                "planType": None,
                "limitId": "gemini",
                "shortWindow": self._exhausted_window("RPM", resets_at, 1),
                "longWindow": self._empty_window("RPD"),
                "credits": None,
                "rateLimitReachedType": "rpm",
                "error": f"Rate limit exceeded (RPM). Resets in {retry_after}s." if retry_after else "Rate limit exceeded (RPM).",
            }
        return {
            "enabled": True,
            "ok": False,
            "source": "gemini-api",
            "planType": None,
            "limitId": "gemini",
            "shortWindow": self._empty_window("RPM"),
            "longWindow": self._exhausted_window("RPD", resets_at, 1440),
            "credits": None,
            "rateLimitReachedType": "rpd",
            "error": f"Rate limit exceeded (RPD). Resets in {retry_after}s." if retry_after else "Rate limit exceeded.",
        }

    def fetch(self) -> dict[str, Any]:
        path = f"/v1beta/models/{self.model}:countTokens"
        body = {"contents": [{"parts": [{"text": "x"}]}]}

        try:
            _result, _headers = self._post(path, body)
        except HTTPError as exc:
            if exc.code == 429:
                return self._parse_rate_limit_error(exc)
            body_text = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"gemini HTTP {exc.code}: {body_text[:200]}") from exc

        return {
            "enabled": True,
            "ok": True,
            "source": "gemini-api",
            "planType": None,
            "limitId": "gemini",
            "shortWindow": self._empty_window("RPM", 1),
            "longWindow": self._empty_window("RPD", 1440),
            "credits": None,
            "rateLimitReachedType": None,
            "error": None,
        }
