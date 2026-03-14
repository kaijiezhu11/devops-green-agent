"""Claude Code Purple Agent - solves DevOps tasks via SSH using Claude Code."""

import uvicorn
import os
import subprocess
import tempfile
import time
import re
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


class ClaudeCodePurpleAgentExecutor(AgentExecutor):
    """Purple agent that uses Claude Code to solve tasks via SSH."""
    
    def __init__(self, model: str = None):
        self.ctx_id_to_state = {}
        self.model = model  # None means use Claude Code's default
    
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user_input = context.get_user_input()
        context_id = context.context_id
        
        print(f"[Claude Code Purple Agent] Received message (ctx={context_id})")
        print(f"[Claude Code Purple Agent] Full message:\n{user_input}\n")
        
        # Parse tags from green agent's message
        tags = parse_tags(user_input)
        ssh_command = tags.get('ssh_command', '')
        instruction = tags.get('instruction', '')
        timeout_str = tags.get('timeout', '')
        task_name = tags.get('task_name', 'unknown-task')
        # Get container name (short name without task_type prefix, used for Docker commands)
        container_name_from_green = tags.get('container_name', '')
        
        if not ssh_command:
            await event_queue.enqueue_event(
                new_agent_text_message(f"🚀 Claude Code Purple Agent - Task: {task_name}\n\n<status>error</status>\nError: No SSH command provided", context_id=context_id)
            )
            return
        
        print(f"[Claude Code Purple Agent] Task = {task_name}")
        print(f"[Claude Code Purple Agent] SSH = {ssh_command}")
        print(f"[Claude Code Purple Agent] Instruction = {instruction[:200]}...")
        print(f"[Claude Code Purple Agent] Timeout = {timeout_str}")
        
        # Get API key
        api_key = os.getenv('ANTHROPIC_API_KEY', '')
        if not api_key or api_key.strip() == '':
            response = f"""🚀 Claude Code Purple Agent - Task: {task_name}

<status>error</status>
❌ Error: ANTHROPIC_API_KEY environment variable is not set or empty. Cannot run Claude Code.
Please set your Anthropic API key before running this command:
  export ANTHROPIC_API_KEY="your-api-key-here"
"""
            print(f"[Claude Code Purple Agent] API key not set or empty")
            await event_queue.enqueue_event(new_agent_text_message(response, context_id=context_id))
            return
        
        print(f"[Claude Code Purple Agent] API key found (length: {len(api_key)} chars, starts with: {api_key[:10]}...)")
        
        # Extract container name and port from SSH command
        # ssh -p PORT root@localhost -> extract PORT
        port_match = re.search(r'-p\s+(\d+)', ssh_command)
        if not port_match:
            response = f"""🚀 Claude Code Purple Agent - Task: {task_name}

<status>error</status>
❌ Could not parse SSH port from command
"""
            await event_queue.enqueue_event(new_agent_text_message(response, context_id=context_id))
            return
        
        ssh_port = port_match.group(1)
        # Use container_name from green agent if provided, otherwise derive from task_name
        if container_name_from_green:
            container_name = container_name_from_green
        else:
            # Fallback: Docker container names cannot contain '/', replace with '_'
            container_name = task_name.replace('/', '_')
        
        print(f"[Claude Code Purple Agent] Extracted SSH port: {ssh_port}")
        print(f"[Claude Code Purple Agent] Task name: {task_name}")
        print(f"[Claude Code Purple Agent] Container name: {container_name}")
        
        # Step 1: Install Node.js and Claude Code in the container
        print(f"[Claude Code Purple Agent] Step 1: Installing Node.js and Claude Code...")
        
        setup_script = """#!/bin/bash
set -e

echo "[Setup] Starting Claude Code installation..."

# Check if we're in Docker (typical for DevOps tasks)
if [ -f /.dockerenv ]; then
    echo "[Setup] Running in Docker container"
fi

# Install Node.js via nvm
echo "[Setup] Installing nvm and Node.js..."
apt-get update 2>&1 | head -20
apt-get install -y curl 2>&1 | tail -10

# Install nvm
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.2/install.sh | bash

# Load nvm
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"

# Install Node.js 22
echo "[Setup] Installing Node.js 22..."
nvm install 22
nvm use 22
node --version
npm --version

# Install Claude Code
echo "[Setup] Installing Claude Code CLI..."
npm install -g @anthropic-ai/claude-code@latest

# Verify installation
echo "[Setup] Verifying installation..."
which claude || echo "Warning: claude not in PATH"

echo "[Setup] Installation complete!"
"""
        
        try:
            # Write setup script to container and run it
            print(f"[Claude Code Purple Agent] Writing setup script to container...")
            write_cmd = [
                'docker', 'exec', '-i', container_name,
                'bash', '-c', 'cat > /tmp/setup_claude.sh && chmod +x /tmp/setup_claude.sh'
            ]
            result = subprocess.run(
                write_cmd,
                input=setup_script,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                error_msg = f"❌ Failed to write setup script: {result.stderr}"
                print(f"[Claude Code Purple Agent] {error_msg}")
                await event_queue.enqueue_event(new_agent_text_message(
                    f"🚀 Claude Code Purple Agent - Task: {task_name}\n\n{error_msg}\n<status>error</status>",
                    context_id=context_id,
                ))
                return
            
            print(f"[Claude Code Purple Agent] Running setup script...")
            
            # Run setup with long timeout (installation takes time)
            setup_cmd = [
                'docker', 'exec', container_name,
                'bash', '/tmp/setup_claude.sh'
            ]
            result = subprocess.run(
                setup_cmd,
                capture_output=True,
                text=True,
                timeout=300  # 5 minutes for installation
            )
            
            print(f"[Claude Code Purple Agent] Setup output (last 500 chars):")
            print(result.stdout[-500:] if result.stdout else "(no stdout)")
            print(f"[Claude Code Purple Agent] Setup stderr (last 500 chars):")
            print(result.stderr[-500:] if result.stderr else "(no stderr)")
            print(f"[Claude Code Purple Agent] Setup exit code: {result.returncode}")
            
            if result.returncode != 0:
                error_msg = f"⚠️  Setup script failed (exit {result.returncode})"
                print(f"[Claude Code Purple Agent] {error_msg}")
                # Don't return error yet - try to continue
            else:
                print(f"[Claude Code Purple Agent] Setup completed successfully")
            
        except subprocess.TimeoutExpired:
            error_msg = "❌ Setup timeout (>5 minutes)"
            print(f"[Claude Code Purple Agent] {error_msg}")
            await event_queue.enqueue_event(new_agent_text_message(
                f"🚀 Claude Code Purple Agent - Task: {task_name}\n\n{error_msg}\n<status>error</status>",
                context_id=context_id,
            ))
            return
        except Exception as e:
            error_msg = f"❌ Setup error: {e}"
            print(f"[Claude Code Purple Agent] {error_msg}")
            await event_queue.enqueue_event(new_agent_text_message(
                f"🚀 Claude Code Purple Agent - Task: {task_name}\n\n{error_msg}\n<status>error</status>",
                context_id=context_id,
            ))
            return
        
        # Step 2: Run Claude Code with the instruction
        print(f"[Claude Code Purple Agent] Step 2: Running Claude Code...")
        
        # Prepare Claude Code command
        # Use --verbose --output-format stream-json for better logging
        # Escape instruction for shell
        import shlex
        escaped_instruction = shlex.quote(instruction)
        
        claude_cmd = f"""#!/bin/bash
set -e

# Load nvm
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"

# Set API key
export ANTHROPIC_API_KEY="{api_key}"
export FORCE_AUTO_BACKGROUND_TASKS="1"
export ENABLE_BACKGROUND_TASKS="1"

# Find the repository directory (usually /home or /home/*)
cd /home
if [ -d .git ]; then
    echo "[Claude Code] Found git repo in /home"
elif [ -d */..git ]; then
    cd */
    echo "[Claude Code] Found git repo in /home/*/"
else
    echo "[Claude Code] Warning: No git repository found, running from /home"
fi

echo "[Claude Code] Working directory: $(pwd)"
echo "[Claude Code] Repository info:"
git status 2>&1 | head -10 || echo "Not a git repository or git not available"

echo "[Claude Code] Starting Claude Code..."
echo "[Claude Code] Instruction: {instruction[:100]}..."

# Run Claude Code
claude --verbose --output-format stream-json -p {escaped_instruction} {f'--model {self.model}' if self.model else ''} --allowedTools Bash Edit Write Read Glob Grep LS WebFetch NotebookEdit NotebookRead TodoRead TodoWrite Agent

echo "[Claude Code] Execution complete!"
"""
        
        try:
            # Write Claude command script
            print(f"[Claude Code Purple Agent] Writing Claude Code execution script...")
            write_cmd = [
                'docker', 'exec', '-i', container_name,
                'bash', '-c', 'cat > /tmp/run_claude.sh && chmod +x /tmp/run_claude.sh'
            ]
            result = subprocess.run(
                write_cmd,
                input=claude_cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                error_msg = f"❌ Failed to write Claude script: {result.stderr}"
                print(f"[Claude Code Purple Agent] {error_msg}")
                await event_queue.enqueue_event(new_agent_text_message(
                    f"🚀 Claude Code Purple Agent - Task: {task_name}\n\n{error_msg}\n<status>error</status>",
                    context_id=context_id,
                ))
                return
            
            print(f"[Claude Code Purple Agent] Executing Claude Code...")
            
            # Run Claude Code with timeout from task config (default 20 minutes)
            # Parse timeout from timeout_str like "You have 1200 seconds to complete this task."
            timeout_seconds = 1200  # default
            timeout_match = re.search(r'(\d+(?:\.\d+)?)\s+seconds?', timeout_str)
            if timeout_match:
                timeout_seconds = int(float(timeout_match.group(1)))
            
            print(f"[Claude Code Purple Agent] Using timeout: {timeout_seconds}s")
            
            claude_start = time.time()
            run_cmd = [
                'docker', 'exec', container_name,
                'bash', '/tmp/run_claude.sh'
            ]
            
            # Check if verbose mode is enabled
            verbose = os.environ.get('CLAUDE_CODE_VERBOSE', '').lower() in ('1', 'true', 'yes')
            
            try:
                if verbose:
                    # Real-time streaming mode
                    print(f"[Claude Code Purple Agent] Running in VERBOSE mode - showing real-time output...")
                    print("=" * 80)
                    process = subprocess.Popen(
                        run_cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1
                    )
                    
                    output_lines = []
                    try:
                        for line in process.stdout:
                            print(line.rstrip())
                            output_lines.append(line)
                        
                        process.wait(timeout=timeout_seconds + 60)
                        result_stdout = ''.join(output_lines)
                        result_stderr = ""
                        result_returncode = process.returncode
                        
                    except subprocess.TimeoutExpired:
                        process.kill()
                        raise
                    
                    print("=" * 80)
                else:
                    # Silent mode - capture all output
                    result = subprocess.run(
                        run_cmd,
                        capture_output=True,
                        text=True,
                        timeout=timeout_seconds + 60
                    )
                    result_stdout = result.stdout
                    result_stderr = result.stderr
                    result_returncode = result.returncode
                
                claude_duration = time.time() - claude_start
                
                print(f"[Claude Code Purple Agent] Claude Code finished in {claude_duration:.1f}s")
                print(f"[Claude Code Purple Agent] Exit code: {result_returncode}")
                if not verbose:
                    print(f"[Claude Code Purple Agent] Output length: {len(result_stdout)} chars")
                    print(f"[Claude Code Purple Agent] Claude Code output (last 1000 chars):")
                    print(result_stdout[-1000:] if result_stdout else "(no output)")
                    print(f"[Claude Code Purple Agent] Claude Code stderr (last 1000 chars):")
                    print(result_stderr[-1000:] if result_stderr else "(no stderr)")
                
                # Prepare response
                output_sample = result_stdout[-1500:] if result_stdout else ""
                stderr_sample = result_stderr[-500:] if result_stderr else ""
                
                if result_returncode == 0:
                    response = f"""🚀 Claude Code Purple Agent - Task: {task_name}

✅ Claude Code completed successfully!

Duration: {claude_duration:.1f} seconds
Exit Code: {result_returncode}

Output (last 1500 chars):
{output_sample}

Stderr (last 500 chars):
{stderr_sample}

<status>completed</status>
"""
                else:
                    response = f"""🚀 Claude Code Purple Agent - Task: {task_name}

⚠️  Claude Code completed with exit code {result_returncode}

Duration: {claude_duration:.1f} seconds

Output (last 1500 chars):
{output_sample}

Stderr (last 500 chars):
{stderr_sample}

<status>completed</status>
"""
                
                await event_queue.enqueue_event(new_agent_text_message(response, context_id=context_id))
                print(f"[Claude Code Purple Agent] Sent completion response")
                
            except subprocess.TimeoutExpired:
                claude_duration = time.time() - claude_start
                timeout_msg = f"⏱️  Claude Code timeout after {claude_duration:.1f}s (max: {timeout_seconds}s)"
                print(f"[Claude Code Purple Agent] {timeout_msg}")
                response = f"""🚀 Claude Code Purple Agent - Task: {task_name}

{timeout_msg}

The task may not have been completed within the time limit.

<status>timeout</status>
"""
                await event_queue.enqueue_event(new_agent_text_message(response, context_id=context_id))
                
        except Exception as e:
            import traceback
            error_msg = f"❌ Error running Claude Code: {e}"
            print(f"[Claude Code Purple Agent] {error_msg}")
            print(f"[Claude Code Purple Agent] Traceback:\n{traceback.format_exc()}")
            await event_queue.enqueue_event(new_agent_text_message(
                f"🚀 Claude Code Purple Agent - Task: {task_name}\n\n{error_msg}\n\n{traceback.format_exc()[-500:]}\n<status>error</status>",
                context_id=context_id,
            ))
    
    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError


def prepare_agent_card(url):
    skill = AgentSkill(
        id="claude-code-solving",
        name="Claude Code Task Solving",
        description="Solves DevOps tasks by connecting to containers via SSH and using Claude Code AI assistant",
        tags=["claude-code", "ssh", "devops", "solver", "ai"],
        examples=["Fix bugs in a Java project", "Debug a Go application", "Add new features"],
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


def start_claude_code_purple_agent(host="localhost", port=9030, card_url=None, model=None):
    print("Starting Claude Code Purple Agent...")
    if model:
        print(f"Using model: {model}")
    url = card_url or f"http://{host}:{port}"
    card = prepare_agent_card(url)
    print(f"Agent card URL: {url}")
    
    request_handler = DefaultRequestHandler(
        agent_executor=ClaudeCodePurpleAgentExecutor(model=model),
        task_store=InMemoryTaskStore(),
    )
    
    app = A2AStarletteApplication(
        agent_card=card,
        http_handler=request_handler,
    )
    
    uvicorn.run(app.build(), host=host, port=port, timeout_keep_alive=300)
