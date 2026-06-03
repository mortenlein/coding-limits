from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class ClaudeProvider:
    def __init__(
        self,
        base_url: str = "https://claude.ai",
        session_key: str = "",
        timeout_seconds: int = 15,
    ) -> None:
        if not session_key:
            raise RuntimeError(
                "claude session_key is not configured. "
                "Set CLAUDE_SESSION_KEY to the sessionKey cookie from claude.ai."
            )
        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise RuntimeError(f"claude base_url is not a valid HTTP URL: {base_url!r}")
        self.base_url = base_url.rstrip("/")
        self.session_key = session_key
        self.timeout_seconds = timeout_seconds

    def _request_json(self, path: str) -> Any:

        req = Request(
            self.base_url + path,
            headers={
                "accept": "*/*",
                "accept-language": "en-US,en;q=0.9",
                "content-type": "application/json",
                "anthropic-client-platform": "web_claude_ai",
                "anthropic-client-version": "1.0.0",
                "origin": "https://claude.ai",
                "referer": "https://claude.ai/settings/usage",
                "user-agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                "Cookie": f"sessionKey={self.session_key}",
            },
            method="GET",
        )
        try:
            with urlopen(req, timeout=self.timeout_seconds) as response:
                return json.load(response)
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"claude HTTP {exc.code}: {body[:200]}") from exc
        except URLError as exc:
            raise RuntimeError(f"claude connection error: {exc}") from exc

    @staticmethod
    def _parse_limit(value: Any, label: str) -> dict[str, Any]:
        if not value:
            return {
                "label": label,
                "usedPercent": None,
                "remainingPercent": None,
                "windowDurationMins": None,
                "resetsAt": None,
            }

        reset_at = value.get("resets_at")
        reset_epoch = None
        if reset_at:
            dt = datetime.fromisoformat(reset_at.replace("Z", "+00:00"))
            reset_epoch = int(dt.astimezone(timezone.utc).timestamp())

        used = value.get("utilization")
        remaining = None if used is None else max(0, min(100, 100 - int(used)))
        return {
            "label": label,
            "usedPercent": used,
            "remainingPercent": remaining,
            "windowDurationMins": 300 if label == "5h" else 10080,
            "resetsAt": reset_epoch,
        }

    def fetch(self) -> dict[str, Any]:
        organizations = self._request_json("/api/organizations") or []
        if not organizations or not isinstance(organizations, list):
            raise RuntimeError(f"claude /api/organizations: unexpected response ({type(organizations).__name__})")

        org = organizations[0] or {}
        if not isinstance(org, dict):
            raise RuntimeError(f"claude organizations[0] is not a dict ({type(org).__name__})")
        org_uuid = org.get("uuid")
        if not org_uuid:
            raise RuntimeError("claude organization response is missing uuid")

        usage = self._request_json(f"/api/organizations/{org_uuid}/usage") or {}

        overage_error = None
        credits: dict[str, Any] | None = None
        try:
            overage = self._request_json(f"/api/organizations/{org_uuid}/overage_spend_limit") or {}
            credits = {
                "enabled": overage.get("is_enabled"),
                "usedCreditsCents": overage.get("used_credits"),
                "monthlyLimitCents": overage.get("monthly_credit_limit"),
            }
        except Exception as exc:  # optional
            overage_error = str(exc)

        return {
            "enabled": True,
            "ok": True,
            "source": "claude-web",
            "planType": None,
            "limitId": "claude",
            "organizationId": org_uuid,
            "shortWindow": self._parse_limit(usage.get("five_hour"), "5h"),
            "longWindow": self._parse_limit(usage.get("seven_day"), "7d"),
            "details": {
                "sevenDayOpus": self._parse_limit(usage.get("seven_day_opus"), "7d-opus"),
                "sevenDaySonnet": self._parse_limit(usage.get("seven_day_sonnet"), "7d-sonnet"),
            },
            "credits": credits,
            "rateLimitReachedType": None,
            "error": overage_error,
        }
