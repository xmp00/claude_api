# Claude API — Support Q&A Reference

Questions and answers that I have faced working on this project. Covering authentication, request structure,
error handling, rate limits, context management, streaming, multimodal inputs,
tool use, cost optimisation, and security. Organised by category.

---

## Authentication & API Keys

**Q1. How do I authenticate with the Claude API?**

Pass your key in the `x-api-key` header on every request. There are no sessions, no OAuth flows, no Bearer tokens — every request is independently authenticated.

```
POST https://api.anthropic.com/v1/messages
x-api-key: sk-ant-api03-...
anthropic-version: 2023-06-01
Content-Type: application/json
```

---

**Q2. My API key stopped working. What happened?**

Keys can be revoked manually, expire if unused for an extended period, or become invalid if the account's billing lapses. Go to `console.anthropic.com → API Keys`, verify the key status, and create a fresh one if needed. When copying, ensure no trailing whitespace or newline characters are included.

---

**Q3. I'm getting HTTP 401 even though my key looks correct.**

The three most common causes:
1. Trailing whitespace or a newline at the end of the copied key
2. The key belongs to a different Anthropic account
3. The key was generated before a billing issue and silently invalidated

Create a new key, copy it character-by-character, and test with a minimal Postman request before adding it to your application.

---

**Q4. Can I embed my API key in a mobile app?**

No. Every request sends the key in the `x-api-key` header in plain text over TLS. An attacker who decompiles your mobile app binary can extract the key and make requests at your expense with no way to stop them short of revoking the key. The correct architecture: your mobile app calls your own backend server; your backend calls Anthropic. The key never leaves your server.

---

**Q5. Can I have multiple API keys for the same account?**

Yes. You can create multiple keys at `console.anthropic.com → API Keys`. Recommended practice: one key per application or environment (dev/staging/prod). This allows you to revoke a single environment's access without affecting others.

---

**Q6. What does the `anthropic-version` header do? Is it required?**

It pins the API behaviour to a specific version. Without it, the API returns an error. Currently the required value is `2023-06-01`. This header exists so that future API changes do not silently break your integration — you control when to adopt new behaviour by updating the version string.

---

## Request Structure

**Q7. What is the minimum valid request body?**

```json
{
  "model": "claude-haiku-4-5-20251001",
  "max_tokens": 100,
  "messages": [
    {"role": "user", "content": "Hello"}
  ]
}
```

`model`, `max_tokens`, and `messages` are all required. Omitting any of them returns HTTP 400.

---

**Q8. Where does the system prompt go?**

At the top level of the request body, not inside the messages array:

```json
{
  "model": "claude-haiku-4-5-20251001",
  "max_tokens": 200,
  "system": "You are a concise technical support engineer.",
  "messages": [
    {"role": "user", "content": "What is a 429 error?"}
  ]
}
```

Placing it inside messages with `"role": "system"` returns HTTP 400:
`messages: Unexpected role "system". The Messages API accepts a top-level system parameter, not "system" as an input message role.`

---

**Q9. Why do I get HTTP 422 after a few messages?**

The API enforces strict message role alternation: `user → assistant → user → assistant`. Two messages with the same role in a row, or a conversation that starts with `assistant`, returns 422 Unprocessable Entity. This is the most common integration bug in multi-turn chat systems.

Valid:
```json
[
  {"role": "user",      "content": "What is Python?"},
  {"role": "assistant", "content": "Python is a programming language."},
  {"role": "user",      "content": "Show me an example."}
]
```

Invalid (two user messages in a row):
```json
[
  {"role": "user", "content": "What is Python?"},
  {"role": "user", "content": "Also explain JavaScript."}
]
```

---

**Q10. My responses are being cut off mid-sentence. What is wrong?**

Nothing is wrong with the API — `max_tokens` is too low. Check the `stop_reason` field in the response. If it reads `"max_tokens"` instead of `"end_turn"`, the model was interrupted at the token limit. The response is incomplete. Increase `max_tokens`. Current limits: Haiku up to 16,000, Sonnet up to 64,000, Opus up to 32,000.

---

**Q11. What is `stop_reason` and what values can it have?**

| Value | Meaning |
|-------|---------|
| `end_turn` | Model finished naturally. Response is complete. |
| `max_tokens` | Hit the `max_tokens` limit. Response is **incomplete**. |
| `stop_sequence` | A custom stop sequence was matched. Response ended there. |
| `tool_use` | Model wants to call a function. Application must handle it. |

Always check this field. A customer reporting truncated responses has `stop_reason: max_tokens`.

---

**Q12. Can I use the GET method on the Messages endpoint?**

No. `/v1/messages` only accepts `POST`. A GET request returns HTTP 405 Method Not Allowed. This was confirmed during testing with a deliberate GET request in Postman.

---

**Q13. What is `temperature` and when should I change it?**

Controls response randomness. Range: 0.0 to 1.0.

- `0.0` — identical output every time for the same input. Use for classification, structured extraction, code generation.
- `1.0` — default. Balanced creativity.
- Values above `0.7` — more creative/diverse, less predictable. Use for brainstorming, creative writing.

If a customer reports inconsistent or unpredictable responses, ask for their `temperature` setting.

---

**Q14. I am not passing `temperature` but my responses still vary. Why?**

The default temperature is 1.0, which includes sampling randomness. For deterministic output, explicitly set `"temperature": 0`. Note that even at temperature 0, minor variations can occur due to numerical precision in GPU computations.

---

## Error Codes — Complete Reference

**Q15. What does HTTP 400 mean from the Claude API?**

Bad request — the payload is malformed. Common causes: missing `max_tokens`, wrong model string, empty messages array, invalid JSON structure, system prompt placed inside messages, or context window exceeded. Fix the request before retrying — retrying will not help.

---

**Q16. What does HTTP 401 mean?**

Authentication failed — invalid or missing `x-api-key`. Verify the key at `console.anthropic.com`. Not retryable.

---

**Q17. What does HTTP 403 mean?**

Permission denied — the key is valid but lacks access to the requested model or feature. Often seen when trying to access a beta feature without the required `anthropic-beta` header. Check your account tier.

---

**Q18. What does HTTP 404 mean?**

Resource not found. Usually a wrong endpoint URL. The correct URL is `https://api.anthropic.com/v1/messages`. Also occurs if referencing a deprecated model string.

---

**Q19. What does HTTP 422 mean?**

Unprocessable entity — valid JSON but semantically invalid. Almost always a message role alternation violation or `role: system` inside the messages array. Fix the request structure.

---

**Q20. What does HTTP 429 mean?**

Rate limit exceeded. Two independent limits: Requests Per Minute (RPM) and Tokens Per Minute (TPM). Either can trigger 429. **Retryable** — use exponential backoff with jitter. Read the `Retry-After` response header for the minimum wait time.

---

**Q21. What does HTTP 500 mean?**

Internal server error on Anthropic's side. Rare. **Retryable** — wait and retry with backoff. Check `https://status.anthropic.com` for active incidents.

---

**Q22. What does HTTP 529 mean?**

API overloaded — too much global demand. Distinct from 500. **Retryable** — exponential backoff resolves it automatically. Common during model launches.

---

**Q23. What is the `X-Should-Retry` response header?**

Anthropic includes this header on error responses. `X-Should-Retry: true` means retrying with backoff is appropriate. `X-Should-Retry: false` means the error is deterministic and retrying will not help — fix the request.

---

## Rate Limits

**Q24. How does rate limiting work on the Claude API?**

Two independent dimensions:
- **RPM** (Requests Per Minute) — number of API calls
- **TPM** (Tokens Per Minute) — total token volume across all requests

Both can independently trigger HTTP 429. A single request with a very large context can hit TPM even if RPM is not exceeded.

Rate limit status is visible in every successful response header — you do not have to wait for a 429 to see your remaining capacity:

```
Anthropic-Ratelimit-Requests-Limit:     50
Anthropic-Ratelimit-Requests-Remaining: 49
Anthropic-Ratelimit-Requests-Reset:     2026-03-03T18:44:16Z
Anthropic-Ratelimit-Tokens-Limit:       60000
Anthropic-Ratelimit-Tokens-Remaining:   60000
Anthropic-Ratelimit-Input-Tokens-Limit: 50000
Anthropic-Ratelimit-Output-Tokens-Limit: 10000
```

These headers are visible in Burp Suite's response panel on every successful request.

---

**Q25. What is exponential backoff with jitter?**

A retry strategy that increases wait time after each failure, with added randomness.

```python
import time, random

def backoff(attempt, base=1.0, cap=60.0, jitter=0.25):
    delay = min(base * (2 ** attempt), cap)
    delay += delay * jitter * (random.random() * 2 - 1)
    return max(0.1, delay)

# Results: ~1s, ~2s, ~4s, ~8s... with ±25% randomness each time
```

The jitter component is essential: without it, all clients that received the same 429 simultaneously retry at the same moment, creating a second wave of rate limit errors ("thundering herd"). Randomised jitter distributes retries across time.

---

**Q26. My application hits 429 constantly. What should I check?**

1. Are multiple processes sharing one API key without coordination? Add a request queue.
2. Is `max_tokens` set much higher than needed? This burns TPM even on short conversations.
3. Are you polling in a tight loop? Add deliberate pauses between requests.
4. Check `Anthropic-Ratelimit-Requests-Remaining` in response headers — monitor consumption before hitting zero.
5. Consider upgrading your plan tier for higher limits.

---

**Q27. The `Retry-After` header — what format is it in and how do I use it?**

It is an integer representing seconds to wait before the next request. Always prefer it over your computed backoff value. In the client:

```python
retry_after_header = response.headers.get("Retry-After")
if retry_after_header:
    wait = float(retry_after_header)
else:
    wait = exponential_backoff(attempt)
time.sleep(wait)
```

---

## Context Window & Token Management

**Q28. What is the context window?**

The maximum number of tokens a model can process in a single request, including both input (your messages + system prompt) and output (the response). All current Claude models support 200,000 tokens.

---

**Q29. How do tokens map to words?**

Roughly 1 token ≈ 4 characters or ¾ of a word in English. A 200,000 token context window holds approximately 150,000 words — about the length of two full novels. Code and non-English text tend to tokenise differently.

---

**Q30. What happens when my conversation history fills the context window?**

The API returns HTTP 400 with a specific message about context length. The client in this project detects this condition and logs: `CONTEXT WINDOW OVERFLOW`. Resolution options: (1) start a new session, (2) summarise earlier turns into a shorter user message, (3) remove old messages from the history array.

---

**Q31. How do I prevent context overflow in a long-running chatbot?**

Implement a summarisation step when the conversation approaches the limit. When token count exceeds a threshold (e.g., 80% of the context window), send a separate API call asking Claude to summarise the conversation so far. Replace the message history with the summary as a single user message.

---

**Q32. Does the token count include the system prompt?**

Yes. The system prompt is included in `input_tokens` in the response usage block. For applications with long system prompts (hundreds or thousands of tokens), prompt caching significantly reduces cost.

---

**Q33. How do I check how many tokens a request will use before sending it?**

You cannot know the exact count without sending the request — the API returns the authoritative count in `response.usage.input_tokens` and `response.usage.output_tokens`. You can estimate with the ~4 characters per token rule, but treat this as approximate. The `estimate_tokens()` method in `claude_client.py` implements this for pre-flight warnings.

---

## Streaming

**Q34. How do I show responses progressively in my app instead of waiting for the full response?**

Add `"stream": true` to your request body. The response arrives as Server-Sent Events (SSE), one content delta at a time.

```json
{
  "model": "claude-haiku-4-5-20251001",
  "max_tokens": 500,
  "stream": true,
  "messages": [{"role": "user", "content": "Tell me a story."}]
}
```

Each chunk looks like:
```
data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Once"}}
data: {"type":"content_block_delta","delta":{"type":"text_delta","text":" upon"}}
data: {"type":"message_stop"}
```

---

**Q35. How do I get the token count when streaming?**

The final SSE event before `message_stop` is `message_delta`, which includes the usage object:

```json
{"type": "message_delta", "usage": {"output_tokens": 87}}
```

The `input_tokens` count comes from the `message_start` event at the beginning of the stream.

---

**Q36. Why is streaming harder to debug than standard responses?**

There is no single JSON object to inspect — the response is a stream of small text events. To reconstruct the full response, you must concatenate all `text_delta` values. In Burp Suite, streaming responses appear as chunked transfer encoding in HTTP History.

---

**Q37. Can streaming be used with tool use?**

Yes. Tool use events (`tool_use` blocks) arrive as streaming content blocks. The application must buffer them to assemble the complete tool call before executing.

---

## Multimodal — Vision

**Q38. Can I send an image to Claude?**

Yes. Images are sent as base64-encoded strings in the message content array. Supported formats: JPEG, PNG, GIF, WebP. Maximum 5MB per image. Up to 20 images per request.

```json
{
  "model": "claude-haiku-4-5-20251001",
  "max_tokens": 300,
  "messages": [{
    "role": "user",
    "content": [
      {
        "type": "image",
        "source": {
          "type": "base64",
          "media_type": "image/png",
          "data": "<base64-encoded-bytes>"
        }
      },
      {"type": "text", "text": "What does this error message say?"}
    ]
  }]
}
```

---

**Q39. When content is an array instead of a string, does anything change about authentication or error handling?**

No. The same headers, the same error codes, the same retry logic applies. The only change is the structure of the `content` field — it becomes an array of typed blocks instead of a plain string.

---

**Q40. Can I send a URL instead of base64?**

Yes — use `"type": "url"` in the source object instead of `"type": "base64"`. The URL must be publicly accessible. Anthropic's servers fetch the image at request time.

```json
{
  "type": "image",
  "source": {
    "type": "url",
    "url": "https://example.com/screenshot.png"
  }
}
```

---

## Tool Use (Function Calling)

**Q41. What is tool use?**

A mechanism where Claude can request that your application run a function on its behalf. You define tools with a name, description, and parameter schema. If Claude determines it needs data from a tool to answer, it responds with `stop_reason: "tool_use"` and a `tool_use` content block containing the function name and arguments it chose.

---

**Q42. What does `stop_reason: tool_use` mean?**

The model has paused and is asking your application to execute a function. The conversation is not complete. Your application must run the requested function, then send the result back in a new `tool_result` message. If your code ignores `tool_use` stop reason, the conversation appears to "stop randomly" — a common support ticket.

---

**Q43. How do I send a tool result back?**

Add a new user message with a `tool_result` content block:

```json
{
  "role": "user",
  "content": [{
    "type": "tool_result",
    "tool_use_id": "<id from the tool_use block>",
    "content": "The weather in Dublin is 8°C and cloudy."
  }]
}
```

---

**Q44. A customer says "Claude stops responding after a certain message." How do I diagnose this?**

Check `stop_reason` in the response. If it is `tool_use`, the application is not handling the tool call cycle — it stops after Claude's tool request instead of sending the result and continuing. If it is `max_tokens`, the response was truncated. If it is `end_turn` but the UI is not rendering, it is a client-side display issue.

---

## Prompt Caching

**Q45. What is prompt caching and when should I use it?**

Prompt caching stores a portion of your input prompt on Anthropic's servers so it does not need to be processed again on subsequent requests. Cached tokens cost 10% of the standard input token price. Minimum 1,024 tokens required for a block to be cached.

Use it when your requests share a large common prefix — for example, a long system prompt with documentation, a code base, or a reference document.

```json
{
  "system": [{
    "type": "text",
    "text": "<your very long system prompt>",
    "cache_control": {"type": "ephemeral"}
  }]
}
```

---

**Q46. How do I verify that caching is working?**

The response `usage` block includes `cache_creation_input_tokens` (tokens written to cache) and `cache_read_input_tokens` (tokens read from cache). If `cache_read_input_tokens` is greater than 0, the cache was hit.

```json
"usage": {
  "input_tokens": 10,
  "cache_creation_input_tokens": 0,
  "cache_read_input_tokens": 1850,
  "output_tokens": 93
}
```

---

**Q47. How long does a cached prompt last?**

The `ephemeral` cache type has a minimum 5-minute TTL. It resets with each cache hit. For consistently-used prompts, caching remains active as long as the prompt is used regularly.

---

## Models

**Q48. What are the current Claude models and when should I use each?**

| Model | Best for | Context | Max Output |
|-------|----------|---------|------------|
| `claude-haiku-4-5-20251001` | High volume, cost-sensitive, fast responses | 200K | 16K |
| `claude-sonnet-4-20250514` | Balanced quality and cost, everyday tasks | 200K | 64K |
| `claude-opus-4-20250514` | Most complex reasoning, highest quality | 200K | 32K |

Current pricing reference: [docs.anthropic.com/en/docs/about-claude/models/overview](https://docs.anthropic.com/en/docs/about-claude/models/overview)

---

**Q49. I get HTTP 400 with "model: field required" even though I set the model. What happened?**

The model string is likely misspelled. The API requires the exact string — no partial matches, no aliases. Common mistakes: `"claude-haiku"` (missing version suffix), `"claude-3-haiku"` (wrong family), `"claude-sonnet"` (missing date). Use the complete string from the models documentation page.

---

**Q50. How do I know when a model is deprecated?**

Anthropic publishes deprecation notices in advance. Model deprecations are announced at `docs.anthropic.com/en/docs/about-claude/models/overview` with sunset dates. Deprecated models return HTTP 400 after the sunset date. Subscribe to `status.anthropic.com` for notifications.

---

## Batch API

**Q51. I need to process 50,000 documents. What is the right approach?**

The Batch API. It accepts up to 100,000 requests per batch, processes them within 24 hours, and charges 50% of standard token pricing. Not for real-time use cases — results are available via polling or webhook after processing completes.

```
POST https://api.anthropic.com/v1/messages/batches
```

---

**Q52. What are the limitations of the Batch API?**

Responses are not immediate — typically within 1 hour for small batches, up to 24 hours for large ones. Streaming is not available. Use the standard Messages API for any real-time or interactive use case.

---

## Security

**Q53. Is API traffic encrypted?**

Yes. TLS 1.3 is enforced. This was confirmed during this project by inspecting traffic through Burp Suite, which showed the protocol version in the connection details. Downgrade to TLS 1.2 or below is rejected.

---

**Q54. What information is in the request that could be sensitive?**

The `x-api-key` header (sent on every request), the `system` prompt (often contains proprietary business logic or confidential instructions), and the `messages` array (user data). All of this is encrypted in transit but visible to anyone with access to your server's outbound traffic.

---

**Q55. Should I log API responses in my application?**

Log metadata (timestamp, model, token counts, latency, stop_reason) but be careful about logging the full `messages` array in production — it may contain PII, confidential user queries, or sensitive business data. This project logs metadata to `interactions.jsonl` and omits message content.

---

**Q56. What is the Anthropic-Organization-Id header in responses?**

It identifies the Anthropic organisation associated with the API key used. Visible in Burp Suite responses. Useful for multi-account environments where you need to verify which account's key a request used.

---

**Q57. Can I use the Claude API from a frontend application (browser)?**

Technically yes, but you must never expose your API key in frontend code. Use a backend proxy: your frontend calls your server, your server calls Anthropic. If you embed the key in browser JavaScript, it is trivially extractable from the page source or network inspector.

---

## Debugging & Diagnostics

**Q58. How do I get a request ID to report a bug to Anthropic support?**

Every response includes a `request_id` field in the JSON body and a `Request-Id` response header. When filing a support ticket, include this ID — it allows Anthropic to look up the exact request in their systems.

---

**Q59. What does `X-Envoy-Upstream-Service-Time` in the response mean?**

This is an infrastructure header showing how long Anthropic's backend (via their Envoy proxy) took to process the request, in milliseconds. It is distinct from your end-to-end latency (which includes network time). If this number is high while your total latency is also high, the delay is on Anthropic's side. If your total latency is high but this number is low, the bottleneck is network.

---

**Q60. What does `Server: cloudflare` in the response mean?**

Anthropic routes traffic through Cloudflare for DDoS protection and global edge caching. You are connecting to a Cloudflare edge node, which forwards to Anthropic's origin servers. This is normal and expected.

---

**Q61. My request succeeds in Postman but fails in my code. What do I check?**

1. Confirm all three headers are present: `x-api-key`, `anthropic-version`, `Content-Type`
2. Confirm `Content-Type` is `application/json` — missing it causes 400
3. Confirm the body is valid JSON (no trailing commas, no unquoted strings)
4. Confirm `max_tokens` is present and greater than 0
5. Capture the failing request in Burp Suite and compare it byte-for-byte to the working Postman request

---

**Q62. How do I reproduce a customer's exact error from their description?**

1. Ask for the full request payload and response body (including `request_id`)
2. Reconstruct the request in Burp Suite Repeater
3. Send it and observe the exact error response
4. Modify one field at a time until the request succeeds — the field that changes the outcome is the root cause

---

## Windows-Specific

**Q63. Environment variables are not working in Windows CMD. What is the correct syntax?**

Windows CMD uses `%VARIABLE%` syntax, not `$VARIABLE`:

```cmd
:: Set
set ANTHROPIC_API_KEY=sk-ant-...

:: Read (must use % not $)
echo %ANTHROPIC_API_KEY%

:: Not this (Linux syntax — does not work in CMD)
echo $ANTHROPIC_API_KEY
```

In PowerShell:
```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
Write-Host $env:ANTHROPIC_API_KEY
```

---

**Q64. I installed Burp's CA certificate in Windows Trusted Root but Python still rejects it. Why?**

Python's `urllib` and `ssl` module do not use the Windows certificate store — they use their own bundle. Installing into Windows Trusted Root has no effect on Python. The solution: export the certificate as DER, convert to PEM using `convert_der_to_pem.py`, then load it explicitly:

```python
ctx = ssl.create_default_context()
ctx.load_verify_locations(cafile="burp-ca.pem")
```

This was the core problem encountered and resolved in this project. See `docs/burp/README.md`.

---

**Q65. What is WinError 10061 and what causes it?**

`[WinError 10061] No connection could be made because the target machine actively refused it.`

Burp Suite's Intercept was ON, blocking all traffic. When Intercept is ON, Burp holds each request waiting for manual approval — your client sits there getting a connection refused. Turn Intercept OFF for passive observation: `Proxy → Intercept → Intercept OFF`.

---

**Q66. What is the `CERTIFICATE_VERIFY_FAILED: Missing Authority Key Identifier` error?**

Python 3.10+ enforces stricter X.509 validation. Burp Community Edition's default CA certificate often lacks the Authority Key Identifier extension. Python rejects it even if it appears valid. Fix: load the PEM explicitly via `ctx.load_verify_locations()` rather than relying on the system certificate store.

---

**Q67. What is `CERTIFICATE_VERIFY_FAILED: CA cert does not include key usage extension`?**

Python 3.13 added `VERIFY_X509_STRICT` by default. Burp Community CA sometimes lacks the "Key Usage" extension marking it as a CA certificate. The solution used in this project: convert DER to PEM, load explicitly into the SSL context. If it persists, regenerate the Burp CA: `Proxy → Settings → Regenerate CA`.

---

**Q68. How do I set proxy variables permanently in Windows (not just for the current CMD session)?**

Control Panel → System → Advanced System Settings → Environment Variables → System Variables → New. Add `HTTP_PROXY` and `HTTPS_PROXY` with value `http://127.0.0.1:8080`. These persist across terminal sessions. Setting them in CMD with `set` only lasts until the CMD window is closed.

---

## Advanced Topics

**Q69. What is `inference_geo: not_available` in the response body?**

A field indicating the geographic region where inference was executed. When set to `not_available`, the specific region is not disclosed. This field appeared in Burp Suite inspection during testing and is for Anthropic's internal routing telemetry.

---

**Q70. What is `service_tier: standard` in the response body?**

Indicates the processing tier. `standard` is the default tier. This field is used internally to differentiate request handling. Visible in full response inspection via Burp Suite.

---

**Q71. What is `Cf-Ray` in the response headers?**

A Cloudflare identifier for the specific edge request. Format: `<id>-<datacenter-code>`. Useful if reporting a network-level issue to Cloudflare support. Not relevant for standard API debugging but visible in raw traffic.

---

**Q72. What does `Cf-Cache-Status: DYNAMIC` mean?**

Cloudflare did not cache this response — it was served dynamically from Anthropic's origin. API responses are never cached at the CDN layer because they depend on request content and authentication.

---

**Q73. Can I change which datacenter handles my requests?**

No. Anthropic routes to the nearest available edge location automatically. The `inference_geo` field in the response indicates where inference ran. If you have specific data residency requirements, contact Anthropic for enterprise options.

---

**Q74. What are beta headers and how do I use them?**

Some features require an `anthropic-beta` header to opt in:

```
anthropic-beta: max-tokens-3-5-sonnet-2024-07-15
```

Without the required beta header, attempting to use a beta feature returns HTTP 403. Check the documentation page for each feature to see if a beta header is required.

---

**Q75. What is the `cache_creation` object inside `usage`?**

Extended caching telemetry returned when prompt caching is active:

```json
"cache_creation": {
  "ephemeral_5m_input_tokens": 0,
  "ephemeral_1h_input_tokens": 0
}
```

This breaks down cached tokens by TTL tier. Observed in Burp Suite during testing with `max_tokens: 5` — the field is present even on minimal requests.

---

**Q76. How do stop sequences work?**

You can define an array of strings that, if generated, will cause the model to stop:

```json
{
  "stop_sequences": ["###", "END", "\n\n"]
}
```

When triggered, `stop_reason` is `"stop_sequence"` and `stop_sequence` in the response contains the matched string. Useful for structured output parsing.

---

**Q77. What is the Messages API vs the legacy completions API?**

The Messages API (`/v1/messages`) is the current, supported interface. It uses the `messages` array format with role/content objects. The completions API is legacy and no longer recommended. All current Claude models use the Messages API.

---

**Q78. Can I specify how the assistant turn starts?**

Yes — add an assistant message as the last item in the messages array (before the user message). Claude will continue from where that message left off:

```json
"messages": [
  {"role": "user",      "content": "List three items."},
  {"role": "assistant", "content": "1."}
]
```

Claude will complete starting from "1." This technique is called "prefilling the assistant turn."

---

**Q79. What happens if I set `max_tokens` higher than the model's maximum?**

You receive HTTP 400 with a message about the limit. The API does not silently cap — it rejects the request. Always refer to the model's documented maximum output tokens before setting this value.

---

**Q80. What does `Accept-Encoding: gzip, deflate, br` in the request headers mean?**

Python's `urllib` automatically adds this header, telling the server it can accept compressed responses. Anthropic's servers return compressed responses when appropriate. This header appears in Burp Suite traffic inspection — it is automatically handled by the HTTP client and requires no application-level action.

---

## Cost Optimisation

**Q81. How do I estimate the cost of an API call before making it?**

Estimate input tokens with the ~4 characters/token rule, then:

```python
# Example: Haiku pricing
input_cost  = input_tokens  * 0.0000008   # $0.80 per million
output_cost = output_tokens * 0.000004    # $4.00 per million
total = input_cost + output_cost
```

Actual costs are calculated against the response `usage` block, not the estimate.

---

**Q82. The cheapest way to run high-volume inference?**

1. Use Haiku — it is the most cost-efficient model for most support/classification tasks
2. Enable prompt caching for repeated system prompts (10% of input token cost for cached tokens)
3. Use the Batch API for offline workloads (50% discount)
4. Set `max_tokens` to the minimum needed — output tokens cost 5× more than input on Haiku

---

**Q83. I accidentally set `max_tokens` to 64,000 for every request but only need 200 tokens of output. How much money did I waste?**

None — you are only billed for tokens actually generated (`output_tokens` in the response), not for `max_tokens` as reserved capacity. `max_tokens` is a ceiling, not a pre-allocation.

---

**Q84. Does retrying failed requests cost money?**

Only if the retry succeeds. Failed requests (4xx, 5xx) do not incur token charges. If you retry a 429 and it eventually succeeds, you are charged for that successful request only.

---

## Error Scenarios from This Project

**Q85. I set `max_tokens: 5` and the response was cut off. Was this an error?**

No. HTTP 200 was returned. `stop_reason` was `"max_tokens"`. The response text was `"# Hello! "` — the model started to respond but was interrupted at 5 tokens. This was an intentional test to observe truncation behaviour. The fix is increasing `max_tokens`.

---

**Q86. I triggered a 401 using the key `sk-ant-api03-1337`. What was the exact response?**

Observed in Burp Suite Repeater:
```json
{
  "type": "error",
  "error": {
    "type": "authentication_error",
    "message": "invalid x-api-key"
  }
}
```
Response headers included `X-Should-Retry: false` confirming this is not retryable.

---

**Q87. I put `role: system` inside the messages array. What was the exact error?**

Observed in both Postman and Burp Suite:
```json
{
  "type": "error",
  "error": {
    "type": "invalid_request_error",
    "message": "messages: Unexpected role \"system\". The Messages API accepts a top-level `system` parameter, not \"system\" as an input message role."
  }
}
```

---

**Q88. I routed traffic through Burp and saw rate limit headers on a successful 200 response. What was the state?**

Observed after the first request:
```
Anthropic-Ratelimit-Requests-Limit:     50
Anthropic-Ratelimit-Requests-Remaining: 49
Anthropic-Ratelimit-Tokens-Limit:       60000
Anthropic-Ratelimit-Tokens-Remaining:   60000
```

The quota was 49/50 requests remaining. This demonstrates that rate limit status is available on every response, not just on 429 errors. Proactively monitoring these headers allows applications to slow down before hitting the limit.

---

**Q89. Python threw `UnicodeEncodeError: 'charmap' codec can't encode character '\u2248'`. What caused this?**

Windows CMD uses the CP1252 legacy encoding by default, which cannot represent many Unicode characters. The `≈` symbol (U+2248) used in a log message caused `logging.StreamHandler` to fail. Fix: wrap stdout and stderr at module load:

```python
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
```

Also open `FileHandler` with `encoding="utf-8"` to prevent the same issue in log files.

---

**Q90. What is `User-Agent: Python-urllib/3.13` in the request headers?**

Python's `urllib` automatically sets this header identifying the HTTP client and version. Anthropic's servers log this. It is not modifiable without custom header injection and does not affect API behaviour.

---

## Postman-Specific

**Q91. How do I save Postman requests so I can reuse them?**

Create a Collection: click the Collections icon → New Collection → name it "Claude API — Error Tests". Save each request into the collection. Collections can be exported as JSON and shared with teammates or committed to version control (without the API key — use Postman Environment variables for secrets).

---

**Q92. What is the "Beautify" button in Postman's body editor?**

It formats your JSON with proper indentation. Essential for editing nested JSON payloads. Keyboard shortcut: Shift+Ctrl+B. Always run this before editing to avoid introducing structural errors.

---

**Q93. How do I test a GET request to `/v1/messages` in Postman to get the 405 error?**

Change the method dropdown from POST to GET, keep the URL as `https://api.anthropic.com/v1/messages`, and send. Response: HTTP 405 Method Not Allowed. This confirms the endpoint only accepts POST. Tested and confirmed during this project.

---

**Q94. How do I use Postman Environment Variables to avoid hardcoding my API key?**

Go to Environments → Create Environment → Add variable `ANTHROPIC_API_KEY` with your key as the value. In the header, set the `x-api-key` value to `{{ANTHROPIC_API_KEY}}`. Postman resolves this at request time. The key is stored in the environment file, not in the collection, so you can share the collection without exposing the key.

---

## Burp Suite-Specific

**Q95. How do I find the rate limit headers in Burp?**

After a successful request, click the entry in HTTP History. In the Response panel, switch to Pretty view. Scroll through the headers at the top of the response. All `Anthropic-Ratelimit-*` headers are listed before the JSON body.

---

**Q96. What is the Inspector panel on the right side of HTTP History?**

A structured view of request and response attributes. It parses headers into a readable table showing each header name and value. In the screenshot from this project, it shows: `X-Api-Key`, `Anthropic-Version`, `Content-Length`, `Anthropic-Ratelimit-Input-Tokens-Limit: 50000`, etc. More readable than scanning raw text.

---

**Q97. Why does Burp show HTTP/1.1 in HTTP History but the raw request shows HTTP/2?**

Burp Community Edition downgrades connections to HTTP/1.1 for its proxy listener (127.0.0.1:8080) and re-upgrades to HTTP/2 when forwarding to the upstream server. The HTTP History column shows the protocol used between your client and Burp. The raw request view in Repeater shows what is sent to Anthropic. This is normal Burp behaviour.

---

**Q98. How do I use Repeater to reproduce a specific error scenario?**

Right-click any request in HTTP History → Send to Repeater. In the Repeater tab: modify the request (change the API key, alter the payload, switch the HTTP method), then click Send. The request and response appear side-by-side. Each modification creates a new entry in the Repeater history. This is the most efficient way to reproduce customer-reported issues without writing code.

---

**Q99. Can Burp Suite capture WebSocket traffic from streaming API calls?**

Yes. Streaming responses use HTTP chunked transfer encoding, not WebSockets. Burp captures them in HTTP History as standard HTTP responses. The response body contains the SSE event stream. Burp's WebSockets History tab is for WS/WSS connections which the Claude API does not use.

---

**Q100. I see `Strict-Transport-Security: max-age=31536000; includeSubDomains; preload` in every Anthropic response. What does this mean?**

This is HSTS — HTTP Strict Transport Security. It tells browsers and HTTP clients that `api.anthropic.com` must only be accessed over HTTPS for the next 31,536,000 seconds (1 year). After the first request, any attempt to connect over plain HTTP is automatically upgraded to HTTPS. The `preload` flag means the domain is submitted to browser HSTS preload lists, hardcoding HTTPS enforcement before any connection is made. This is a standard security header for any TLS-enforced service.

---

## Additional: Edge Cases & Rare Issues

**Q101. What happens if I send a request with no messages at all?**

HTTP 400: `messages: field required`. The messages array cannot be null, empty, or omitted. Minimum: one user message.

---

**Q102. Can I send an empty string as message content?**

No — the API rejects messages with empty content strings. Every `content` value must be a non-empty string (or a non-empty array of content blocks for multimodal inputs).

---

**Q103. What happens if `temperature` is set above 1.0?**

HTTP 400: temperature value out of range. Valid range is 0.0 to 1.0 inclusive.

---

**Q104. A customer is getting wildly different answers to the same question every time. What do I ask?**

1. What is their `temperature` setting? Values above 0.7 produce high variance.
2. Are they using a system prompt? Inconsistent or absent system prompts produce inconsistent behaviour.
3. Are they seeding the conversation differently each time (different conversation history)?
4. Which model are they using? Larger models tend to be more consistent.

---

**Q105. The API response includes a `request_id` but my customer says they never saved it. How do I get it?**

It is also in the response headers as `Request-Id`. If they are using a standard HTTP library, they can access it from the response headers object. Advise them to always log `response.headers['Request-Id']` alongside their own request metadata.

---

**Q106. What does "context window" mean in practical terms for a support engineer?**

If a user's conversation history is too long for the model to process in one request, the API returns an error. The user must either shorten the history, summarise it, or start a new conversation. The 200,000-token limit accommodates most use cases, but enterprise customers processing very large documents (legal contracts, codebases, research papers) can reach it.

---

**Q107. Can two different users share the same API key?**

Technically yes, but it is bad practice: shared keys mean shared rate limits, shared billing, and inability to revoke one user's access without revoking all. For multi-user applications, each service or team should have its own key.

---

**Q108. What is the difference between HTTP 500 and HTTP 529?**

Both are server-side errors and both are retryable. 500 is a generic internal error — something went wrong in Anthropic's infrastructure. 529 is a specific "overloaded" signal — the system is healthy but under too much load to process your request right now. The retry strategy is the same for both, but the cause differs.

---

*Reference: [Anthropic API Documentation](https://docs.anthropic.com/en/api/getting-started) · [Model Overview](https://docs.anthropic.com/en/docs/about-claude/models/overview) · [Error Codes](https://docs.anthropic.com/en/api/errors) · [Rate Limits](https://docs.anthropic.com/en/api/rate-limits) · [Status Page](https://status.anthropic.com)*
