"""
main.py
=======
Interactive demo and test runner for the Claude API client.

Modes:
  python main.py                  — interactive chat (default)
  python main.py --demo-all       — run all demonstration modes
  python main.py --demo-errors    — trigger and classify error scenarios
  python main.py --explain 429    — print full documentation for an error code

Environment:
  ANTHROPIC_API_KEY=sk-ant-...    required
  BURP_PROXY_ENABLED=true         optional — route traffic through Burp Suite

Proxy usage (Windows CMD):
  set BURP_PROXY_ENABLED=true
  set BURP_CA_PEM_PATH=burp-ca.pem
  set HTTP_PROXY=http://127.0.0.1:8080
  set HTTPS_PROXY=http://127.0.0.1:8080
  python main.py

Author: Robert Jurkevich
https://www.linkedin.com/in/rjurkevich/
"""

import os
import sys
import argparse
from claude_client import ClaudeClient, ERROR_REGISTRY


# ─────────────────────────────────────────────────────────────────────────────
# DEMO MODES
# ─────────────────────────────────────────────────────────────────────────────

def demo_basic_question(client: ClaudeClient) -> None:
    """Single-turn question — demonstrates the simplest possible API call."""
    print("\n" + "─" * 60)
    print("DEMO 1: Single-turn question")
    print("─" * 60)

    response = client.send_message(
        user_message="In one sentence, what is the most important thing about API rate limiting?",
        system_prompt="You are a concise technical expert. Answer in exactly one sentence.",
    )

    if response.success:
        print(f"  Response:     {response.content}")
        print(f"  Tokens:       {response.input_tokens} in / {response.output_tokens} out")
        print(f"  Latency:      {response.latency_ms:.0f}ms")
        print(f"  stop_reason:  {response.stop_reason}")
    else:
        print(f"  Error [{response.error_type}]: {response.error_message}")


def demo_conversation(client: ClaudeClient) -> None:
    """
    Multi-turn conversation — demonstrates session management.

    The API is stateless. To maintain context, every request must include
    the full message history. ClaudeClient.ConversationSession handles this
    automatically and tracks cumulative token usage.
    """
    print("\n" + "─" * 60)
    print("DEMO 2: Multi-turn conversation (session management)")
    print("─" * 60)

    session = client.create_session("demo_conversation")
    system = "You are a senior API support engineer. Be concise — 2-3 sentences per answer."

    exchanges = [
        "What is an HTTP 429 error?",
        "How should I handle it in my code?",
        "Why does jitter matter in exponential backoff?",
    ]

    for question in exchanges:
        print(f"\n  User:   {question}")
        response = client.send_message(
            user_message=question,
            system_prompt=system,
            session=session,
        )
        if response.success:
            print(f"  Claude: {response.content}")
        else:
            print(f"  Error:  {response.error_message}")

    print(f"\n  {session.token_summary()}")
    print(f"  Messages in session: {len(session.messages)}")
    # Note: each message adds to context. At 200,000 tokens, the API returns
    # HTTP 400 with a context overflow message. See ClaudeClient._check_context_window.


def demo_truncation(client: ClaudeClient) -> None:
    """
    Demonstrates stop_reason: max_tokens — the most commonly misunderstood response.

    HTTP 200 OK is returned, but the response is INCOMPLETE.
    The model was cut off at the max_tokens limit.
    Users report this as "Claude stops mid-sentence."
    """
    print("\n" + "─" * 60)
    print("DEMO 3: Truncation — stop_reason: max_tokens (NOT an error)")
    print("─" * 60)

    response = client.send_message(
        user_message="Write a detailed explanation of how TCP/IP works.",
        max_tokens=5,  # intentionally tiny — will cut off
    )

    if response.success:
        print(f"  HTTP status:  200 OK")
        print(f"  stop_reason:  {response.stop_reason}  ← THIS means truncation")
        print(f"  Response:     '{response.content}'  ← incomplete")
        print(f"  Tokens out:   {response.output_tokens}")
        print()
        print("  Diagnosis: increase max_tokens. Haiku supports up to 16,000.")
        print("  Always check stop_reason — 'end_turn' = complete, 'max_tokens' = cut off.")
    else:
        print(f"  Error [{response.error_type}]: {response.error_message}")


def demo_error_handling(client: ClaudeClient) -> None:
    """
    Deliberately trigger error conditions and verify they are caught cleanly.
    Each test simulates a real customer-reported issue.
    """
    print("\n" + "─" * 60)
    print("DEMO 4: Error classification — intentional bad requests")
    print("─" * 60)

    # ── Test A: 400 Bad Request — wrong model name ───────────────────────────
    print("\n  Test A: Invalid model name (HTTP 400)")
    bad_model_client = ClaudeClient(api_key=client.api_key, model="claude-not-real")
    r = bad_model_client.send_message("Hello")
    status = "PASS" if not r.success and "bad_request" in (r.error_type or "") else "FAIL"
    print(f"  [{status}] error_type={r.error_type}")

    # ── Test B: 401 Authentication Error ────────────────────────────────────
    print("\n  Test B: Invalid API key (HTTP 401)")
    # Security note: never hardcode real keys. This is a deliberately fake value.
    # Observed in Burp: response body contains {"type":"authentication_error","message":"invalid x-api-key"}
    bad_auth_client = ClaudeClient(api_key="sk-ant-invalid-key-000000")
    r = bad_auth_client.send_message("Hello")
    status = "PASS" if not r.success and "authentication_error" in (r.error_type or "") else "FAIL"
    print(f"  [{status}] error_type={r.error_type}")


def demo_error_docs(client: ClaudeClient) -> None:
    """Print full documentation for all registered error codes."""
    print("\n" + "─" * 60)
    print("DEMO 5: Error code reference")
    print("─" * 60)
    for status_code in sorted(ERROR_REGISTRY.keys()):
        print(client.explain_error(status_code))


# ─────────────────────────────────────────────────────────────────────────────
# INTERACTIVE CHAT
# ─────────────────────────────────────────────────────────────────────────────
def interactive_chat(client: ClaudeClient) -> None:
    """
    REPL-mode interactive chat.

    Commands:
      quit   — exit
      new    — start a fresh session (clears history)
      stats  — show session token usage and estimated cost
    """
    print("\n" + "═" * 60)
    print("  CLAUDE API — INTERACTIVE CHAT")
    print("  quit · new · stats")
    print("═" * 60)

    session = client.create_session()
    system = "You are a helpful AI assistant."

    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nSession ended.")
            break

        if not user_input:
            continue

        if user_input.lower() == "quit":
            print("Goodbye.")
            break

        if user_input.lower() == "new":
            session = client.create_session()
            print("  ✓ New session started.")
            continue

        if user_input.lower() == "stats":
            print(f"  {session.token_summary()}")
            total = session.total_input_tokens * 0.0000008 + session.total_output_tokens * 0.000004
            print(f"  Estimated cost (Haiku): ~${total:.5f}")
            # Haiku pricing: $0.80 / $4.00 per million tokens (input/output)
            # Sonnet pricing: $3.00 / $15.00 per million tokens
            # Opus pricing:   $15.00 / $75.00 per million tokens
            continue

        response = client.send_message(
            user_message=user_input,
            system_prompt=system,
            session=session,
        )

        if response.success:
            print(f"\nClaude: {response.content}")
            print(f"  [{response.input_tokens}→{response.output_tokens} tokens | "
                  f"{response.latency_ms:.0f}ms | stop_reason={response.stop_reason}]")
        else:
            print(f"\n  ✗ Error [{response.error_type}]: {response.error_message}")
            if response.error_type == "context_window_overflow":
                print("  → Session cleared automatically.")
                session = client.create_session()


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Claude API client — interactive chat and error demonstration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py
  python main.py --demo-all
  python main.py --explain 429
  python main.py --explain 422

Set proxy (Windows CMD):
  set BURP_PROXY_ENABLED=true
  set BURP_CA_PEM_PATH=D:\\path\\to\\burp-ca.pem
  python main.py
        """,
    )
    parser.add_argument("--demo-errors", action="store_true", help="Run error handling demonstrations")
    parser.add_argument("--demo-all",    action="store_true", help="Run all demonstration modes")
    parser.add_argument("--explain",     type=int, metavar="STATUS_CODE", help="Explain an HTTP error code")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("\n  No ANTHROPIC_API_KEY found in environment.")
        print("  Windows CMD:  set ANTHROPIC_API_KEY=sk-ant-...")
        print("  PowerShell:   $env:ANTHROPIC_API_KEY = 'sk-ant-...'")
        print("  Get your key: https://console.anthropic.com/settings/keys\n")
        client = ClaudeClient(api_key="__no_key__")
        if args.explain:
            print(client.explain_error(args.explain))
        else:
            for code in sorted(ERROR_REGISTRY.keys()):
                print(client.explain_error(code))
        return

    client = ClaudeClient(api_key=api_key)

    if args.explain:
        print(client.explain_error(args.explain))
        return

    if args.demo_errors:
        demo_error_handling(client)
        demo_error_docs(client)
        return

    if args.demo_all:
        demo_basic_question(client)
        demo_conversation(client)
        demo_truncation(client)
        demo_error_handling(client)
        return

    interactive_chat(client)


if __name__ == "__main__":
    main()
