#!/usr/bin/env python3
"""A2A server for DevOps Green Agent."""

import argparse
import uvicorn

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
)

from src.green_agent.agent import DevOpsGreenAgentExecutor


def main():
    parser = argparse.ArgumentParser(description="Run the DevOps Green Agent A2A server.")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind the server")
    parser.add_argument("--port", type=int, default=9119, help="Port to bind the server")
    parser.add_argument("--card-url", type=str, help="URL to advertise in the agent card")
    args = parser.parse_args()

    # Agent card describing capabilities
    skill = AgentSkill(
        id="devops-batch-evaluation",
        name="DevOps Task Batch Evaluation",
        description="Evaluates DevOps tasks from DevOps-Gym dataset by coordinating with purple agents",
        tags=["devops", "evaluation", "batch", "docker", "testing"],
        examples=[
            "Run all issue_resolving tasks",
            "Evaluate specific tasks with custom purple agent",
            "Batch evaluation with task filtering"
        ]
    )

    agent_card = AgentCard(
        name="DevOps Green Agent",
        description="A green agent for batch evaluation of DevOps tasks using the DevOps-Gym dataset. Coordinates with purple agents (solvers) to evaluate tasks and run tests.",
        url=args.card_url or f"http://{args.host}:{args.port}/",
        version='1.0.0',
        default_input_modes=['text'],
        default_output_modes=['text'],
        capabilities=AgentCapabilities(streaming=True),
        skills=[skill]
    )

    # Create request handler with our executor
    request_handler = DefaultRequestHandler(
        agent_executor=DevOpsGreenAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )
    
    # Create A2A server
    server = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    )
    
    print(f"Starting DevOps Green Agent on {args.host}:{args.port}")
    print(f"Agent card URL: {agent_card.url}")
    uvicorn.run(server.build(), host=args.host, port=args.port)


if __name__ == '__main__':
    main()
