"""Green agent implementation - manages DevOps task evaluations."""

import uvicorn
import json
import time
import sys
import os
from pathlib import Path
from pydantic import BaseModel, HttpUrl, ValidationError
from typing import Any

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCard, AgentSkill, AgentCapabilities, SendMessageSuccessResponse, Message, 
    Part, TextPart, DataPart, TaskArtifactUpdateEvent, Artifact,
    Task, TaskState, UnsupportedOperationError, InvalidRequestError
)
from a2a.utils import new_agent_text_message, get_text_parts, new_task, get_message_text
from a2a.utils.errors import ServerError

from src.util import parse_tags
from src.docker_manager import DockerManager
from src.dataset_manager import DatasetManager
from src.messenger import Messenger


TERMINAL_STATES = {
    TaskState.completed,
    TaskState.canceled,
    TaskState.failed,
    TaskState.rejected
}


class BatchEvalRequest(BaseModel):
    """Request format for batch evaluation."""
    participants: dict[str, HttpUrl]
    config: dict[str, Any]


def get_task_environment(task_identifier: str, dataset_dir: Path = None):
    """
    Get task environment information (similar to tau_bench's get_env).
    
    This function:
    1. Ensures DevOps-Gym dataset is available (clones if needed)
    2. Resolves task identifier to full path
    3. Loads task configuration
    4. Returns complete task environment
    
    Args:
        task_identifier: Task to evaluate (e.g., "issue_resolving/task-123")
        dataset_dir: Optional custom dataset directory
    
    Returns:
        Dictionary with task configuration
    """
    print(f"Green agent: Getting task environment for '{task_identifier}'...")
    
    # Initialize dataset manager (will auto-clone if needed)
    dataset_mgr = DatasetManager(dataset_dir)
    
    # Resolve task (triggers dataset check/clone if needed)
    task_info = dataset_mgr.get_task_info(task_identifier)
    
    print(f"Green agent: Task resolved to {task_info['full_identifier']}")
    
    # Load task configuration
    import yaml
    with open(task_info['task_yaml'], 'r') as f:
        task_data = yaml.safe_load(f)
    
    # Return complete task environment
    return {
        'task_info': task_info,
        'task_data': task_data,
    }


async def ask_purple_agent_to_solve(
    purple_agent_url: str,
    docker_manager: DockerManager,
    task_config: dict,
    timeout_config: dict,
    dataset_dir: Path = None
):
    """
    Ask purple agent to solve the task by providing SSH access.
    
    Flow:
    1. Green agent starts container and gets SSH command
    2. Green agent sends SSH command to purple agent
    3. Purple agent solves the task via SSH
    4. Green agent runs tests and evaluates
    """
    
    # Step 1: Start container and get SSH access
    print(f"Green agent: Starting container for task {task_config['task_name']}...")
    print(f"DEBUG ask_purple_agent_to_solve: build_context = {task_config.get('build_context')!r}")
    
    container, ssh_command = docker_manager.start_task_container(
        task_name=task_config['task_name'],
        image=task_config['image'],
        command=task_config.get('command', 'sleep infinity'),
        environment=task_config.get('environment', {}),
        ports=task_config.get('ports', {"22/tcp": None}),
        network=task_config.get('network'),
        build_context=task_config.get('build_context'),
        dockerfile=task_config.get('dockerfile', 'Dockerfile'),
        nocache=task_config.get('nocache', False)
    )
    
    print(f"Green agent: Container started. SSH command: {ssh_command}")
    
    # Step 2: Send task description with SSH access to purple agent
    # Use full_identifier so purple agent can find the correct solution
    task_identifier_to_send = task_config.get('full_identifier', task_config['task_name'])
    
    task_description = f"""
You are given SSH access to a Docker container with a DevOps/software engineering task.

<task_name>
{task_identifier_to_send}
</task_name>

<container_name>
{task_config['task_name']}
</container_name>

<ssh_command>
{ssh_command}
</ssh_command>

<instruction>
{task_config.get('instruction', 'Fix the bug in the code and make all tests pass.')}
</instruction>

<timeout>
You have {timeout_config.get('max_agent_timeout_sec', 1200)} seconds to complete this task.
</timeout>

Please connect via SSH and solve the task. 
"""
    
    # Add dataset_dir if provided (for Oracle agent to find solutions)
    if dataset_dir:
        task_description += f"\n<dataset_dir>{dataset_dir}</dataset_dir>"
    
    print(f"Green agent: Sending task to purple agent at {purple_agent_url}...")
    print(f"Task description:\n{task_description}")
    
    start_time = time.time()
    messenger = Messenger()
    
    try:
        # Send initial message to purple agent using new A2A client
        purple_response = await messenger.talk_to_agent(
            message=task_description,
            url=purple_agent_url,
            new_conversation=True,
            timeout=int(timeout_config.get('max_agent_timeout_sec', 1200) + 60)  # Add buffer
        )
        
        print(f"Green agent: Purple agent response:\n{purple_response}")
        
        # Check if purple agent finished
        tags = parse_tags(purple_response)
        agent_status = tags.get('status', 'in_progress')
        
        # Wait for completion or timeout
        agent_timeout_occurred = False
        max_wait = timeout_config.get('max_agent_timeout_sec', 1200)
        
        while agent_status != 'completed' and time.time() - start_time < max_wait:
            # In real scenario, purple agent would work asynchronously
            # For now, we assume it's done after first response
            break
        
        if time.time() - start_time >= max_wait:
            agent_timeout_occurred = True
            print(f"Green agent: Purple agent timeout after {max_wait}s")
        
        agent_duration = time.time() - start_time
        
    except Exception as e:
        print(f"Green agent: Error communicating with purple agent: {e}")
        agent_duration = time.time() - start_time
        
        # Clean up container on failure
        try:
            docker_manager.stop_and_remove_container(task_config['task_name'])
        except:
            pass
        
        # Return failure result
        return {
            'success': False,
            'agent_duration': agent_duration,
            'test_duration': 0,
            'total_duration': agent_duration,
            'agent_timeout': False,
            'test_timeout': False,
            'test_exit_code': -1,
            'test_output': '',
            'purple_agent_response': f"Error: {e}",
            'ssh_command': '',
            'parser_name': task_config.get('parser_name', 'swebench'),
            'error': str(e)
        }
    
    # Check if purple agent actually completed
    if agent_status != 'completed':
        print(f"Green agent: Purple agent did not complete (status: {agent_status})")
        
        # Clean up container
        try:
            docker_manager.stop_and_remove_container(task_config['task_name'])
        except:
            pass
        
        # Return failure result
        return {
            'success': False,
            'agent_duration': agent_duration,
            'test_duration': 0,
            'total_duration': agent_duration,
            'agent_timeout': agent_timeout_occurred,
            'test_timeout': False,
            'test_exit_code': -1,
            'test_output': '',
            'purple_agent_response': purple_response,
            'ssh_command': ssh_command,
            'parser_name': task_config.get('parser_name', 'swebench'),
            'error': f"Purple agent did not complete (status: {agent_status})"
        }
    
    # Step 3: Run tests
    print("Green agent: Running tests...")
    test_start = time.time()
    
    # Copy tests
    if task_config.get('has_tests', True):
        docker_manager.copy_to_container(
            container_name=task_config['task_name'],
            src_path=f"{task_config.get('build_context', '')}/tests",
            dest_path="/tests"
        )
    
    # Copy and run test script
    docker_manager.copy_to_container(
        container_name=task_config['task_name'],
        src_path=f"{task_config.get('build_context', '')}/run-tests.sh",
        dest_path="/run-tests.sh"
    )
    
    # Fix /home/fix-run.sh to apply test.patch and fix.patch separately.
    # The default fix-run.sh passes both patches to a single `git apply` call,
    # which fails entirely when fix.patch is empty (agent made no changes).
    # This causes tests to run on raw base code, producing false positives.
    # We surgically replace that line so test.patch is always applied independently.
    fix_run_patch_cmd = (
        "python3 -c \""
        "import re, os\n"
        "orig = open('/home/fix-run.sh').read()\n"
        "fixed = re.sub(\n"
        "    r'git apply /home/test\\.patch /home/fix\\.patch',\n"
        "    'git apply /home/test.patch\\n"
        "if [ -s /home/fix.patch ]; then git apply /home/fix.patch; fi',\n"
        "    orig\n"
        ")\n"
        "open('/home/fix-run.sh', 'w').write(fixed)\n"
        "os.chmod('/home/fix-run.sh', 0o755)\n"
        "\""
    )
    out, rc, _ = docker_manager.exec_command_in_container(
        container_name=task_config['task_name'],
        command=fix_run_patch_cmd,
        timeout_sec=10
    )
    if rc == 0:
        print("Green agent: Patched /home/fix-run.sh to apply patches separately")
    else:
        print(f"Green agent: Warning - could not patch /home/fix-run.sh (rc={rc}): {out}")
    
    test_timeout = timeout_config.get('max_test_timeout_sec', 600)
    
    # Set TEST_DIR environment variable for test scripts that need it
    # The test directory is typically at /tests or relative to build context
    test_command = "export TEST_DIR=/tests && bash /run-tests.sh"
    
    test_output, test_exit, test_timeout_occurred = docker_manager.exec_command_in_container(
        container_name=task_config['task_name'],
        command=test_command,
        timeout_sec=test_timeout
    )
    
    test_duration = time.time() - test_start
    
    print(f"Green agent: Tests completed. Exit code: {test_exit}")
    
    # Step 4: Parse results based on parser_name
    parser_name = task_config.get('parser_name', 'swebench')
    print(f"Green agent: Parsing results with {parser_name} parser...")
    
    passed = False
    
    if parser_name == 'swebench':
        # SWEBench parser (same as terminal-bench/terminal_bench/parsers/swebench_parser.py)
        START_MARKER = "SWEBench results starts here"
        END_MARKER = "SWEBench results ends here"
        
        if START_MARKER not in test_output or END_MARKER not in test_output:
            print("Green agent: SWEBench markers not found in output")
            passed = False
        else:
            # Extract content between markers
            content = test_output.split(START_MARKER, 1)[-1]
            content = content.rsplit(END_MARKER, 1)[0]
            block = content.strip()
            
            # Simple check: if block is "PASSED", then passed
            if block == "PASSED":
                passed = True
                print("Green agent: SWEBench result = PASSED")
            else:
                passed = False
                print(f"Green agent: SWEBench result = FAILED (block: {block[:100]})")
    
    elif parser_name == 'pytest':
        # Pytest parser (same as terminal-bench/terminal_bench/parsers/pytest_parser.py)
        import re
        
        # Look for "short test summary info" section
        SHORT_TEST_SUMMARY_INFO_PATTERN = r"=+\s*short test summary info\s*=+"
        parts = re.split(
            pattern=SHORT_TEST_SUMMARY_INFO_PATTERN,
            string=test_output,
            flags=re.IGNORECASE,
            maxsplit=1,
        )
        
        if len(parts) < 2:
            # No short test summary found, check exit code
            if test_exit == 0:
                passed = True
                print("Green agent: Pytest no summary found, but exit code 0 = PASSED")
            else:
                passed = False
                print(f"Green agent: Pytest no summary found, exit code {test_exit} = FAILED")
        else:
            # Parse the short test summary section
            short_test_summary = parts[1]
            
            # Check for any FAILED/ERROR/XPASS lines (these indicate failure)
            has_failed = bool(re.search(r"^(FAILED|ERROR|XPASS)\s+", short_test_summary, re.MULTILINE))
            
            if has_failed:
                passed = False
                print("Green agent: Pytest summary shows FAILED/ERROR/XPASS = FAILED")
            else:
                # No failures in summary, consider it passed
                passed = True
                print("Green agent: Pytest summary shows no failures = PASSED")
    
    else:
        # Unknown parser, use simple heuristic
        print(f"Green agent: Unknown parser '{parser_name}', using simple heuristic")
        passed = test_exit == 0
    
    print(f"Green agent: Final result = {'PASSED' if passed else 'FAILED'}")
    
    return {
        "success": passed,
        "agent_duration": agent_duration,
        "test_duration": test_duration,
        "total_duration": time.time() - start_time,
        "agent_timeout": agent_timeout_occurred,
        "test_timeout": test_timeout_occurred,
        "test_exit_code": test_exit,
        "test_output": test_output[:1000],  # Truncate for artifact
        "purple_agent_response": purple_response[:500],  # Truncate
        "ssh_command": ssh_command,
        "parser_name": parser_name,
    }


class Agent:
    """DevOps Green Agent - manages batch evaluation of DevOps tasks."""
    
    required_roles: list[str] = ["purple_agent"]
    
    def __init__(self):
        self.docker_manager = DockerManager()
    
    def validate_request(self, request: BatchEvalRequest) -> tuple[bool, str]:
        """Validate batch evaluation request."""
        missing_roles = set(self.required_roles) - set(request.participants.keys())
        if missing_roles:
            return False, f"Missing required roles: {missing_roles}"
        return True, "ok"
    
    async def run(self, message: Message, updater: TaskUpdater) -> None:
        """Run batch evaluation of DevOps tasks.
        
        Args:
            message: The incoming message with evaluation request
            updater: Report progress and results
        
        Expected JSON format:
        {
            "participants": {
                "purple_agent": "http://purple-agent-url:9020"
            },
            "config": {
                "task_type": "issue_resolving",
                "task_ids": ["task1", "task2"],
                "dataset_dir": "/DevOps-Gym",
                "force_reclone": false
            }
        }
        """
        input_text = get_message_text(message)
        
        # Try to parse as JSON (Pydantic validation)
        try:
            request: BatchEvalRequest = BatchEvalRequest.model_validate_json(input_text)
            ok, msg = self.validate_request(request)
            if not ok:
                await updater.reject(new_agent_text_message(msg))
                return
        except ValidationError as e:
            # Fall back to XML tags format for backward compatibility
            print("Green agent: JSON validation failed, trying XML tags format...")
            try:
                tags = parse_tags(input_text)
                print(f"Green agent: Parsed XML tags: {tags}")
                
                # Convert to BatchEvalRequest
                participants = {'purple_agent': tags.get('purple_agent_url', '')}
                config = {}
                if tags.get('task_type'):
                    config['task_type'] = tags.get('task_type').strip()
                if tags.get('task_ids'):
                    task_ids_str = tags.get('task_ids', '').strip()
                    config['task_ids'] = [t.strip() for t in task_ids_str.replace(',', ' ').split() if t.strip()]
                if tags.get('dataset_dir'):
                    config['dataset_dir'] = tags.get('dataset_dir').strip()
                if tags.get('force_reclone'):
                    config['force_reclone'] = tags.get('force_reclone', '').strip().lower() in ('true', '1', 'yes')
                
                request = BatchEvalRequest(participants=participants, config=config)
                ok, msg = self.validate_request(request)
                if not ok:
                    await updater.reject(new_agent_text_message(msg))
                    return
            except Exception as parse_error:
                await updater.reject(new_agent_text_message(f"Invalid request format. Expected JSON or XML tags. Parse error: {e}, {parse_error}"))
                return
        
        # Extract parameters
        purple_agent_url = str(request.participants["purple_agent"])
        task_type = request.config.get("task_type")
        task_ids = request.config.get("task_ids")
        dataset_dir_str = request.config.get("dataset_dir", "")
        dataset_dir = Path(dataset_dir_str) if dataset_dir_str else None
        force_reclone = request.config.get("force_reclone", False)
        output_dir_str = request.config.get("output_dir", "")
        output_dir = Path(output_dir_str) if output_dir_str else None
        
        # Docker network URL translation
        if purple_agent_url and 'localhost' in purple_agent_url:
            if os.path.exists('/.dockerenv') or os.path.exists('/run/.containerenv'):
                original_url = purple_agent_url
                purple_agent_url = purple_agent_url.replace('localhost', 'oracle-agent')
                purple_agent_url = purple_agent_url.replace('127.0.0.1', 'oracle-agent')
                print(f"Green agent: Translated Docker URL: {original_url} → {purple_agent_url}")
        
        print(f"Green agent: Purple agent URL: {purple_agent_url}", flush=True)
        print(f"Green agent: Task type: {task_type or 'all'}", flush=True)
        print(f"Green agent: Task IDs: {task_ids or 'all'}", flush=True)
        print(f"Green agent: Dataset dir: {dataset_dir}", flush=True)
        print(f"Green agent: Force re-clone: {force_reclone}", flush=True)
        print(f"Green agent: Output dir: {output_dir}", flush=True)
        
        # Create output directory if specified
        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)
            print(f"Green agent: Created output directory: {output_dir}", flush=True)
        
        # Initialize dataset manager
        dataset_mgr = DatasetManager(dataset_dir, force_reclone=force_reclone)
        
        # Get list of tasks to run
        await updater.update_status(
            TaskState.working,
            new_agent_text_message(f"🔍 Discovering tasks (type={task_type or 'all'}, ids={len(task_ids) if task_ids else 'all'})...")
        )
        
        try:
            if task_ids:
                task_identifiers = task_ids
            else:
                task_identifiers = dataset_mgr.list_tasks(task_type=task_type)
            
            total_tasks = len(task_identifiers)
            await updater.update_status(
                TaskState.working,
                new_agent_text_message(f"📋 Found {total_tasks} tasks to evaluate")
            )
            
        except Exception as e:
            import traceback
            error_msg = f"Failed to list tasks: {e}\n{traceback.format_exc()}"
            print(f"Green agent: {error_msg}")
            await updater.failed(new_agent_text_message(f"❌ {error_msg}"))
            return
        
        # Run evaluations sequentially
        results = []
        for idx, task_identifier in enumerate(task_identifiers, 1):
            await updater.update_status(
                TaskState.working,
                new_agent_text_message(f"⚙️  [{idx}/{total_tasks}] Evaluating: {task_identifier}")
            )
            
            try:
                # Get task environment
                task_env = get_task_environment(task_identifier, dataset_dir)
                task_info = task_env['task_info']
                task_data = task_env['task_data']
                
                # Build task config
                build_context_path = str(task_info['task_path'])
                print(f"DEBUG: task_info['task_path'] = {task_info['task_path']!r}")
                print(f"DEBUG: build_context_path = {build_context_path!r}")
                
                task_config = {
                    'task_name': task_info['task_name'],
                    'full_identifier': task_info['full_identifier'],
                    'image': f"{task_info['task_name']}:local",
                    'command': 'sleep infinity',
                    'environment': {'TERM': 'xterm-256color'},
                    'ports': {'22/tcp': None},
                    'build_context': build_context_path,
                    'dockerfile': 'Dockerfile',
                    'instruction': task_data.get('instruction', ''),
                    'max_agent_timeout_sec': task_data.get('max_agent_timeout_sec', 1200),
                    'max_test_timeout_sec': task_data.get('max_test_timeout_sec', 600),
                    'parser_name': task_data.get('parser_name', 'swebench'),
                    'has_tests': task_info['tests_dir'] is not None,
                }
                print(f"DEBUG: task_config['build_context'] = {task_config['build_context']!r}")
                
                timeout_config = {
                    'max_agent_timeout_sec': task_config['max_agent_timeout_sec'],
                    'max_test_timeout_sec': task_config['max_test_timeout_sec'],
                }
                
                # Run evaluation
                print(f"Green agent: Calling ask_purple_agent_to_solve for {task_identifier}...", flush=True)
                
                # Send progress update
                try:
                    await event_queue.enqueue_event(
                        new_agent_text_message(f"⏳ [{idx}/{total_tasks}] Running task: {task_identifier}")
                    )
                except:
                    pass  # Ignore if queue is closed
                
                try:
                    result = await ask_purple_agent_to_solve(
                        purple_agent_url=purple_agent_url,
                        docker_manager=self.docker_manager,
                        task_config=task_config,
                        timeout_config=timeout_config,
                        dataset_dir=dataset_dir
                    )
                    print(f"Green agent: ask_purple_agent_to_solve completed for {task_identifier}", flush=True)
                except Exception as e:
                    print(f"Green agent: ERROR in ask_purple_agent_to_solve: {e}", flush=True)
                    import traceback
                    traceback.print_exc()
                    raise
                
                # Store result
                result['task_identifier'] = task_identifier
                results.append(result)
                
                # Save result to file if output_dir is specified
                if output_dir:
                    task_output_dir = output_dir / task_identifier.replace('/', '__')
                    task_output_dir.mkdir(parents=True, exist_ok=True)
                    
                    # Save agent response (Claude Code log)
                    agent_log_file = task_output_dir / "agent_log.txt"
                    with open(agent_log_file, 'w', encoding='utf-8') as f:
                        f.write(f"Task: {task_identifier}\n")
                        f.write(f"Status: {'PASSED' if result['success'] else 'FAILED'}\n")
                        f.write(f"Agent Duration: {result.get('agent_duration', 0):.2f}s\n")
                        f.write(f"Agent Timeout: {result.get('agent_timeout', False)}\n")
                        f.write(f"SSH Command: {result.get('ssh_command', 'N/A')}\n")
                        f.write("=" * 80 + "\n")
                        f.write("Purple Agent Response:\n")
                        f.write("=" * 80 + "\n")
                        f.write(result.get('purple_agent_response', 'No response'))
                        f.write("\n")
                    
                    # Save test output (evaluation result)
                    test_output_file = task_output_dir / "evaluation_output.txt"
                    with open(test_output_file, 'w', encoding='utf-8') as f:
                        f.write(f"Task: {task_identifier}\n")
                        f.write(f"Status: {'PASSED' if result['success'] else 'FAILED'}\n")
                        f.write(f"Test Duration: {result.get('test_duration', 0):.2f}s\n")
                        f.write(f"Test Timeout: {result.get('test_timeout', False)}\n")
                        f.write(f"Test Exit Code: {result.get('test_exit_code', -1)}\n")
                        f.write(f"Parser: {result.get('parser_name', 'N/A')}\n")
                        f.write("=" * 80 + "\n")
                        f.write("Test Output:\n")
                        f.write("=" * 80 + "\n")
                        f.write(result.get('test_output', 'No test output'))
                        f.write("\n")
                        if 'error' in result:
                            f.write("\n" + "=" * 80 + "\n")
                            f.write("Error:\n")
                            f.write("=" * 80 + "\n")
                            f.write(result['error'])
                            f.write("\n")
                    
                    # Save summary JSON
                    summary_json_file = task_output_dir / "summary.json"
                    with open(summary_json_file, 'w', encoding='utf-8') as f:
                        json.dump(result, f, indent=2, ensure_ascii=False)
                    
                    print(f"Green agent: Saved results to {task_output_dir}", flush=True)
                
                # Report progress
                status_emoji = "✅" if result['success'] else "❌"
                await updater.update_status(
                    TaskState.working,
                    new_agent_text_message(
                        f"{status_emoji} [{idx}/{total_tasks}] {task_identifier}: "
                        f"{'PASSED' if result['success'] else 'FAILED'} "
                        f"({result['total_duration']:.1f}s)"
                    )
                )
                
            except Exception as e:
                import traceback
                error_msg = f"❌ [{idx}/{total_tasks}] {task_identifier}: ERROR - {str(e)}"
                print(f"Green agent: {error_msg}")
                print(f"Green agent: Traceback:\n{traceback.format_exc()}")
                await updater.update_status(
                    TaskState.working,
                    new_agent_text_message(error_msg)
                )
                results.append({
                    'task_identifier': task_identifier,
                    'success': False,
                    'error': str(e),
                    'total_duration': 0,
                })
        
        # Generate summary report
        passed = sum(1 for r in results if r.get('success', False))
        failed = total_tasks - passed
        
        summary_text = f"""
🎯 Batch Evaluation Complete

Total Tasks: {total_tasks}
Passed: {passed} ✅
Failed: {failed} ❌
Success Rate: {(passed/total_tasks*100):.1f}%

Purple Agent: {purple_agent_url}
Task Type: {task_type or 'all'}

Detailed Results:
"""
        for r in results:
            status = "✅ PASS" if r.get('success', False) else "❌ FAIL"
            duration = f"{r.get('total_duration', 0):.1f}s" if 'total_duration' in r else "N/A"
            summary_text += f"\n{status} {r['task_identifier']} ({duration})"
        
        # Add artifact with detailed results
        await updater.add_artifact(
            parts=[
                Part(root=TextPart(text=summary_text)),
                Part(root=DataPart(data={
                    "total_tasks": total_tasks,
                    "passed": passed,
                    "failed": failed,
                    "success_rate": passed / total_tasks if total_tasks > 0 else 0,
                    "purple_agent_url": purple_agent_url,
                    "task_type": task_type,
                    "results": results
                }))
            ],
            name="Batch Evaluation Results",
        )
        
        print(f"Green agent: Batch evaluation complete. Passed: {passed}/{total_tasks}")


class DevOpsGreenAgentExecutor(AgentExecutor):
    """Executor for DevOps Green Agent - manages task lifecycle."""
    
    def __init__(self):
        self.agents: dict[str, Agent] = {}  # context_id to agent instance
    
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        msg = context.message
        if not msg:
            raise ServerError(error=InvalidRequestError(message="Missing message in request"))
        
        task = context.current_task
        if task and task.status.state in TERMINAL_STATES:
            raise ServerError(error=InvalidRequestError(message=f"Task {task.id} already processed (state: {task.status.state})"))
        
        if not task:
            task = new_task(msg)
            await event_queue.enqueue_event(task)
        
        context_id = task.context_id
        agent = self.agents.get(context_id)
        if not agent:
            agent = Agent()
            self.agents[context_id] = agent
        
        updater = TaskUpdater(event_queue, task.id, context_id)
        
        await updater.start_work()
        try:
            await agent.run(msg, updater)
            if not updater._terminal_state_reached:
                await updater.complete()
        except Exception as e:
            print(f"Task failed with agent error: {e}")
            import traceback
            traceback.print_exc()
            await updater.failed(new_agent_text_message(f"Agent error: {e}", context_id=context_id, task_id=task.id))
    
    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise ServerError(error=UnsupportedOperationError())


def prepare_agent_card(url):
    skill = AgentSkill(
        id="devops-evaluation",
        name="DevOps Task Evaluation",
        description="Evaluates AI agents on DevOps and software engineering tasks using Docker containers",
        tags=["devops", "docker", "testing", "evaluation"],
        examples=["Evaluate an agent on a bug fixing task", "Test container management skills"],
    )
    
    card = AgentCard(
        name="DevOps Green Agent",
        description="Evaluates purple agents on DevOps tasks by providing SSH access to Docker containers and running tests",
        url=url,
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=True),
        skills=[skill],
    )
    return card


def start_green_agent(host="0.0.0.0", port=9009):
    print("Starting DevOps Green Agent...")
    url = f"http://{host}:{port}"
    card = prepare_agent_card(url)
    
    request_handler = DefaultRequestHandler(
        agent_executor=DevOpsGreenAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )
    
    app = A2AStarletteApplication(
        agent_card=card,
        http_handler=request_handler,
    )
    
    uvicorn.run(app.build(), host=host, port=port)
