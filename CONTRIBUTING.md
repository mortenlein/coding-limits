# Contributing

## Getting started

```bash
git clone https://github.com/mortenlein/coding-limits
cd coding-limits
```

No dependencies outside the standard library. Run tests with:

```bash
python3 -m unittest discover tests/ -v
```

Lint with ruff (optional):
```bash
pip install ruff
ruff check .
```

---

## Adding a provider

A provider is a Python class with a single `fetch()` method that returns a dict matching the snapshot schema.

### 1. Create `providers/yourprovider.py`

```python
from __future__ import annotations
from typing import Any

class YourProvider:
    def __init__(self, api_key: str = "", timeout_seconds: int = 10) -> None:
        if not api_key:
            raise RuntimeError("api_key is required")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def fetch(self) -> dict[str, Any]:
        # ... call your API ...
        return {
            "enabled": True,
            "ok": True,
            "source": "your-api",
            "planType": None,
            "shortWindow": {
                "label": "5h",
                "usedPercent": 40.0,
                "remainingPercent": 60,
                "windowDurationMins": 300,
                "resetsAt": 1780408200,
            },
            "longWindow": {
                "label": "7d",
                "usedPercent": 20.0,
                "remainingPercent": 80,
                "windowDurationMins": 10080,
                "resetsAt": 1780840800,
            },
            "error": None,
        }
```

### 2. Register in `providers/__init__.py`

```python
from .yourprovider import YourProvider
__all__ = [..., "YourProvider"]
```

### 3. Wire up in `server.py`

Follow the same pattern as the Codex/Claude/Gemini entries in `build_snapshot()`.

### 4. Add env vars to `coding-limits.env.example` and `config.example.json`

```
YOUR_ENABLED=false
YOUR_API_KEY=
YOUR_TIMEOUT_SECONDS=10
```

### 5. Add tests in `tests/test_providers.py`

Cover at minimum: happy path, null/missing fields, auth failure.

---

## Pull request checklist

- [ ] `python3 -m unittest discover tests/ -v` passes
- [ ] New providers: tests cover `fetch()` happy path, null/missing fields, and auth failure
- [ ] No secrets, credentials, or local paths committed
