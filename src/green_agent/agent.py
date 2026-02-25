"""Green agent implementation - manages DevOps task evaluations."""

import uvicorn
import json
import time
import sys
import os
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCard, AgentSkill, AgentCapabilities, SendMessageSuccessResponse, Message, 
    Part, TextPart, DataPart, TaskArtifactUpdateEvent, Artifact
)
from a2a.utils import new_agent_text_message, get_text_parts

from src.util import parse_tags
from src.util import a2a_helper
from src.docker_manager import DockerManager
from src.dataset_manager import DatasetManager


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
    
    context_id = None
    start_time = time.time()
    
    try:
        # Send initial message to purple agent
        response = await a2a_helper.send_message(
            purple_agent_url, task_description, context_id=context_id
        )
        
        res_root = response.root
        assert isinstance(res_root, SendMessageSuccessResponse)
        res_result = res_root.result
        assert isinstance(res_result, Message)
        
        if context_id is None:
            context_id = res_result.context_id
        
        text_parts = get_text_parts(res_result.parts)
        assert len(text_parts) >= 1, "Expecting at least one text part from purple agent"
        purple_response = "\n".join(text_parts)
        
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
        agent_timeout_occurred = False
        purple_response = f"Error: {e}"
    
    # Step 3: Run tests regardless of purple agent status
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
        # SWEBench parser: check for SWEBench results block
        if "SWEBench results starts here" in test_output and "SWEBench results ends here" in test_output:
            import re
            match = re.search(r"SWEBench results starts here\s+(PASSED|FAILED)\s+SWEBench results ends here", test_output, re.DOTALL)
            if match:
                result_str = match.group(1).strip()
                passed = (result_str == "PASSED")
                print(f"Green agent: SWEBench result = {result_str}")
            else:
                print("Green agent: SWEBench markers found but couldn't parse result")
                passed = False
        else:
            print("Green agent: SWEBench markers not found in output")
            passed = False
    
    elif parser_name == 'pytest':
        # Pytest parser: check for "short test summary info" section
        if test_exit == 0:
            # Exit code 0 means all tests passed
            passed = True
            print("Green agent: Pytest exit code 0 = PASSED")
        elif test_exit == 4:
            # Exit code 4 typically means "no tests were collected" which we treat as FAILED
            passed = False
            print("Green agent: Pytest exit code 4 (no tests collected) = FAILED")
        else:
            # Check if there are any PASSED lines in short test summary
            import re
            if re.search(r"=+\s*short test summary info\s*=+", test_output, re.IGNORECASE):
                # Has summary section
                summary_match = re.split(r"=+\s*short test summary info\s*=+", test_output, flags=re.IGNORECASE, maxsplit=1)
                if len(summary_match) >= 2:
                    summary_section = summary_match[1]
                    # Check if all tests passed (only PASSED lines, no FAILED)
                    has_passed = bool(re.search(r"^PASSED\s+", summary_section, re.MULTILINE))
                    has_failed = bool(re.search(r"^FAILED\s+", summary_section, re.MULTILINE))
                    
                    if has_passed and not has_failed:
                        passed = True
                        print("Green agent: Pytest summary shows all PASSED")
                    else:
                        passed = False
                        print(f"Green agent: Pytest summary shows failures (has_passed={has_passed}, has_failed={has_failed})")
                else:
                    passed = False
            else:
                # No summary section, rely on exit code
                passed = (test_exit == 0)
                print(f"Green agent: No pytest summary, using exit code: {test_exit}")
    
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


class DevOpsGreenAgentExecutor(AgentExecutor):
    def __init__(self):
        self.docker_manager = DockerManager()
    
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        import sys
        print("Green agent: ========== EXECUTE CALLED ==========", flush=True)
        sys.stdout.flush()
        print("Green agent: Received batch evaluation request, parsing...", flush=True)
        sys.stdout.flush()
        try:
            user_input = context.get_user_input()
            print(f"Green agent: User input: {user_input[:200] if user_input else 'None'}...")
            tags = parse_tags(user_input)
            print(f"Green agent: Parsed tags: {tags}")
        except Exception as e:
            print(f"Green agent: ERROR parsing input: {e}")
            import traceback
            traceback.print_exc()
            raise
        
        purple_agent_url = tags.get('purple_agent_url', '')
        task_type = tags.get('task_type', '').strip() or None  # Empty string -> None
        task_ids_str = tags.get('task_ids', '').strip()
        dataset_dir_str = tags.get('dataset_dir', '').strip()
        dataset_dir = Path(dataset_dir_str) if dataset_dir_str else None
        force_reclone_str = tags.get('force_reclone', '').strip().lower()
        force_reclone = force_reclone_str in ('true', '1', 'yes')
        
        # Parse task_ids (comma or space separated)
        task_ids = None
        if task_ids_str:
            # Support both comma and space separated
            task_ids = [t.strip() for t in task_ids_str.replace(',', ' ').split() if t.strip()]
        
        # Docker network URL translation
        # When running in Docker, translate localhost URLs to container network addresses
        if purple_agent_url and 'localhost' in purple_agent_url:
            import os
            # Check if we're running in a Docker container
            if os.path.exists('/.dockerenv') or os.path.exists('/run/.containerenv'):
                # Replace localhost with oracle-agent service name
                original_url = purple_agent_url
                purple_agent_url = purple_agent_url.replace('localhost', 'oracle-agent')
                purple_agent_url = purple_agent_url.replace('127.0.0.1', 'oracle-agent')
                print(f"Green agent: Translated Docker URL: {original_url} → {purple_agent_url}")
        
        print(f"Green agent: Purple agent URL: {purple_agent_url}", flush=True)
        print(f"Green agent: Task type: {task_type or 'all'}", flush=True)
        print(f"Green agent: Task IDs: {task_ids or 'all'}", flush=True)
        print(f"Green agent: Dataset dir: {dataset_dir}", flush=True)
        print(f"Green agent: Force re-clone: {force_reclone}", flush=True)
        
        # Initialize dataset manager
        print(f"Green agent: Initializing DatasetManager...", flush=True)
        dataset_mgr = DatasetManager(dataset_dir, force_reclone=force_reclone)
        print(f"Green agent: DatasetManager initialized", flush=True)
        
        # Get list of tasks to run
        print(f"Green agent: About to send 'Discovering tasks' message...", flush=True)
        await event_queue.enqueue_event(
            new_agent_text_message(f"🔍 Discovering tasks (type={task_type or 'all'}, ids={len(task_ids) if task_ids else 'all'})...")
        )
        print(f"Green agent: 'Discovering tasks' message sent", flush=True)
        
        try:
            print(f"Green agent: Getting task list...", flush=True)
            if task_ids:
                # Specific task IDs provided
                task_identifiers = task_ids
            else:
                # List all tasks (filtered by type if specified)
                task_identifiers = dataset_mgr.list_tasks(task_type=task_type)
            
            total_tasks = len(task_identifiers)
            await event_queue.enqueue_event(
                new_agent_text_message(f"📋 Found {total_tasks} tasks to evaluate")
            )
            
        except Exception as e:
            import traceback
            error_msg = f"Failed to list tasks: {e}"
            print(f"Green agent: {error_msg}")
            print(f"Green agent: Traceback:\n{traceback.format_exc()}")
            await event_queue.enqueue_event(new_agent_text_message(f"❌ {error_msg}"))
            return
        
        # Run evaluations sequentially
        results = []
        for idx, task_identifier in enumerate(task_identifiers, 1):
            await event_queue.enqueue_event(
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
                
                # Report progress
                status_emoji = "✅" if result['success'] else "❌"
                await event_queue.enqueue_event(
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
                await event_queue.enqueue_event(new_agent_text_message(error_msg))
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
        
        # Send summary message
        await event_queue.enqueue_event(new_agent_text_message(summary_text))
        
        # Add artifact with structured data for AgentBeats
        import uuid
        artifact = Artifact(
            artifactId=str(uuid.uuid4()),
            name="Batch Evaluation Results",
            description=f"Evaluation results for {total_tasks} tasks",
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
        )
        
        artifact_event = TaskArtifactUpdateEvent(
            kind="artifact-update",
            taskId=context.task_id,
            contextId=context.context_id,
            artifact=artifact,
        )
        
        await event_queue.enqueue_event(artifact_event)
        
        print(f"Green agent: Artifact sent with ID: {artifact.artifact_id}")
        print(f"Green agent: Batch evaluation complete. Passed: {passed}/{total_tasks}")
    
    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError


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
