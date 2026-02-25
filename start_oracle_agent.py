#!/usr/bin/env python3
"""Start the Oracle Purple Agent."""

import argparse
from src.purple_agent.oracle_agent import start_oracle_purple_agent


def main():
    parser = argparse.ArgumentParser(description="Run the Oracle Purple Agent.")
    parser.add_argument("--host", type=str, default="localhost", help="Host to bind the server")
    parser.add_argument("--port", type=int, default=9121, help="Port to bind the server")
    parser.add_argument("--card-url", type=str, help="URL to advertise in the agent card")
    args = parser.parse_args()
    
    start_oracle_purple_agent(host=args.host, port=args.port, card_url=args.card_url)


if __name__ == '__main__':
    main()
