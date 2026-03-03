"""
claude_client.py
================
Python client for the Anthropic Messages API.

Features:
  - Authentication via x-api-key header
  - Structured error classification (all 8 HTTP error types)
  - Automatic retry with exponential backoff + jitter
  - Context window estimation and overflow detection
  - Conversation session management with token tracking
  - Dual-output logging: console + rotating JSONL file
  - Optional Burp Suite proxy integration for traffic inspection

Environment variables:
  ANTHROPIC_API_KEY   — required, your API key from console.anthropic.com
  BURP_PROXY_ENABLED  — optional, set to "true" to route traffic through Burp Suite
  BURP_CA_PEM_PATH    — optional, path to Burp CA PEM file (default: burp-ca.pem)

Author: Robert Jurkevich
https://www.linkedin.com/in/rjurkevich/
"""

import sys
import io
import os
import json
import time
import logging
import random
import ssl
import urllib.request
import urllib.error
from datetime import datetime
from typing import Optional
from dataclasses import dataclass, field
from pathlib import Path
from urllib.request import HTTPSHandler

# Windows CMD UTF-8 fix — resolves CP1252 encoding errors on special characters
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# Console output + daily rotating file in logs/
# In production: forward JSONL to a SIEM, log aggregator, or monitoring system.
# ─────────────────────────────────────────────────────────────────────────────
log_dir = Path("logs")
log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            log_dir / f"claude_api_{datetime.now().strftime('%Y%m%d')}.log",
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger("claude_client")


# ─────────────────────────────────────────────────────────────────────────────
# PROXY CONFIGURATION
# Set BURP_PROXY_ENABLED=true in your environment to route all API traffic
# through Burp Suite for inspection. Requires burp-ca.pem in the project root.
#
# Windows CMD:
#   set BURP_PROXY_ENABLED=true
#   set BURP_CA_PEM_PATH=burp-ca.pem
#
# When enabled, every request appears in Burp's HTTP History tab with
# full headers, payload, and response — including rate limit headers.
# ─────────────────────────────────────────────────────────────────────────────
BURP_PROXY_ENABLED: bool = os.environ.get("BURP_PROXY_ENABLED", "false").lower() == "true"
BURP_PROXY_URL: str = os.environ.get("BURP_PROXY_URL", "http://127.0.0.1:8080")
BURP_CA_PEM_PATH: str = os.environ.get("BURP_CA_PEM_PATH", "burp-ca.pem")


# ─────────────────────────────────────────────────────────────────────────────
# DATA MODELS
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class APIResponse:
    """Structured result returned from every send_message() call."""
    success: bool
    content: Optional[str] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    model: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    attempt: int = 1
    # stop_reason is the single most diagnostic field in the response.
    # "end_turn"   = model finished naturally, response is complete.
    # "max_tokens" = model was cut off, response is INCOMPLETE — increase max_tokens.
    # "tool_use"   = model wants to call a function, application must handle it.
    stop_reason: Optional[str] = None


@dataclass
class ConversationSession:
    """
    Maintains message history for multi-turn conversations.

    The Claude API is stateless — every request must include the full
    conversation history. This class manages that history and tracks
    cumulative token usage across the session.
    """
    session_id: str
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    messages: list = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0

    def add_message(self, role: str, content: str) -> None:
        """
        Append a message to the history.
        IMPORTANT: The API enforces strict alternation — user, assistant, user, assistant.
        Consecutive messages with the same role return HTTP 422 Unprocessable Entity.
        """
        self.messages.append({"role": role, "content": content})

    def to_api_format(self) -> list:
        """Return the messages array in the format the API expects."""
        return self.messages

    def token_summary(self) -> str:
        return (
            f"Session {self.session_id} | "
            f"messages={len(self.messages)} | "
            f"total_tokens={self.total_input_tokens + self.total_output_tokens} "
            f"({self.total_input_tokens} in / {self.total_output_tokens} out)"
        )


# ─────────────────────────────────────────────────────────────────────────────
# ERROR REGISTRY
# Every HTTP error the Anthropic API can return, with root cause and remediation.
# Populated from: https://docs.anthropic.com/en/api/errors
# ─────────────────────────────────────────────────────────────────────────────
ERROR_REGISTRY: dict = {
    400: {
        "name": "bad_request",
        "description": "Malformed request — invalid JSON, missing required fields, or invalid parameter values.",
        "common_causes": [
            "max_tokens not set or set to 0 (required field)",
            "Model string typo — must be exact, e.g. 'claude-haiku-4-5-20251001'",
            "messages array empty or missing 'role'/'content' keys",
            "system prompt placed inside messages array instead of top-level 'system' field",
            "Context window exceeded — conversation history too long",
        ],
        "user_action": "Check your request payload structure against https://docs.anthropic.com/en/api/messages",
        "retry": False,
    },
    401: {
        "name": "authentication_error",
        "description": "Invalid or missing API key.",
        "common_causes": [
            "API key not passed in x-api-key header",
            "Key copied with trailing whitespace or newline character",
            "Using a key from a different Anthropic account",
            "Key has been rotated or revoked",
        ],
        "user_action": "Verify your API key at console.anthropic.com → API Keys. Create a fresh key.",
        "retry": False,
    },
    403: {
        "name": "permission_error",
        "description": "API key exists but lacks permission for this operation.",
        "common_causes": [
            "Attempting to use a model not enabled for your plan tier",
            "Accessing a beta feature without the required anthropic-beta header",
        ],
        "user_action": "Check your plan and enabled models at console.anthropic.com.",
        "retry": False,
    },
    404: {
        "name": "not_found",
        "description": "The requested resource does not exist.",
        "common_causes": [
            "Wrong API endpoint URL — must be https://api.anthropic.com/v1/messages",
            "Referencing a model ID that has been deprecated or renamed",
        ],
        "user_action": "Verify the endpoint URL. Check current model IDs at docs.anthropic.com/en/docs/about-claude/models/overview",
        "retry": False,
    },
    405: {
        "name": "method_not_allowed",
        "description": "HTTP method not supported for this endpoint.",
        "common_causes": [
            "Using GET instead of POST for /v1/messages",
            "Incorrect endpoint path returning a different resource",
        ],
        "user_action": "The Messages endpoint only accepts POST. Verify your HTTP method.",
        "retry": False,
    },
    422: {
        "name": "unprocessable_entity",
        "description": "Request is syntactically valid JSON but semantically invalid for the API.",
        "common_causes": [
            "Consecutive messages with the same role (must strictly alternate user/assistant)",
            "First message is 'assistant' role — conversations must start with 'user'",
            "system prompt placed inside messages array with role:'system' (invalid role)",
            "Content block type mismatch in multi-modal requests",
        ],
        "user_action": "Check message alternation: user → assistant → user → assistant. Never two same roles in a row.",
        "retry": False,
    },
    429: {
        "name": "rate_limit_error",
        "description": "Rate limit exceeded — too many requests or too many tokens per minute.",
        "common_causes": [
            "Burst of requests exceeding RPM (Requests Per Minute) limit",
            "Single request with very large context exceeding TPM (Tokens Per Minute) limit",
            "Multiple processes sharing one API key without coordination",
        ],
        "user_action": (
            "Implement exponential backoff with jitter. Inspect the Retry-After response header. "
            "Check your current limits at console.anthropic.com."
        ),
        "retry": True,
    },
    500: {
        "name": "api_error",
        "description": "Anthropic internal server error.",
        "common_causes": ["Transient infrastructure issue on Anthropic's side."],
        "user_action": "Retry with backoff. Check https://status.anthropic.com for active incidents.",
        "retry": True,
    },
    529: {
        "name": "overloaded_error",
        "description": "Anthropic API is temporarily overloaded.",
        "common_causes": [
            "High global demand — common during model launches or peak hours.",
        ],
        "user_action": "Retry with exponential backoff. This resolves automatically.",
        "retry": True,
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# MODEL LIMITS
# Reference: https://docs.anthropic.com/en/docs/about-claude/models/overview
# ─────────────────────────────────────────────────────────────────────────────
CONTEXT_WINDOW_LIMITS: dict = {
    "claude-opus-4-20250514":   200_000,
    "claude-sonnet-4-20250514": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
}

MAX_OUTPUT_TOKENS: dict = {
    "claude-opus-4-20250514":    32_000,
    "claude-sonnet-4-20250514":  64_000,
    "claude-haiku-4-5-20251001": 16_000,
}


# ─────────────────────────────────────────────────────────────────────────────
# CORE CLIENT
# ─────────────────────────────────────────────────────────────────────────────
class ClaudeClient:
    """
    Production-grade client for the Anthropic Messages API.

    Handles authentication, request construction, error classification,
    automatic retry with exponential backoff, session management,
    and structured logging. Supports optional Burp Suite proxy integration
    for traffic inspection without modifying application logic.
    """

    BASE_URL: str = "https://api.anthropic.com/v1/messages"
    ANTHROPIC_VERSION: str = "2023-06-01"
    DEFAULT_MODEL: str = "claude-haiku-4-5-20251001"
    DEFAULT_MAX_TOKENS: int = 1024

    # Retry configuration
    MAX_RETRIES: int = 3
    BASE_BACKOFF_SECONDS: float = 1.0
    MAX_BACKOFF_SECONDS: float = 60.0
    JITTER_FACTOR: float = 0.25  # ±25% randomness — prevents thundering herd

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model or self.DEFAULT_MODEL

        if not self.api_key:
            logger.warning(
                "No API key provided. "
                "Set ANTHROPIC_API_KEY environment variable or pass api_key= to ClaudeClient()."
            )

        # The three headers required on every request.
        # x-api-key        — authentication credential
        # anthropic-version — API version pin (prevents breaking changes)
        # Content-Type     — declares JSON body format
        self.headers: dict = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": self.ANTHROPIC_VERSION,
        }

        self._sessions: dict[str, ConversationSession] = {}
        self._opener = self._build_opener()

        proxy_status = f"via Burp proxy {BURP_PROXY_URL}" if BURP_PROXY_ENABLED else "direct"
        logger.info(f"ClaudeClient initialized | model={self.model} | connection={proxy_status}")

    def _build_opener(self) -> urllib.request.OpenerDirector:
        """
        Construct the URL opener.

        In standard mode: uses default system SSL context.
        In Burp mode: routes HTTPS through 127.0.0.1:8080 with Burp's CA certificate
        loaded explicitly to enable HTTPS decryption and inspection.

        Why explicit CA loading instead of system store?
        Python's urllib on Windows uses its own certificate bundle (not the Windows
        certificate store), so even after installing the Burp CA into Windows Trusted Root,
        Python still rejects it. Loading the PEM file directly bypasses this.

        See docs/burp/README.md for the full certificate troubleshooting walkthrough.
        """
        if not BURP_PROXY_ENABLED:
            return urllib.request.build_opener()

        pem_path = Path(BURP_CA_PEM_PATH)
        if not pem_path.exists():
            logger.warning(
                f"Burp proxy enabled but CA PEM not found at '{pem_path}'. "
                f"Run convert_der_to_pem.py to generate it. "
                f"Falling back to direct connection."
            )
            return urllib.request.build_opener()

        ctx = ssl.create_default_context()
        ctx.load_verify_locations(cafile=str(pem_path))

        proxy_handler = urllib.request.ProxyHandler({
            "http":  BURP_PROXY_URL,
            "https": BURP_PROXY_URL,
        })
        https_handler = HTTPSHandler(context=ctx)
        opener = urllib.request.build_opener(proxy_handler, https_handler)

        logger.info(f"Burp proxy configured | proxy={BURP_PROXY_URL} | ca={pem_path}")
        return opener

    # ── Session management ──────────────────────────────────────────────────
    def create_session(self, session_id: Optional[str] = None) -> ConversationSession:
        """Create a new conversation session."""
        sid = session_id or (
            f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{random.randint(1000, 9999)}"
        )
        session = ConversationSession(session_id=sid)
        self._sessions[sid] = session
        logger.info(f"Session created | id={sid}")
        return session

    def get_session(self, session_id: str) -> Optional[ConversationSession]:
        """Retrieve an existing session by ID."""
        return self._sessions.get(session_id)

    # ── Token utilities ─────────────────────────────────────────────────────
    @staticmethod
    def estimate_tokens(text: str) -> int:
        """
        Rough pre-flight token estimate (~4 characters per token for English).
        The authoritative count is always in the response usage block.
        Use this only for pre-flight context window warnings.
        """
        return len(text) // 4

    def _check_context_window(self, messages: list, system: str = "") -> bool:
        """Warn when approaching 85% of the model's context window limit."""
        total_text = system + " ".join(m.get("content", "") for m in messages)
        estimated = self.estimate_tokens(total_text)
        limit = CONTEXT_WINDOW_LIMITS.get(self.model, 200_000)
        if estimated > limit * 0.85:
            logger.warning(
                f"Context window warning: estimated {estimated:,} tokens "
                f"against {limit:,} limit ({estimated/limit*100:.0f}%). "
                f"Consider summarising earlier messages or starting a new session."
            )
            return False
        return True

    # ── Retry / backoff ─────────────────────────────────────────────────────
    def _calculate_backoff(
        self,
        attempt: int,
        retry_after: Optional[float] = None,
    ) -> float:
        """
        Exponential backoff with full jitter.

        Formula: min(base * 2^attempt, max_backoff) ± (jitter_factor * random)

        The jitter component is critical. Without it, all clients that received
        the same 429 simultaneously would retry at the same moment, causing
        another wave of rate limit errors — the "thundering herd" problem.
        Randomised jitter distributes retry load across time.

        If the API returns a Retry-After header (which it does on 429),
        that value takes precedence over the computed backoff.
        """
        if retry_after:
            return float(retry_after)
        backoff = min(self.BASE_BACKOFF_SECONDS * (2 ** attempt), self.MAX_BACKOFF_SECONDS)
        jitter = backoff * self.JITTER_FACTOR * (random.random() * 2 - 1)
        return max(0.1, backoff + jitter)

    # ── Core send ───────────────────────────────────────────────────────────
    def send_message(
        self,
        user_message: str,
        system_prompt: str = "",
        session: Optional[ConversationSession] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 1.0,
    ) -> APIResponse:
        """
        Send a message to Claude and return a structured APIResponse.

        Args:
            user_message:  The user's input text.
            system_prompt: Top-level system instructions. MUST be passed here,
                           not as a message with role:'system' (that causes 422).
            session:       If provided, maintains conversation history for multi-turn.
            max_tokens:    Maximum tokens in the response. Required by the API.
                           If the response hits this limit, stop_reason='max_tokens'
                           and the response is incomplete.
            temperature:   Sampling temperature. 0.0 = deterministic. 1.0 = default.

        Returns:
            APIResponse — check .success before using .content.
        """
        max_tokens = max_tokens or self.DEFAULT_MAX_TOKENS

        if session:
            session.add_message("user", user_message)
            messages = session.to_api_format()
        else:
            messages = [{"role": "user", "content": user_message}]

        self._check_context_window(messages, system_prompt)

        payload: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system_prompt:
            payload["system"] = system_prompt
        if temperature != 1.0:
            payload["temperature"] = temperature

        logger.info(
            f"API request | model={self.model} | "
            f"messages={len(messages)} | max_tokens={max_tokens} | "
            f"estimated_input_tokens~{self.estimate_tokens(str(payload))}"
        )

        for attempt in range(self.MAX_RETRIES + 1):
            start_time = time.monotonic()
            try:
                request_data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    self.BASE_URL,
                    data=request_data,
                    headers=self.headers,
                    method="POST",
                )

                with self._opener.open(req, timeout=60) as response:
                    latency_ms = (time.monotonic() - start_time) * 1000
                    body = json.loads(response.read().decode("utf-8"))

                    content = body["content"][0]["text"]
                    usage = body.get("usage", {})
                    input_tokens = usage.get("input_tokens", 0)
                    output_tokens = usage.get("output_tokens", 0)
                    stop_reason = body.get("stop_reason", "unknown")

                    if stop_reason == "max_tokens":
                        logger.warning(
                            f"Response truncated — stop_reason=max_tokens. "
                            f"Output was cut at {output_tokens} tokens. "
                            f"Increase max_tokens (current model supports up to "
                            f"{MAX_OUTPUT_TOKENS.get(self.model, 'unknown')})."
                        )

                    logger.info(
                        f"API success | latency={latency_ms:.0f}ms | "
                        f"input_tokens={input_tokens} | output_tokens={output_tokens} | "
                        f"stop_reason={stop_reason}"
                    )

                    self._log_interaction(payload, body, latency_ms)

                    if session:
                        session.add_message("assistant", content)
                        session.total_input_tokens += input_tokens
                        session.total_output_tokens += output_tokens

                    return APIResponse(
                        success=True,
                        content=content,
                        model=body.get("model"),
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        latency_ms=latency_ms,
                        attempt=attempt + 1,
                        stop_reason=stop_reason,
                    )

            except urllib.error.HTTPError as e:
                latency_ms = (time.monotonic() - start_time) * 1000
                status_code = e.code
                error_body: dict = {}
                try:
                    error_body = json.loads(e.read().decode("utf-8"))
                except Exception:
                    pass

                error_info = ERROR_REGISTRY.get(status_code, {})
                error_type = error_info.get("name", f"http_{status_code}")
                should_retry = error_info.get("retry", False)
                api_error_msg = error_body.get("error", {}).get("message", str(e))

                logger.error(
                    f"API error | status={status_code} | type={error_type} | "
                    f"message={api_error_msg} | attempt={attempt+1}/{self.MAX_RETRIES+1}"
                )

                if status_code == 400 and "context" in api_error_msg.lower():
                    logger.error(
                        "CONTEXT WINDOW OVERFLOW — conversation history exceeds model limit. "
                        "Options: (1) start a new session, (2) summarise earlier turns, "
                        "(3) switch to a model with a larger context window."
                    )
                    return APIResponse(
                        success=False,
                        error_type="context_window_overflow",
                        error_message="Context window exceeded. Start a new session or summarise history.",
                        latency_ms=latency_ms,
                        attempt=attempt + 1,
                    )

                if should_retry and attempt < self.MAX_RETRIES:
                    retry_after = None
                    retry_after_header = e.headers.get("retry-after") or e.headers.get("Retry-After")
                    if retry_after_header:
                        try:
                            retry_after = float(retry_after_header)
                        except ValueError:
                            pass
                    wait = self._calculate_backoff(attempt, retry_after)
                    logger.info(
                        f"Retrying in {wait:.1f}s "
                        f"(attempt {attempt+2}/{self.MAX_RETRIES+1})"
                        + (f" — Retry-After header: {retry_after}s" if retry_after else "")
                    )
                    time.sleep(wait)
                    continue

                return APIResponse(
                    success=False,
                    error_type=error_type,
                    error_message=(
                        f"{error_info.get('description', 'Unknown error')} | "
                        f"API message: {api_error_msg} | "
                        f"Action: {error_info.get('user_action', '')}"
                    ),
                    latency_ms=latency_ms,
                    attempt=attempt + 1,
                )

            except Exception as e:
                latency_ms = (time.monotonic() - start_time) * 1000
                logger.error(f"Unexpected error | type={type(e).__name__} | message={e}")
                if attempt < self.MAX_RETRIES:
                    wait = self._calculate_backoff(attempt)
                    logger.info(f"Retrying in {wait:.1f}s")
                    time.sleep(wait)
                    continue
                return APIResponse(
                    success=False,
                    error_type="client_error",
                    error_message=str(e),
                    latency_ms=latency_ms,
                    attempt=attempt + 1,
                )

        return APIResponse(
            success=False,
            error_type="max_retries_exceeded",
            error_message=f"Failed after {self.MAX_RETRIES + 1} attempts.",
        )

    # ── Logging ─────────────────────────────────────────────────────────────
    def _log_interaction(self, request: dict, response: dict, latency_ms: float) -> None:
        """
        Append a structured JSONL record for each successful interaction.
        One record per line — compatible with log aggregation systems.
        """
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "latency_ms": round(latency_ms, 2),
            "model": request.get("model"),
            "message_count": len(request.get("messages", [])),
            "max_tokens_requested": request.get("max_tokens"),
            "stop_reason": response.get("stop_reason"),
            "usage": response.get("usage", {}),
        }
        log_file = log_dir / "interactions.jsonl"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry) + "\n")

    # ── Error documentation ──────────────────────────────────────────────────
    def explain_error(self, status_code: int) -> str:
        """Return a formatted explanation of any Claude API HTTP error code."""
        info = ERROR_REGISTRY.get(status_code)
        if not info:
            return f"Unknown status code: {status_code}"

        sep = "=" * 60
        lines = [
            f"\n{sep}",
            f"HTTP {status_code} — {info['name'].upper()}",
            sep,
            f"Description:  {info['description']}",
            f"\nCommon causes:",
        ]
        for cause in info["common_causes"]:
            lines.append(f"  • {cause}")
        lines.append(f"\nRemediation:  {info['user_action']}")
        lines.append(
            f"  {'✓ Retryable — use exponential backoff.' if info.get('retry') else '✗ Not retryable — fix the request.'}"
        )
        lines.append(sep)
        return "\n".join(lines)
