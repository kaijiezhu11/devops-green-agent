"""Oracle Purple Agent - applies pre-built solutions directly."""

import uvicorn
import os
import subprocess
import tempfile
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
from src.dataset_manager import DatasetManager


class OraclePurpleAgentExecutor(AgentExecutor):
    """Oracle agent that applies the gold solution directly via SSH."""
    
    def __init__(self):
        self.dataset_manager = DatasetManager()
    
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user_input = context.get_user_input()
        context_id = context.context_id
        
        print(f"Oracle Purple agent: Received message (ctx={context_id})")
        
        # Parse tags from green agent's message
        tags = parse_tags(user_input)
        ssh_command = tags.get('ssh_command', '')
        task_identifier = tags.get('task_name', 'unknown-task')  # Can be full (test_generation/foo) or short (foo)
        dataset_dir_str = tags.get('dataset_dir', '').strip()
        dataset_dir = Path(dataset_dir_str) if dataset_dir_str else None
        
        if not ssh_command:
            await event_queue.enqueue_event(
                new_agent_text_message("Error: No SSH command provided")
            )
            return
        
        print(f"Oracle Purple agent: Task = {task_identifier}")
        print(f"Oracle Purple agent: SSH = {ssh_command}")
        if dataset_dir:
            print(f"Oracle Purple agent: Dataset dir = {dataset_dir}")
        
        # Send acknowledgment
        await event_queue.enqueue_event(
            new_agent_text_message(f"Oracle agent applying gold solution for task: {task_identifier}")
        )
        
        # Extract SSH port
        import re
        port_match = re.search(r'-p\s+(\d+)', ssh_command)
        if not port_match:
            await event_queue.enqueue_event(
                new_agent_text_message("<status>error</status>\nCould not parse SSH port")
            )
            return
        
        ssh_port = port_match.group(1)
        
        # Get task info to find solution.patch
        try:
            # Use the provided dataset_dir if available
            if dataset_dir:
                dataset_manager = DatasetManager(dataset_dir)
            else:
                dataset_manager = self.dataset_manager
            
            # Try to resolve task
            # First check if task_identifier already contains full path (e.g., "test_generation/containerd__containerd-4847")
            task_info = None
            
            if "/" in task_identifier:
                # Already full identifier, use directly
                try:
                    task_info = dataset_manager.get_task_info(task_identifier)
                except Exception as e:
                    print(f"Oracle Purple agent: Failed to get task info for {task_identifier}: {e}")
                    pass
            
            # If not found, try searching with task_type prefixes (for backward compatibility)
            if not task_info:
                task_types = ["build", "end_to_end", "issue_resolving", "monitor", "test_generation"]
                for task_type in task_types:
                    try:
                        candidate = f"{task_type}/{task_identifier}"
                        task_info = dataset_manager.get_task_info(candidate)
                        break
                    except:
                        continue
            
            if not task_info:
                raise FileNotFoundError(f"Task {task_identifier} not found in any task type")
            
            # Extract container name (short name without task_type prefix)
            # e.g., "test_generation/containerd__containerd-4847" -> "containerd__containerd-4847"
            container_name = task_info['task_name']
            
            # Try to find solution file (either .patch or .sh)
            solution_patch = task_info['task_path'] / 'solution.patch'
            solution_sh = task_info['task_path'] / 'solution.sh'
            
            if solution_patch.exists():
                solution_path = solution_patch
                solution_type = 'patch'
            elif solution_sh.exists():
                solution_path = solution_sh
                solution_type = 'script'
            else:
                error_msg = f"Solution not found (tried .patch and .sh) in: {task_info['task_path']}"
                print(f"Oracle Purple agent: {error_msg}")
                await event_queue.enqueue_event(
                    new_agent_text_message(f"<status>error</status>\n{error_msg}")
                )
                return
            
            print(f"Oracle Purple agent: Found {solution_type} solution at {solution_path}")
            
            # Apply solution via SSH
            # 1. Copy solution file to container
            # 2. Apply the patch or run the script
            # 3. Return success
            
            result = self._apply_solution_via_ssh(
                ssh_port=ssh_port,
                solution_path=solution_path,
                solution_type=solution_type,
                task_name=container_name  # Use short container name for docker commands
            )
            
            if result['success']:
                response = f"""
Oracle agent successfully applied solution!

Container: {container_name}
Solution: {solution_path.name}
Output:
{result['output'][:500]}

<status>completed</status>
"""
            else:
                response = f"""
<status>error</status>
Failed to apply solution:
{result['error']}
"""
            
            await event_queue.enqueue_event(new_agent_text_message(response))
            print("Oracle Purple agent: Completed")
            
        except Exception as e:
            import traceback
            error_msg = f"Error: {e}\n{traceback.format_exc()}"
            print(f"Oracle Purple agent: {error_msg}")
            await event_queue.enqueue_event(
                new_agent_text_message(f"<status>error</status>\n{error_msg}")
            )
    
    def _apply_solution_via_ssh(self, ssh_port: str, solution_path: Path, solution_type: str, task_name: str) -> dict:
        """
        Apply solution via SSH.
        
        Args:
            ssh_port: SSH port number
            solution_path: Path to solution file (.patch or .sh)
            solution_type: 'patch' or 'script'
            task_name: Container name
        
        Returns:
            Dictionary with 'success', 'output', and 'error'
        """
        try:
            if solution_type == 'patch':
                # Apply patch
                script_content = f"""#!/bin/bash
set -e

echo "Oracle: Applying solution patch..."

# Find the git repository root
cd /home
if [ -d .git ]; then
    echo "Found git repo in /home"
elif [ -d */..git ]; then
    cd */
    echo "Found git repo in /home/*/"
else
    echo "ERROR: No git repository found"
    exit 1
fi

# Apply the patch
if git apply --check /tmp/solution.patch 2>/dev/null; then
    echo "Patch can be applied cleanly"
    git apply /tmp/solution.patch
    echo "Patch applied successfully"
elif patch --dry-run -p1 < /tmp/solution.patch 2>/dev/null; then
    echo "Using patch command"
    patch -p1 < /tmp/solution.patch
    echo "Patch applied successfully"
else
    echo "Trying git apply without --check..."
    git apply /tmp/solution.patch || patch -p1 < /tmp/solution.patch
fi

echo "Solution applied!"
"""
                copy_dest = f'{task_name}:/tmp/solution.patch'
            
            else:  # solution_type == 'script'
                # Execute solution.sh script directly (it already has full logic)
                script_content = """#!/bin/bash
set -e

echo "Oracle: Executing solution script..."
echo "Working directory: $(pwd)"
echo "Container filesystem:"
ls -la /home/ 2>/dev/null || echo "No /home found"
ls -la /tmp/ 2>/dev/null || echo "No /tmp found"

# Make script executable and run it
chmod +x /tmp/solution.sh
echo "Running solution script..."
/tmp/solution.sh

echo "Solution script executed!"
echo "Verifying changes applied..."
"""
                copy_dest = f'{task_name}:/tmp/solution.sh'
            
            # Read solution content and write to container
            # (Avoids path resolution issues in Docker-in-Docker scenarios)
            print(f"Oracle: Reading solution from {solution_path}...")
            try:
                solution_content = solution_path.read_text()
            except Exception as e:
                return {
                    'success': False,
                    'output': '',
                    'error': f"Failed to read solution file: {e}"
                }
            
            # Write content to container using docker exec
            print(f"Oracle: Writing {solution_path.name} to container...")
            write_cmd = [
                'docker', 'exec', '-i', task_name,
                'bash', '-c', f'cat > /tmp/{solution_path.name} && chmod +x /tmp/{solution_path.name}'
            ]
            result = subprocess.run(
                write_cmd, 
                input=solution_content, 
                capture_output=True, 
                text=True, 
                timeout=10
            )
            if result.returncode != 0:
                return {
                    'success': False,
                    'output': '',
                    'error': f"Failed to write solution to container: {result.stderr}"
                }
            
            # Execute the script via docker exec
            print(f"Oracle: Applying solution in container...")
            apply_cmd = [
                'docker', 'exec', task_name,
                'bash', '-c', script_content
            ]
            result = subprocess.run(apply_cmd, capture_output=True, text=True, timeout=120)
            
            output = result.stdout + result.stderr
            
            # Print detailed output for debugging
            print(f"Oracle: Solution application output:")
            print(f"--- STDOUT ---")
            print(result.stdout)
            print(f"--- STDERR ---")
            print(result.stderr)
            print(f"--- EXIT CODE: {result.returncode} ---")
            
            if result.returncode == 0:
                return {
                    'success': True,
                    'output': output,
                    'error': ''
                }
            else:
                return {
                    'success': False,
                    'output': output,
                    'error': f"Exit code: {result.returncode}"
                }
        
        except subprocess.TimeoutExpired:
            return {
                'success': False,
                'output': '',
                'error': 'Timeout while applying solution'
            }
        except Exception as e:
            return {
                'success': False,
                'output': '',
                'error': str(e)
            }
    
    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError


def prepare_agent_card(url):
    skill = AgentSkill(
        id="oracle-solving",
        name="Oracle Solution Application",
        description="Applies pre-built gold solutions directly to tasks",
        tags=["oracle", "solution", "devops", "testing"],
        examples=["Apply gold solution to test task", "Verify test infrastructure"],
    )
    
    card = AgentCard(
        name="Oracle Purple Agent",
        description="Applies gold solutions directly for testing infrastructure",
        url=url,
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(),
        skills=[skill],
    )
    return card


def start_oracle_purple_agent(host="localhost", port=9020):
    print("Starting Oracle Purple Agent...")
    url = f"http://{host}:{port}"
    card = prepare_agent_card(url)
    
    request_handler = DefaultRequestHandler(
        agent_executor=OraclePurpleAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )
    
    app = A2AStarletteApplication(
        agent_card=card,
        http_handler=request_handler,
    )
    
    uvicorn.run(app.build(), host=host, port=port)
