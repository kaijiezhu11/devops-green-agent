"""Script to start the Nop Purple Agent."""

if __name__ == "__main__":
    import argparse
    from src.purple_agent.nop_agent import start_nop_purple_agent
    
    parser = argparse.ArgumentParser(description="Start the Nop Purple Agent")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind the server")
    parser.add_argument("--port", type=int, default=9121, help="Port to bind the server")
    parser.add_argument("--card-url", type=str, help="URL to advertise in the agent card")
    
    args = parser.parse_args()
    
    start_nop_purple_agent(host=args.host, port=args.port, card_url=args.card_url)
