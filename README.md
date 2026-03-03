# claude-api

A Python client for the [Anthropic Messages API](https://docs.anthropic.com/en/api/messages), built with a focus on understanding the full request lifecycle — from authentication and error classification to traffic inspection at the HTTP layer.

The project grew out of systematic hands-on study of the API: reading the documentation, implementing the client, deliberately triggering every documented error type, and verifying behaviour at the raw HTTP level using Postman and Burp Suite on Windows. The troubleshooting notes, inline comments, and Q&A reference are a direct record of that process.

---

## Contents

- [Project Structure](#project-structure)
- [Quick Start](#quick-start)
- [Core Client](#core-client)
- [Error Handling](#error-handling)
- [Rate Limits](#rate-limits)
- [Testing Methodology](#testing-methodology)
  - [Python CLI](#python-cli)
  - [Postman](#postman)
  - [Burp Suite](#burp-suite)
- [Windows-Specific: Burp SSL Resolution](#windows-specific-burp-ssl-resolution)
- [Observations from Traffic Inspection](#observations-from-traffic-inspection)
- [Q&A Reference](#qa-reference)
- [Coming Soon](#coming-soon)
- [References](#references)

---

## Project Structure

```
claude_api/
├── claude_client.py          # Core API client
├── main.py                   # Interactive chat and demo runner
├── convert_der_to_pem.py     # Burp CA certificate converter (DER → PEM)
├── .gitignore
│
├── docs/
│   ├── postman/
│   │   └── README.md         # Postman collection setup and all test scenarios
│   └── burp/
│       └── README.md         # Burp Suite proxy setup, Windows SSL troubleshooting
│
├── qa/
│   └── support-qa.md         # 108 Q&A covering the complete API surface
│
└── docs/screenshots/         # Traffic captures referenced throughout this document
```

---

## Quick Start

**Requirements:** Python 3.8+, no external dependencies (uses stdlib only).

```cmd
:: 1. Set your API key
set ANTHROPIC_API_KEY=sk-ant-your-key-here

:: 2. Run interactive chat
python main.py

:: 3. Run all demonstrations
python main.py --demo-all

:: 4. Explain a specific error code
python main.py --explain 429
python main.py --explain 422

:: 5. Route traffic through Burp Suite
set BURP_PROXY_ENABLED=true
set BURP_CA_PEM_PATH=D:\path\to\burp-ca.pem
python main.py
```

Get your API key at [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys).

---

## Core Client

`claude_client.py` implements the Anthropic Messages API without any third-party dependencies — only Python standard library (`urllib`, `ssl`, `json`, `logging`).

### Authentication

Every request includes three required headers:

```python
self.headers = {
    "Content-Type": "application/json",
    "x-api-key": self.api_key,          # authentication credential
    "anthropic-version": "2023-06-01",  # API version pin
}
```

The `anthropic-version` header pins the API to a specific behaviour version. Omitting it returns HTTP 400. Pinning to a version means your integration does not silently break when Anthropic updates the API — you control when to adopt new behaviour.

### Session Management

The API is stateless — there is no server-side session. To maintain a multi-turn conversation, every request must include the complete message history:

```python
session = client.create_session()

# Turn 1
client.send_message("What is rate limiting?", session=session)

# Turn 2 — full history is included automatically
client.send_message("How do I handle it?", session=session)
```

`ConversationSession` accumulates the message array and tracks cumulative token usage across the session. The session stores the history in memory for the lifetime of the process.

### Message Role Alternation

The API enforces a strict rule: messages must alternate between `user` and `assistant` roles. Consecutive messages with the same role return HTTP 422:

```python
# Valid
messages = [
    {"role": "user",      "content": "What is Python?"},
    {"role": "assistant", "content": "Python is a programming language."},
    {"role": "user",      "content": "Give me an example."},
]

# Invalid — triggers HTTP 422
messages = [
    {"role": "user", "content": "What is Python?"},
    {"role": "user", "content": "Also explain JavaScript."},  # same role
]
```

### Retry Logic

Retryable errors (429, 500, 529) are automatically retried with exponential backoff and full jitter:

```python
def _calculate_backoff(self, attempt, retry_after=None):
    if retry_after:
        return float(retry_after)  # honour the Retry-After header
    backoff = min(self.BASE_BACKOFF_SECONDS * (2 ** attempt), self.MAX_BACKOFF_SECONDS)
    jitter  = backoff * self.JITTER_FACTOR * (random.random() * 2 - 1)
    return max(0.1, backoff + jitter)
```

Jitter prevents the thundering herd problem: without it, all clients that hit a 429 simultaneously would retry at the same moment, producing a second wave of rate limit errors. Randomising the wait time distributes retries across time.

### Context Window Monitoring

Before each request, the client estimates the token count and warns at 85% of the model's context window limit:

```python
WARNING | Context window warning: estimated 172,000 tokens
         against 200,000 limit (86%). Consider summarising
         earlier messages or starting a new session.
```

When the window is actually exceeded, the API returns HTTP 400 with a context-specific message. The client catches this and sets `error_type = "context_window_overflow"`, allowing the application to handle it specifically.

### Logging

Every request and response is logged to two destinations:

- `logs/claude_api_YYYYMMDD.log` — human-readable, timestamped
- `logs/interactions.jsonl` — one JSON object per interaction, machine-readable

```
2026-03-03 18:44:15 | INFO | claude_client | API success | latency=483ms |
                     input_tokens=37 | output_tokens=5 | stop_reason=max_tokens
```

The `stop_reason` field is logged on every successful response because it is the primary diagnostic signal for truncation issues.

### Proxy Integration (Burp Suite)

The proxy is controlled entirely via environment variables — no code changes required to toggle it:

```cmd
set BURP_PROXY_ENABLED=true
set BURP_CA_PEM_PATH=D:\Git Project\claude_api\burp-ca.pem
```

When enabled, the client builds a custom SSL context loading the Burp CA PEM file explicitly, and attaches a proxy handler routing HTTPS traffic through `127.0.0.1:8080`. See [Windows-Specific: Burp SSL Resolution](#windows-specific-burp-ssl-resolution) for the certificate troubleshooting walkthrough.

---

## Error Handling

All eight HTTP error types the Anthropic API can return are registered in `ERROR_REGISTRY` with root causes, remediation steps, and retry classification.

| Code | Name | Retryable | Primary cause |
|------|------|-----------|---------------|
| 400 | bad_request | ✗ | Missing `max_tokens`, wrong model string, context overflow, `role:system` in messages |
| 401 | authentication_error | ✗ | Invalid or missing `x-api-key` |
| 403 | permission_error | ✗ | Model not enabled for plan tier |
| 404 | not_found | ✗ | Wrong endpoint URL or deprecated model |
| 405 | method_not_allowed | ✗ | GET instead of POST on `/v1/messages` |
| 422 | unprocessable_entity | ✗ | Message role alternation violation |
| 429 | rate_limit_error | ✓ | RPM or TPM limit exceeded |
| 500 | api_error | ✓ | Anthropic internal error |
| 529 | overloaded_error | ✓ | API under high global load |

Run the documentation for any code from the command line:

```cmd
python main.py --explain 422
```

```
============================================================
HTTP 422 — UNPROCESSABLE_ENTITY
============================================================
Description:  Request is syntactically valid JSON but semantically
              invalid for the API.
Common causes:
  • Consecutive messages with the same role (must alternate user/assistant)
  • First message is 'assistant' role — conversations must start with 'user'
  • system prompt placed inside messages array with role:'system'
Remediation:  Check message alternation: user → assistant → user → assistant.
  ✗ Not retryable — fix the request.
============================================================
```

### stop_reason — the most important response field

`stop_reason` determines whether a successful HTTP 200 response is actually complete:

| Value | Meaning | Action |
|-------|---------|--------|
| `end_turn` | Complete response | None |
| `max_tokens` | **Response is incomplete** — hit `max_tokens` limit | Increase `max_tokens` |
| `tool_use` | Model is requesting a function call | Execute tool, send result back |
| `stop_sequence` | Custom stop string was matched | Intended behaviour |

A customer reporting "Claude stops mid-sentence" always has `stop_reason: max_tokens`.

---

## Rate Limits

Rate limits operate on two independent dimensions simultaneously. Either can trigger HTTP 429 regardless of the other:

- **RPM** — Requests Per Minute
- **TPM** — Tokens Per Minute (input + output combined)

### Rate limit headers

Rate limit status is returned on **every successful response**, not only on 429 errors. These are visible in Burp Suite's response panel and in Postman's Headers tab:

```
Anthropic-Ratelimit-Requests-Limit:          50
Anthropic-Ratelimit-Requests-Remaining:      49
Anthropic-Ratelimit-Requests-Reset:          2026-03-03T18:44:16Z
Anthropic-Ratelimit-Tokens-Limit:            60000
Anthropic-Ratelimit-Tokens-Remaining:        60000
Anthropic-Ratelimit-Input-Tokens-Limit:      50000
Anthropic-Ratelimit-Output-Tokens-Limit:     10000
```

Proactively monitoring these headers allows an application to throttle itself before hitting the limit, avoiding 429 errors entirely.

*Observed during this project: after the first request, `Anthropic-Ratelimit-Requests-Remaining` dropped from 50 to 49, confirming real-time quota tracking.*

### Retry-After header

On 429 responses, Anthropic returns a `Retry-After` header with the number of seconds to wait. The client honours this value over its own computed backoff.

---

## Testing Methodology

Three complementary approaches were used, each providing a different level of visibility into the API's behaviour.

### Python CLI

`claude_client.py` was used to exercise the API programmatically: multi-turn conversation, session management, deliberate error injection, context window observation, and truncation testing.

Key test performed — forced truncation:

```python
response = client.send_message(
    "Write a detailed explanation of how TCP/IP works.",
    max_tokens=5,
)
# Result: HTTP 200, stop_reason="max_tokens", response="# Hello! "
# Log: output_tokens=5 | stop_reason=max_tokens
```

![CLI working with logging](docs/screenshots/Screenshot_2026-03-03_175350.png)

---

### Postman

10 test requests in a dedicated collection covering the full error surface:

| Request | Modification | Result |
|---------|-------------|--------|
| Playing around (baseline) | None | 200 OK |
| 200 OK | System prompt correct placement | 200 OK, pirate response |
| 200 Truncated | `max_tokens: 5` | 200 OK, `stop_reason: max_tokens` |
| 400 Bad Model | Wrong model string | 400 invalid_request_error |
| 400 No_max_tokens | `max_tokens` omitted | 400 field required |
| 401 Bad Key | `x-api-key: sk-ant-api03-1337` | 401 authentication_error |
| 401 No Key | Header removed | 401 authentication_error |
| 400 Same Role | Two user messages | 400 invalid_request_error |
| 400 Assistant Start | First message is assistant | 400 invalid_request_error |
| GET 405 — Method Not Allowed | GET instead of POST | 405 Method Not Allowed |

![Postman collection](docs/screenshots/2026-03-03_17_22_17-Playing_around_-_My_Workspace.png)

*System prompt test — correct placement returns HTTP 200 with the expected pirate response. Wrong placement (role:system inside messages) returns HTTP 400.*

![System prompt correct](docs/screenshots/2026-03-03_17_23_33-Playing_around_-_My_Workspace.png)

Full setup guide: [`docs/postman/README.md`](docs/postman/README.md)

---

### Burp Suite

All Python API traffic was routed through Burp Suite Community Edition as an intercepting proxy, providing direct inspection of every header, request body, and response at the HTTP layer.

**HTTP History — successful 200 request captured:**

![Burp HTTP History](docs/screenshots/2026-03-03_18_47_39-Burp_Suite_Community_Edition_v2026_1_5_-_Temporary_Project.png)

The Inspector panel (right side) shows parsed request headers: `X-Api-Key`, `Anthropic-Version`, `Content-Length`, `User-Agent: Python-urllib/3.13`. The response section shows the full `Anthropic-Ratelimit-*` header set.

**Repeater — 401 authentication error:**

![Burp Repeater 401](docs/screenshots/2026-03-03_18_51_45-Burp_Suite_Community_Edition_v2026_1_5_-_Temporary_Project.png)

Fake key `sk-ant-api03-1337` in the `x-api-key` header. Response: `HTTP 401`, `type: authentication_error`, `message: invalid x-api-key`, `X-Should-Retry: false`.

**Repeater — 400 system role in messages:**

![Burp Repeater 400](docs/screenshots/2026-03-03_18_53_46-Burp_Suite_Community_Edition_v2026_1_5_-_Temporary_Project.png)

Adding `{"role": "system", "content": "test"}` inside the messages array. Response: `HTTP 400`, `Unexpected role "system". The Messages API accepts a top-level system parameter, not "system" as an input message role.`

**Repeater — max_tokens truncation:**

![Burp Repeater max_tokens](docs/screenshots/2026-03-03_18_50_10-Burp_Suite_Community_Edition_v2026_1_5_-_Temporary_Project.png)

`max_tokens: 5` in the request. Response: HTTP 200, `stop_reason: "max_tokens"` (highlighted), `output_tokens: 5`, `text: "# Hello! "`. Rate limit headers visible: `Anthropic-Ratelimit-Requests-Remaining: 49/50`.

Full setup guide: [`docs/burp/README.md`](docs/burp/README.md)

---

## Windows-Specific: Burp SSL Resolution

Routing Python traffic through Burp on Windows required resolving a chain of SSL errors. Each error was distinct and required a separate fix.

### Error chain and resolution

**Stage 1 — WinError 10061: connection refused**

Cause: Burp Suite Intercept was ON, blocking all traffic.
Fix: `Proxy → Intercept → Intercept OFF`.

**Stage 2 — CERTIFICATE_VERIFY_FAILED: Missing Authority Key Identifier**

Cause: Python 3.10+ enforces X.509 extensions that Burp Community CA lacks. Installing the certificate into Windows Trusted Root had no effect — Python's `urllib` does not use the Windows certificate store.
Fix: Convert DER → PEM, load explicitly via `ssl.SSLContext.load_verify_locations()`.

**Stage 3 — CERTIFICATE_VERIFY_FAILED: CA cert does not include key usage extension**

Cause: Python 3.13 added `VERIFY_X509_STRICT`. Burp Community CA sometimes lacks the Key Usage extension.
Fix: Same explicit PEM load, plus regenerating the Burp CA if the error persisted.

**Stage 4 — Environment variable syntax (CMD vs Linux)**

Cause: Windows CMD uses `%VARIABLE%` syntax, not `$VARIABLE`. Running `echo $HTTP_PROXY` prints the literal string `$HTTP_PROXY`.
Fix: `echo %HTTP_PROXY%`.

### The certificate conversion utility

`convert_der_to_pem.py` handles the DER → PEM conversion and validates the result:

```cmd
python convert_der_to_pem.py

Burp Suite CA Certificate — DER to PEM Converter
==================================================
  [OK] Converted burp-ca.der -> burp-ca.pem
       DER size: 1,234 bytes
       PEM size: 1,748 bytes

Verifying PEM with Python ssl module...
  [OK] SSL verification passed — burp-ca.pem is valid
```

The complete troubleshooting walkthrough is in [`docs/burp/README.md`](docs/burp/README.md).

---

## Observations from Traffic Inspection

Direct HTTP-level observation via Burp Suite revealed several details not visible at the application layer:

**Rate limit headers on every response.** `Anthropic-Ratelimit-Requests-Remaining` decrements with each request and is available on every HTTP 200 response. Applications can monitor their quota in real time without waiting for a 429.

**API key in every request header.** The `x-api-key` value is transmitted in plain text over TLS on every single request. There is no session token, no caching of credentials. This confirms that embedding an API key in a client-side application (mobile app, browser JS) is always a security risk — the key is extractable from any intercepted request.

**TLS 1.3 enforcement.** The connection to `api.anthropic.com` uses TLS 1.3. Visible in Burp's connection details.

**Cloudflare infrastructure.** `Server: cloudflare` and `Cf-Ray` headers confirm Anthropic routes traffic through Cloudflare for edge protection. The `X-Envoy-Upstream-Service-Time` header reflects time spent at Anthropic's origin, distinct from end-to-end client latency.

**`inference_geo: not_available`.** Present in every response body. Internal routing telemetry indicating the geographic inference region is not disclosed for this request tier.

**`X-Should-Retry` header.** Present on error responses. `false` on 401 (confirmed: not retryable). Correlates with the `retry` classification in `ERROR_REGISTRY`.

**`Anthropic-Organization-Id`.** Present in response headers. Identifies the account associated with the API key used.

---

## Q&A Reference

[`qa/support-qa.md`](qa/support-qa.md) contains 108 questions and answers covering the complete API surface, grouped by category:

- Authentication & API Keys (Q1–Q8)
- Request Structure (Q9–Q16)
- Error Codes — Complete Reference (Q15–Q24)
- Rate Limits (Q25–Q29)
- Context Window & Token Management (Q30–Q35)
- Streaming (Q36–Q39)
- Multimodal — Vision (Q40–Q42)
- Tool Use / Function Calling (Q43–Q46)
- Prompt Caching (Q47–Q49)
- Models (Q50–Q53)
- Batch API (Q54–Q55)
- Security (Q56–Q60)
- Debugging & Diagnostics (Q61–Q65)
- Windows-Specific (Q66–Q71)
- Advanced Topics (Q72–Q82)
- Cost Optimisation (Q83–Q87)
- Error Scenarios from This Project (Q88–Q93)
- Postman-Specific (Q94–Q97)
- Burp Suite-Specific (Q98–Q103)
- Edge Cases & Rare Issues (Q104–Q108)

---

## Coming Soon

### SPECTRE — Hack The Box Machine

A custom HTB machine built from the ground up: 6 chained exploits (BOLA → JWT Algorithm Confusion → SSRF → Redis RCE → Apache Kafka Command Injection → eBPF Privilege Escalation), a custom Go gRPC server, live eBPF kernel probe, Apache Kafka KRaft pipeline, and a React frontend. 3,058-line idempotent build script with 105 automated health checks. Currently under review by HTB.

When SPECTRE reaches Retired status on HTB, the full writeup will be published — covering the design decisions, the debugging process, and the exploit chain in detail. The build script, all source code, and infrastructure-as-code will be published too.

### Part II

The next machine is already in development.

### Offensive Security Framework

A private security research project. Currently in development at [github.com/xmp00/Offensive-Security-Framework](https://github.com/xmp00/Offensive-Security-Framework). Will be made public on completion.

---

## References

| Resource | URL |
|---|---|
| Anthropic API — Getting Started | [docs.anthropic.com/en/api/getting-started](https://docs.anthropic.com/en/api/getting-started) |
| Messages API Reference | [docs.anthropic.com/en/api/messages](https://docs.anthropic.com/en/api/messages) |
| Error Codes | [docs.anthropic.com/en/api/errors](https://docs.anthropic.com/en/api/errors) |
| Rate Limits | [docs.anthropic.com/en/api/rate-limits](https://docs.anthropic.com/en/api/rate-limits) |
| Models Overview | [docs.anthropic.com/en/docs/about-claude/models/overview](https://docs.anthropic.com/en/docs/about-claude/models/overview) |
| Prompt Caching | [docs.anthropic.com/en/docs/build-with-claude/prompt-caching](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching) |
| Tool Use | [docs.anthropic.com/en/docs/build-with-claude/tool-use](https://docs.anthropic.com/en/docs/build-with-claude/tool-use) |
| Batch API | [docs.anthropic.com/en/docs/build-with-claude/batch-api](https://docs.anthropic.com/en/docs/build-with-claude/batch-api) |
| Status Page | [status.anthropic.com](https://status.anthropic.com) |
| Burp Suite CA Certificate Guide | [portswigger.net/burp/documentation/desktop/settings/network/tls](https://portswigger.net/burp/documentation/desktop/settings/network/tls) |

---

[linkedin.com/in/rjurkevich](https://linkedin.com/in/rjurkevich) · [https://app.hackthebox.com/public/users/342660](https://app.hackthebox.com/profile/purplebyteone)
