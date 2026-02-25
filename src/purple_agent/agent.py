"""Purple agent implementation - solves DevOps tasks via SSH."""

import uvicorn
import os
import subprocess
import shlex
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCard, AgentSkill, AgentCapabilities
from a2a.utils import new_agent_text_message

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.util import parse_tags


class ClaudeCodePurpleAgentExecutor(AgentExecutor):
    """Purple agent that uses Claude Code to solve tasks via SSH."""
    
    def __init__(self):
        self.ctx_id_to_state = {}
    
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user_input = context.get_user_input()
        context_id = context.context_id
        
        print(f"Purple agent: Received message (ctx={context_id})")
        
        # Parse tags from green agent's message
        tags = parse_tags(user_input)
        ssh_command = tags.get('ssh_command', '')
        instruction = tags.get('instruction', '')
        timeout_str = tags.get('timeout', '')
        task_name = tags.get('task_name', 'unknown-task')
        
        if not ssh_command:
            await event_queue.enqueue_event(
                new_agent_text_message("Error: No SSH command provided")
            )
            return
        
        print(f"Purple agent: Task = {task_name}")
        print(f"Purple agent: SSH = {ssh_command}")
        print(f"Purple agent: Instruction = {instruction[:100]}...")
        
        # Send acknowledgment
        await event_queue.enqueue_event(
            new_agent_text_message(f"Connecting to container via SSH and starting Claude Code...\n\nTask: {task_name}\n{timeout_str}")
        )
        
        # Get API key
        api_key = os.getenv('ANTHROPIC_API_KEY', '')
        if not api_key:
            response = """
<status>error</status>
Error: ANTHROPIC_API_KEY not set. Cannot run Claude Code.
"""
            await event_queue.enqueue_event(new_agent_text_message(response))
            return
        
        # Prepare Claude Code execution via SSH
        # Note: In real deployment, this would SSH into the container
        # For now, we simulate the response
        
        # Extract container name and port from SSH command
        # ssh -p PORT root@localhost -> extract PORT
        import re
        port_match = re.search(r'-p\s+(\d+)', ssh_command)
        if port_match:
            ssh_port = port_match.group(1)
            container_name = task_name
            
            # In a real purple agent, you would:
            # 1. SSH into the container
            # 2. Install Claude Code (if not already installed)
            # 3. Run: claude -p "<instruction>"
            # 4. Capture output and return
            
            # For this template, we acknowledge the task
            response = f"""
Purple agent working on task via SSH...

Connected to: {ssh_command}
Container: {container_name}

Claude Code would be executed here with the instruction.

<status>completed</status>
"""
        else:
            response = """
<status>error</status>
Could not parse SSH command
"""
        
        await event_queue.enqueue_event(new_agent_text_message(response))
        print("Purple agent: Sent completion response")
    
    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError


def prepare_agent_card(url):
    skill = AgentSkill(
        id="task-solving",
        name="Task Solving via SSH",
        description="Solves DevOps tasks by connecting to containers via SSH and using Claude Code",
        tags=["claude-code", "ssh", "devops", "solver"],
        examples=["Fix bugs in a Java project", "Debug a Go application"],
    )
    
    card = AgentCard(
        name="Claude Code Purple Agent",
        description="Solves DevOps tasks via SSH using Claude Code AI assistant",
        url=url,
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(),
        skills=[skill],
    )
    return card


def start_purple_agent(host="localhost", port=9010):
    print("Starting Claude Code Purple Agent...")
    url = f"http://{host}:{port}"
    card = prepare_agent_card(url)
    
    request_handler = DefaultRequestHandler(
        agent_executor=ClaudeCodePurpleAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )
    
    app = A2AStarletteApplication(
        agent_card=card,
        http_handler=request_handler,
    )
    
    uvicorn.run(app.build(), host=host, port=port)
