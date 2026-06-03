from __future__ import annotations

import datetime
import glob
import json
import os
import shutil
from pathlib import Path
from typing import Any


class GeminiProvider:
    """
    Reads Gemini CLI usage from ~/.gemini/tmp/*/logs.json.
    No API calls, no API key — uses the same local auth as the CLI.
    Each non-slash user message in the logs counts as one AI request.
    """

    def __init__(
        self,
        gemini_home: str = "",
        daily_limit: int = 1000,
        rpm_limit: int = 15,
    ) -> None:
        resolved = gemini_home or os.environ.get("GEMINI_CLI_HOME", "")
        self.gemini_home = Path(resolved).expanduser() if resolved else Path.home() / ".gemini"
        self.daily_limit = daily_limit
        self.rpm_limit = rpm_limit

    def _log_files(self) -> list[Path]:
        pattern = str(self.gemini_home / "tmp" / "*" / "logs.json")
        return [Path(p) for p in glob.glob(pattern)]

    def _count_requests(self) -> tuple[int, int]:
        """Returns (requests_today, requests_last_minute)."""
        now = datetime.datetime.now(datetime.timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        minute_start = now - datetime.timedelta(seconds=60)

        total_today = 0
        total_minute = 0

        for log_file in self._log_files():
            try:
                with log_file.open("r", encoding="utf-8") as fh:
                    entries = json.load(fh)
            except (OSError, json.JSONDecodeError):
                continue

            for entry in entries:
                if entry.get("type") != "user":
                    continue
                msg = entry.get("message", "")
                if not msg or msg.startswith("/"):
                    continue
                ts_raw = entry.get("timestamp", "")
                if not ts_raw:
                    continue
                try:
                    ts = datetime.datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if ts >= today_start:
                    total_today += 1
                if ts >= minute_start:
                    total_minute += 1

        return total_today, total_minute

    def _make_window(
        self, label: str, used: int, limit: int, duration_mins: int
    ) -> dict[str, Any]:
        used_pct = min(100, round(used / limit * 100, 1)) if limit > 0 else None
        remaining_pct = max(0, 100 - int(used_pct)) if used_pct is not None else None
        return {
            "label": label,
            "usedPercent": used_pct,
            "remainingPercent": remaining_pct,
            "windowDurationMins": duration_mins,
            "resetsAt": None,
        }

    def fetch(self) -> dict[str, Any]:
        if not self.gemini_home.exists():
            raise RuntimeError(
                f"Gemini CLI home not found at {self.gemini_home}. "
                "Install gemini-cli and log in with 'gemini'."
            )
        if not shutil.which("gemini"):
            raise RuntimeError(
                "gemini binary not found in PATH. "
                "Install it from https://github.com/google-gemini/gemini-cli"
            )

        daily_used, rpm_used = self._count_requests()

        return {
            "enabled": True,
            "ok": True,
            "source": "gemini-cli-logs",
            "planType": "personal",
            "limitId": "gemini",
            "shortWindow": self._make_window("RPM", rpm_used, self.rpm_limit, 1),
            "longWindow": self._make_window("RPD", daily_used, self.daily_limit, 1440),
            "credits": None,
            "rateLimitReachedType": None,
            "error": None,
        }
