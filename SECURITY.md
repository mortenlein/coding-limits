# Security

## Threat model

coding-limits is designed for use on a **trusted home or office LAN**. It is not hardened for exposure to the public internet without additional measures.

| Asset | Risk | Mitigation |
|---|---|---|
| WiFi credentials | Stored plaintext in ESP32 NVS flash | Physical access required; see NVS encryption note below |
| Gateway token | Stored plaintext in ESP32 NVS flash | Physical access required |
| Claude session key | Stored in `/etc/coding-limits.env` | `chmod 600` set by installer; root access required |
| Codex CLI auth | Lives in `~/.codex` on gateway host | Standard file permissions |

## Known limitations

### ESP32: no HTTPS certificate validation

When the gateway URL uses `https://`, the firmware connects with `setInsecure()` (certificate validation disabled). This means a man-in-the-middle on your LAN could intercept gateway responses.

**In practice:** most deployments use `http://` on a trusted LAN, so this is not a concern. If you expose the gateway over HTTPS, consider pinning the server's certificate fingerprint — this requires modifying `gateway_client.cpp`:

```cpp
secureClient.setFingerprint("AA:BB:CC:...");  // SHA-1 of your gateway cert
```

Certificate pinning will be a proper config option in a future release.

### ESP32: NVS plaintext storage

WiFi credentials and the gateway token are stored in the ESP32's Non-Volatile Storage (NVS) in plaintext. Physical access to the device and `esptool.py read_flash` could expose these.

ESP32 supports **NVS encryption** via a hardware key burned into eFuses, but enabling it requires a custom flash procedure and partition configuration that is beyond the scope of the default build. If physical security is a concern, enable NVS encryption following [Espressif's documentation](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/api-reference/storage/nvs_flash.html#nvs-encryption).

### Gateway: timing oracle on access token

Versions before 0.2.0 used `==` for token comparison, which is vulnerable to timing attacks. Version 0.2.0+ uses `hmac.compare_digest()`.

### Gateway: no HTTPS on the server side

The gateway serves plain HTTP by default. If you need HTTPS, run it behind a reverse proxy (nginx, Caddy) with a valid TLS certificate.

### Claude session key

The `CLAUDE_SESSION_KEY` is a browser session cookie. It provides read access to your Claude usage statistics. It cannot:
- Send messages or create conversations
- Change account settings or billing
- Access conversation history

It **can** be used to read your usage limits and credit balance. Treat it like a read-only API key.

The key expires when you log out of claude.ai from a browser. Rotate it by logging in again and copying the new `sessionKey` cookie value.

## Reporting a vulnerability

Please report security issues by opening a GitHub issue marked with the `security` label, or email the maintainer directly. We aim to respond within 48 hours.

Do not include sensitive information (credentials, session keys, private data) in issue reports.
