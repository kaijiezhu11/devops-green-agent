"""Nop Purple Agent - does nothing, just returns completed status."""

import uvicorn
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCard, AgentSkill, AgentCapabilities
from a2a.utils import new_agent_text_message

from src.util import parse_tags


class NopPurpleAgentExecutor(AgentExecutor):
    """Nop agent that does nothing - returns completed immediately."""
    
    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Cancel execution (no-op)."""
        raise NotImplementedError
    
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user_input = context.get_user_input()
        context_id = context.context_id
        
        print(f"Nop Purple agent: Received message (ctx={context_id})")
        
        # Parse tags from green agent's message
        tags = parse_tags(user_input)
        task_identifier = tags.get('task_name', 'unknown-task')
        
        print(f"Nop Purple agent: Task = {task_identifier}")
        print(f"Nop Purple agent: Doing nothing...")
        
        # Send single message with completed status
        response = f"""Nop agent received task: {task_identifier}

<status>completed</status>
Nop agent completed without making any changes."""
        
        await event_queue.enqueue_event(new_agent_text_message(response))
        print("Nop Purple agent: Completed")


def prepare_agent_card(url):
    skill = AgentSkill(
        id="nop-agent",
        name="No Operation",
        description="Does nothing and returns completed status immediately - for baseline testing",
        tags=["nop", "baseline", "devops", "testing"],
        examples=["Baseline test without any modifications"],
    )
    
    card = AgentCard(
        name="Nop Purple Agent",
        description="A no-operation agent for baseline testing",
        url=url,
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(),
        skills=[skill],
    )
    return card


def start_nop_purple_agent(host="localhost", port=9141, card_url=None):
    """Start the Nop Purple Agent server."""
    print("Starting Nop Purple Agent...")
    url = card_url or f"http://{host}:{port}"
    card = prepare_agent_card(url)
    print(f"Agent card URL: {url}")
    
    request_handler = DefaultRequestHandler(
        agent_executor=NopPurpleAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )
    
    app = A2AStarletteApplication(
        agent_card=card,
        http_handler=request_handler,
    )
    
    print(f"Starting Nop Purple Agent on {host}:{port}")
    
    uvicorn.run(app.build(), host=host, port=port)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Start the Nop Purple Agent")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind the server")
    parser.add_argument("--port", type=int, default=9141, help="Port to bind the server")
    parser.add_argument("--card-url", type=str, help="URL to advertise in the agent card")
    
    args = parser.parse_args()
    
    start_nop_purple_agent(host=args.host, port=args.port, card_url=args.card_url)
