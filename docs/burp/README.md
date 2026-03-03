# Burp Suite — Windows Setup & Traffic Inspection Guide

Complete walkthrough for routing Python API traffic through Burp Suite Community Edition on Windows, including all SSL certificate issues encountered and resolved.

---

## Prerequisites

- Burp Suite Community Edition — [portswigger.net/burp/communitydownload](https://portswigger.net/burp/communitydownload)
- Python 3.x
- Windows CMD or PowerShell

---

## Part 1 — Initial Setup

### Step 1: Verify the proxy listener

Open Burp Suite. Navigate to `Proxy → Proxy Settings`. Confirm a listener is active on `127.0.0.1:8080`. If not, click **Add** and create it.

### Step 2: Turn Intercept OFF

Go to `Proxy → Intercept`. Ensure **Intercept is OFF**.

> **Critical:** When Intercept is ON, Burp holds every request waiting for manual approval. Your application hangs and eventually throws `WinError 10061: No connection could be made because the target machine actively refused it`. This is not a network error — it is Burp blocking the request. Turn Intercept OFF for passive traffic observation.

### Step 3: Export the CA certificate

`Proxy → Proxy Settings → Import/Export CA Certificate → Export Certificate in DER format → save as burp-ca.der`

Place `burp-ca.der` in your project root.

---

## Part 2 — Certificate Conversion

Burp exports in DER (binary) format. Python's ssl module requires PEM (Base64-encoded text) format. They are the same certificate — just different encodings.

Run the provided conversion script:

```cmd
cd D:\Git Project\claude_api
python convert_der_to_pem.py
```

Expected output:
```
Burp Suite CA Certificate — DER to PEM Converter
==================================================

  [OK] Converted burp-ca.der -> burp-ca.pem
       DER size: 1,234 bytes
       PEM size: 1,748 bytes

  Next steps:
  1. Set BURP_CA_PEM_PATH=D:\Git Project\claude_api\burp-ca.pem
  2. Set BURP_PROXY_ENABLED=true
  ...

Verifying PEM with Python ssl module...
  [OK] SSL verification passed — burp-ca.pem is valid
```

---

## Part 3 — Why Not the Windows Certificate Store?

Installing the DER certificate into Windows Trusted Root Certification Authorities does **not** fix the Python SSL errors. This is a common misconception.

Python's `urllib` and `ssl` module maintain their own certificate bundle, independent of the Windows certificate store. Even with the Burp CA installed system-wide, Python continues to use its own bundle and rejects the Burp certificate.

The solution is explicit PEM loading:

```python
import ssl
ctx = ssl.create_default_context()
ctx.load_verify_locations(cafile="burp-ca.pem")
```

This directly tells Python's SSL stack to trust the Burp CA, bypassing the system store entirely.

---

## Part 4 — SSL Errors Encountered and Resolved

### Error 1: Missing Authority Key Identifier

```
CERTIFICATE_VERIFY_FAILED: certificate verify failed:
Missing Authority Key Identifier (_ssl.c:1032)
```

**Cause:** Python 3.10+ enforces stricter X.509 validation. Burp Community Edition's default CA lacks the AKI extension.

**Fix:** Load the PEM explicitly via `ctx.load_verify_locations()`. Do not rely on the system certificate store.

---

### Error 2: CA cert does not include key usage extension

```
CERTIFICATE_VERIFY_FAILED: certificate verify failed:
CA cert does not include key usage extension (_ssl.c:1032)
```

**Cause:** Python 3.13 added `VERIFY_X509_STRICT` by default. Burp Community CA sometimes lacks the "Key Usage" extension (`Certificate Sign`, `CRL Sign`).

**Fix:** Same — explicit PEM load. If the error persists after loading:
1. In Burp: `Proxy → Settings → Regenerate CA certificate`
2. Restart Burp completely
3. Re-export DER, re-run `convert_der_to_pem.py`
4. Re-test

---

### Error 3: WinError 10061

```
URLError: <urlopen error [WinError 10061]
No connection could be made because the target machine actively refused it>
```

**Cause:** Burp Suite Intercept was ON.

**Fix:** `Proxy → Intercept → turn Intercept OFF`.

---

## Part 5 — Setting Proxy Variables in Windows CMD

Environment variables in Windows CMD use `%VARIABLE%` syntax (not `$VARIABLE` as in Linux/macOS):

```cmd
:: Set proxy routing
set HTTP_PROXY=http://127.0.0.1:8080
set HTTPS_PROXY=http://127.0.0.1:8080

:: Enable in the Python client
set BURP_PROXY_ENABLED=true
set BURP_CA_PEM_PATH=D:\Git Project\claude_api\burp-ca.pem

:: Verify (must use % not $)
echo %HTTP_PROXY%
echo %BURP_PROXY_ENABLED%
```

In PowerShell:

```powershell
$env:HTTP_PROXY = "http://127.0.0.1:8080"
$env:HTTPS_PROXY = "http://127.0.0.1:8080"
$env:BURP_PROXY_ENABLED = "true"
$env:BURP_CA_PEM_PATH = "D:\Git Project\claude_api\burp-ca.pem"
```

> **Note:** These environment variables only last for the current terminal session. To set them permanently: Control Panel → System → Advanced System Settings → Environment Variables → System Variables.

---

## Part 6 — Running the Client Through Burp

```cmd
set BURP_PROXY_ENABLED=true
set BURP_CA_PEM_PATH=D:\Git Project\claude_api\burp-ca.pem
set HTTP_PROXY=http://127.0.0.1:8080
set HTTPS_PROXY=http://127.0.0.1:8080
python main.py
```

Send a message. In Burp, go to `Proxy → HTTP History`. You will see a new entry:

```
Host                    Method  URL             Status  Length  TLS
https://api.anthropic.com  POST  /v1/messages    200     1604    ✓
```

---

## Part 7 — What to Look at in HTTP History

### Request panel

```
POST /v1/messages HTTP/2
Host: api.anthropic.com
Content-Type: application/json
x-api-key: sk-ant-[redacted]
anthropic-version: 2023-06-01
Connection: keep-alive

{
  "model": "claude-haiku-4-5-20251001",
  "max_tokens": 1024,
  "messages": [{"role": "user", "content": "test"}],
  "system": "You are a helpful AI assistant..."
}
```

### Response panel — Rate limit headers (visible on every successful response)

```
Anthropic-Ratelimit-Input-Tokens-Limit:      50000
Anthropic-Ratelimit-Input-Tokens-Remaining:  50000
Anthropic-Ratelimit-Output-Tokens-Limit:     10000
Anthropic-Ratelimit-Output-Tokens-Remaining: 10000
Anthropic-Ratelimit-Requests-Limit:          50
Anthropic-Ratelimit-Requests-Remaining:      49
Anthropic-Ratelimit-Requests-Reset:          2026-03-03T18:44:16Z
Anthropic-Ratelimit-Tokens-Limit:            60000
Anthropic-Ratelimit-Tokens-Remaining:        60000
```

These headers exist on every successful response. You do not need to wait for a 429 to know your remaining quota.

---

## Part 8 — Using Repeater for Error Reproduction

Right-click any request in HTTP History → **Send to Repeater**.

In Repeater, you can modify any part of the request and re-send it with one click. The request and response appear side-by-side.

### Error tests to run in Repeater

| Modification | Expected response |
|---|---|
| Change `x-api-key` to `sk-ant-api03-1337` | HTTP 401 — `authentication_error: invalid x-api-key` |
| Add `{"role": "system", ...}` inside messages | HTTP 400 — `Unexpected role "system"` |
| Set `max_tokens: 5` | HTTP 200 — `stop_reason: max_tokens`, truncated response |
| Remove `max_tokens` entirely | HTTP 400 — `max_tokens: field required` |
| Add two user messages in a row | HTTP 422 — alternation violation |
| Change method to GET | HTTP 405 — Method Not Allowed |

---

## Security Notes

- `burp-ca.der` and `burp-ca.pem` are in `.gitignore` — they must never be committed
- The CA certificate allows Burp to decrypt your HTTPS traffic. Only use it in a controlled testing environment
- All testing in this project was performed against the documented Anthropic API — no exploitation, no penetration testing, no bug bounty activity
- Disable proxy variables when not actively using Burp: `set BURP_PROXY_ENABLED=false`

---

## References

- [PortSwigger: Installing Burp's CA Certificate](https://portswigger.net/burp/documentation/desktop/settings/network/tls)
- [Python ssl module documentation](https://docs.python.org/3/library/ssl.html)
- [Anthropic API Documentation](https://docs.anthropic.com/en/api/getting-started)
