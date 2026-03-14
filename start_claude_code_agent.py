#!/usr/bin/env python3
"""Start the Claude Code Purple Agent."""

import os
import argparse
from src.purple_agent.claude_code_agent import start_claude_code_purple_agent


def main():
    parser = argparse.ArgumentParser(description="Run the Claude Code Purple Agent.")
    parser.add_argument("--host", type=str, default="localhost", help="Host to bind the server")
    parser.add_argument("--port", type=int, default=9121, help="Port to bind the server")
    parser.add_argument("--card-url", type=str, help="URL to advertise in the agent card")
    parser.add_argument("--model", type=str, default=None, help="Claude model to use (e.g. claude-opus-4-5, claude-sonnet-4-5). Defaults to Claude Code's default.")
    args = parser.parse_args()

    # Fall back to CLAUDE_MODEL env var if --model not provided (used by Amber/AgentBeats)
    model = args.model or os.environ.get("CLAUDE_MODEL") or None

    start_claude_code_purple_agent(host=args.host, port=args.port, card_url=args.card_url, model=model)


if __name__ == '__main__':
    main()
