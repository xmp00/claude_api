# Postman — Claude API Testing Collection

Zero-code API testing using Postman. Covers setup, the complete error test collection, system prompt testing, and exporting collections for team sharing.

---

## Prerequisites

- Postman Desktop — [postman.com/downloads](https://www.postman.com/downloads/) (free, no account required to start)
- Your Anthropic API key from [console.anthropic.com](https://console.anthropic.com/settings/keys)

---

## Part 1 — Initial Request Setup

### Step 1: Create a new request

Click **+** to open a new tab.

### Step 2: Configure the request

| Field | Value |
|---|---|
| Method | `POST` |
| URL | `https://api.anthropic.com/v1/messages` |

### Step 3: Add headers

Go to the **Headers** tab. Add these three:

| Key | Value |
|---|---|
| `Content-Type` | `application/json` |
| `x-api-key` | `sk-ant-your-key-here` |
| `anthropic-version` | `2023-06-01` |

> Use a Postman Environment Variable for the API key: `{{ANTHROPIC_API_KEY}}`. Go to Environments → New → add `ANTHROPIC_API_KEY` as a variable. This keeps your key out of the collection file when exporting.

### Step 4: Add the request body

Go to **Body → raw → JSON** and paste:

```json
{
  "model": "claude-haiku-4-5-20251001",
  "max_tokens": 200,
  "messages": [
    {"role": "user", "content": "In one sentence, what is a 429 error?"}
  ]
}
```

### Step 5: Send

Click **Send**. The response panel shows the full JSON response with HTTP status, headers, and body.

---

## Part 2 — The Error Test Collection

Create a Collection named **Claude API — Error Tests**. Save each request below into it.

---

### ✓ 200 OK — Baseline

Working request with no modifications. Confirms connectivity and authentication.

```json
{
  "model": "claude-haiku-4-5-20251001",
  "max_tokens": 200,
  "messages": [{"role": "user", "content": "What is HTTP?"}]
}
```

Expected: HTTP 200. `stop_reason: end_turn`. Full response in `content[0].text`.

---

### ✓ 200 Truncated — stop_reason: max_tokens

```json
{
  "model": "claude-haiku-4-5-20251001",
  "max_tokens": 5,
  "messages": [{"role": "user", "content": "Explain TCP/IP networking in detail."}]
}
```

Expected: HTTP 200. `stop_reason: max_tokens`. Response is cut off mid-sentence. This is not an error — it is the `max_tokens` ceiling being hit. The customer fix: increase `max_tokens`.

---

### ✗ 400 — Bad Model

```json
{
  "model": "claude-not-real",
  "max_tokens": 200,
  "messages": [{"role": "user", "content": "test"}]
}
```

Expected: HTTP 400. `type: invalid_request_error`. Model string does not exist.

---

### ✗ 400 — No max_tokens

```json
{
  "model": "claude-haiku-4-5-20251001",
  "messages": [{"role": "user", "content": "test"}]
}
```

Expected: HTTP 400. `max_tokens: field required`. Omitting this required field always returns 400.

---

### ✗ 400 — System Role in Messages (wrong placement)

```json
{
  "model": "claude-haiku-4-5-20251001",
  "max_tokens": 200,
  "messages": [
    {"role": "system", "content": "You are a pirate."},
    {"role": "user",   "content": "What time is it?"}
  ]
}
```

Expected: HTTP 400.
`messages: Unexpected role "system". The Messages API accepts a top-level system parameter, not "system" as an input message role.`

This is one of the most common developer mistakes. The system prompt must be a top-level `"system"` field, not a message inside the array.

---

### ✗ 401 — Invalid API Key

Change the `x-api-key` header value to `sk-ant-api03-1337`.

```json
{
  "model": "claude-haiku-4-5-20251001",
  "max_tokens": 200,
  "messages": [{"role": "user", "content": "test"}]
}
```

Expected: HTTP 401. `type: authentication_error`. `message: invalid x-api-key`.

---

### ✗ 401 — Missing API Key

Delete the `x-api-key` header entirely (uncheck or remove the row).

Expected: HTTP 401. Authentication error about missing API key.

---

### ✗ 422 — Same Role (alternation violation)

```json
{
  "model": "claude-haiku-4-5-20251001",
  "max_tokens": 200,
  "messages": [
    {"role": "user", "content": "First message"},
    {"role": "user", "content": "Second message — same role"}
  ]
}
```

Expected: HTTP 422. Unprocessable entity. Messages must alternate user/assistant.

---

### ✗ 422 — Assistant as First Message

```json
{
  "model": "claude-haiku-4-5-20251001",
  "max_tokens": 200,
  "messages": [
    {"role": "assistant", "content": "Hello! How can I help?"},
    {"role": "user",      "content": "What is Python?"}
  ]
}
```

Expected: HTTP 422. Conversations must start with a user message.

---

### ✗ 405 — Method Not Allowed

Change the HTTP method from **POST** to **GET**. Keep the URL and headers the same.

Expected: HTTP 405. Method Not Allowed. The Messages endpoint only accepts POST.

---

## Part 3 — System Prompt Testing

### Correct system prompt placement

```json
{
  "model": "claude-haiku-4-5-20251001",
  "max_tokens": 200,
  "system": "You are a pirate. Respond only in pirate speak.",
  "messages": [{"role": "user", "content": "What time is it?"}]
}
```

Expected: HTTP 200. Response in pirate dialect. The `system` field is a top-level key, not inside `messages`.

### Incorrect placement (triggers 400)

```json
{
  "model": "claude-haiku-4-5-20251001",
  "max_tokens": 200,
  "messages": [
    {"role": "system", "content": "You are a pirate."},
    {"role": "user",   "content": "What time is it?"}
  ]
}
```

Save both as **System — Correct** and **System — Wrong (400)** in the collection.

---

## Part 4 — Reading the Response

A successful response looks like:

```json
{
  "model": "claude-haiku-4-5-20251001",
  "id": "msg_01XFDUDYJgAACzvnptvVoYEL",
  "type": "message",
  "role": "assistant",
  "content": [
    {
      "type": "text",
      "text": "The actual response text is here"
    }
  ],
  "stop_reason": "end_turn",
  "stop_sequence": null,
  "usage": {
    "input_tokens": 25,
    "output_tokens": 87,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0
  }
}
```

Key fields to always check:
- `stop_reason` — `end_turn` (complete) or `max_tokens` (truncated)
- `usage.input_tokens` + `usage.output_tokens` — for cost calculation and quota tracking
- `id` — the `request_id` for filing support tickets

---

## Part 5 — Response Headers

Click the **Headers (27)** tab in the response panel to see all response headers. The most important for API support:

| Header | What it tells you |
|---|---|
| `Anthropic-Ratelimit-Requests-Remaining` | How many requests left this minute |
| `Anthropic-Ratelimit-Tokens-Remaining` | How many tokens left this minute |
| `Anthropic-Ratelimit-Requests-Reset` | When the RPM window resets (ISO 8601) |
| `X-Should-Retry` | Whether retrying will help (`true`/`false`) |
| `Request-Id` | Unique identifier for this request — include in support tickets |

---

## Part 6 — Exporting the Collection

To share the collection or commit it to version control:

1. Right-click the collection → **Export**
2. Select **Collection v2.1**
3. Save as `claude_api_error_tests.json`
4. **Before committing:** remove your API key or replace with a placeholder string

---

## References

- [Anthropic Messages API](https://docs.anthropic.com/en/api/messages)
- [Anthropic Error Codes](https://docs.anthropic.com/en/api/errors)
- [Postman Documentation](https://learning.postman.com/docs/getting-started/introduction/)
