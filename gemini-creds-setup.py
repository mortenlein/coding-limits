#!/usr/bin/env python3
"""
Prepares ~/.gemini/oauth_creds.json for use on a remote gateway server.

Adds the antigravity-cli (agy) OAuth client credentials (client_id,
client_secret, token_uri) to the credentials file so the gateway can
refresh access tokens without the agy binary being installed on the server.

Usage (run on your dev machine):

    python3 gemini-creds-setup.py > /tmp/gemini-gateway-creds.json
    scp /tmp/gemini-gateway-creds.json mole@ash:/etc/gemini-oauth-creds.json
    rm /tmp/gemini-gateway-creds.json

Then on the gateway server set:
    GEMINI_ENABLED=true
    GEMINI_CREDS_FILE=/etc/gemini-oauth-creds.json
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _extract_from_binary() -> list[tuple[str, str]]:
    """Return all (client_id, client_secret) pairs found in the agy binary."""
    import re
    agy_bin = shutil.which("agy")
    if not agy_bin:
        raise RuntimeError("agy binary not found in PATH. Install antigravity-cli first.")
    result = subprocess.run(
        ["strings", agy_bin],
        capture_output=True, text=True, timeout=15,
    )
    text = result.stdout
    # Project numbers vary from 12-13 digits; use a range
    client_ids = re.findall(r'\d{10,14}-[a-z0-9]{32}\.apps\.googleusercontent\.com', text)
    # Google client secrets are GOCSPX- + 28 chars (fixed length avoids matching concatenated blobs)
    client_secrets = re.findall(r'GOCSPX-[A-Za-z0-9_\-]{28}', text)
    if not client_ids or not client_secrets:
        raise RuntimeError(
            "Could not find OAuth client credentials in the agy binary. "
            "Set GEMINI_CLIENT_ID and GEMINI_CLIENT_SECRET env vars manually."
        )
    # Return unique pairs (IDs and secrets appear in corresponding order in the binary)
    pairs = list(zip(dict.fromkeys(client_ids), dict.fromkeys(client_secrets)))
    return pairs


def main() -> None:
    creds_path = Path(os.environ.get("GEMINI_CREDS_FILE", Path.home() / ".gemini" / "oauth_creds.json"))
    if not creds_path.exists():
        print(f"Error: credentials not found at {creds_path}", file=sys.stderr)
        print("Log in first with: agy", file=sys.stderr)
        sys.exit(1)

    with creds_path.open() as f:
        creds = json.load(f)

    client_id = os.environ.get("GEMINI_CLIENT_ID") or ""
    client_secret = os.environ.get("GEMINI_CLIENT_SECRET") or ""

    if client_id and client_secret:
        pairs = [(client_id, client_secret)]
    else:
        pairs = _extract_from_binary()

    # Embed all pairs so the gateway can try each one at refresh time
    creds["oauth_pairs"] = [{"client_id": cid, "client_secret": cs} for cid, cs in pairs]
    # Also set the first pair as the primary for the standard fields
    creds["client_id"] = pairs[0][0]
    creds["client_secret"] = pairs[0][1]
    creds["token_uri"] = "https://oauth2.googleapis.com/token"

    print(json.dumps(creds, indent=2))
    print(f"\n# Found {len(pairs)} OAuth client pair(s) — gateway will try each on token refresh.", file=sys.stderr)


if __name__ == "__main__":
    main()
