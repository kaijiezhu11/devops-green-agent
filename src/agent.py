from typing import Any, Optional
from pydantic import BaseModel, HttpUrl, ValidationError
from a2a.server.tasks import TaskUpdater
from a2a.types import Message, TaskState, Part, TextPart, DataPart
from a2a.utils import get_message_text, new_agent_text_message
import logging

from messenger import Messenger
from docker_manager import DockerManager

logger = logging.getLogger(__name__)


class TaskConfig(BaseModel):
    """Configuration for starting a task container."""
    task_name: str  # e.g., "containerd__containerd-4847"
    image: str  # Docker image to use (or tag if building)
    command: Optional[str] = None
    environment: Optional[dict[str, str]] = None
    ports: Optional[dict[str, Any]] = None
    network: Optional[str] = None
    build_context: Optional[str] = None  # Path to build context (triggers local build)
    dockerfile: Optional[str] = None  # Dockerfile name (default: "Dockerfile")


class RunTestConfig(BaseModel):
    """Configuration for running tests in a container."""
    container_name: str  # Container to run tests in
    task_dir: str  # Task directory name (e.g., "containerd__containerd-4847")
    copy_tests: bool = True  # Whether to copy tests directory
    copy_script: bool = True  # Whether to copy run-tests.sh
    test_script: str = "/run-tests.sh"  # Path to test script in container (after copy)
    output_file: Optional[str] = None  # Optional output file path


class EvalRequest(BaseModel):
    """Request format sent by the AgentBeats platform to green agents."""
    participants: dict[str, HttpUrl]  # role -> agent URL
    config: dict[str, Any]


class Agent:
    # Fill in: list of required participant roles, e.g. ["pro_debater", "con_debater"]
    required_roles: list[str] = []
    # Fill in: list of required config keys, e.g. ["topic", "num_rounds"]
    required_config_keys: list[str] = []

    def __init__(self):
        self.messenger = Messenger()
        try:
            self.docker_manager = DockerManager()
            logger.info("DockerManager initialized successfully")
        except Exception as e:
            logger.warning(f"DockerManager initialization failed: {e}")
            self.docker_manager = None
        # Initialize other state here

    def validate_request(self, request: EvalRequest) -> tuple[bool, str]:
        missing_roles = set(self.required_roles) - set(request.participants.keys())
        if missing_roles:
            return False, f"Missing roles: {missing_roles}"

        missing_config_keys = set(self.required_config_keys) - set(request.config.keys())
        if missing_config_keys:
            return False, f"Missing config keys: {missing_config_keys}"

        # Add additional request validation here

        return True, "ok"

    async def run(self, message: Message, updater: TaskUpdater) -> None:
        """Implement your agent logic here.

        Args:
            message: The incoming message
            updater: Report progress (update_status) and results (add_artifact)

        Use self.messenger.talk_to_agent(message, url) to call other agents.
        """
        input_text = get_message_text(message)

        try:
            request: EvalRequest = EvalRequest.model_validate_json(input_text)
            ok, msg = self.validate_request(request)
            if not ok:
                await updater.reject(new_agent_text_message(msg))
                return
        except ValidationError as e:
            await updater.reject(new_agent_text_message(f"Invalid request: {e}"))
            return

        # Check request type
        if "task_config" in request.config:
            await self._handle_container_task(request, updater)
        elif "run_test" in request.config:
            await self._handle_run_test(request, updater)
        else:
            # Default evaluation logic
            await self._handle_evaluation(request, updater)
    
    async def _handle_container_task(
        self, request: EvalRequest, updater: TaskUpdater
    ) -> None:
        """Handle container startup and management tasks."""
        if not self.docker_manager:
            await updater.reject(
                new_agent_text_message("Docker is not available in this agent")
            )
            return
        
        try:
            # Parse task configuration
            task_config = TaskConfig(**request.config["task_config"])
            
            await updater.update_status(
                TaskState.working,
                new_agent_text_message(f"Starting container: {task_config.task_name}...")
            )
            
            # Start the container on the host
            container, ssh_command = self.docker_manager.start_task_container(
                task_name=task_config.task_name,
                image=task_config.image,
                command=task_config.command,
                environment=task_config.environment,
                ports=task_config.ports,
                network=task_config.network,
                build_context=task_config.build_context,
                dockerfile=task_config.dockerfile,
            )
            
            logger.info(f"Container started: {container.name}")
            
            # Return results with SSH connection info
            await updater.add_artifact(
                parts=[
                    Part(root=TextPart(text=f"Container '{task_config.task_name}' started successfully!\n\nSSH Connection:\n```\n{ssh_command}\n```")),
                    Part(root=DataPart(data={
                        "container_id": container.id,
                        "container_name": container.name,
                        "status": container.status,
                        "ssh_command": ssh_command,
                        "ports": container.ports,
                    }))
                ],
                name="Container Info",
            )
            
        except ValidationError as e:
            await updater.reject(
                new_agent_text_message(f"Invalid task configuration: {e}")
            )
        except Exception as e:
            logger.error(f"Failed to start container: {e}")
            await updater.reject(
                new_agent_text_message(f"Failed to start container: {e}")
            )
    
    async def _handle_run_test(
        self, request: EvalRequest, updater: TaskUpdater
    ) -> None:
        """Handle test execution requests."""
        if not self.docker_manager:
            await updater.reject(
                new_agent_text_message("Docker is not available in this agent")
            )
            return
        
        try:
            # Parse test configuration
            test_config = RunTestConfig(**request.config["run_test"])
            
            await updater.update_status(
                TaskState.working,
                new_agent_text_message(f"Preparing tests for container: {test_config.container_name}...")
            )
            
            # Step 1: Copy tests directory if requested
            if test_config.copy_tests:
                logger.info("Copying tests directory to container")
                tests_src = f"/workspace/{test_config.task_dir}/tests"
                tests_dest = "/tests"
                success = self.docker_manager.copy_to_container(
                    container_name=test_config.container_name,
                    src_path=tests_src,
                    dest_path=tests_dest,
                )
                if not success:
                    await updater.reject(
                        new_agent_text_message(f"Failed to copy tests directory from {tests_src}")
                    )
                    return
            
            # Step 2: Copy run-tests.sh if requested
            if test_config.copy_script:
                logger.info("Copying run-tests.sh to container")
                script_src = f"/workspace/{test_config.task_dir}/run-tests.sh"
                script_dest = "/run-tests.sh"
                success = self.docker_manager.copy_to_container(
                    container_name=test_config.container_name,
                    src_path=script_src,
                    dest_path=script_dest,
                )
                if not success:
                    await updater.reject(
                        new_agent_text_message(f"Failed to copy test script from {script_src}")
                    )
                    return
            
            await updater.update_status(
                TaskState.working,
                new_agent_text_message(f"Running tests in container: {test_config.container_name}...")
            )
            
            # Step 3: Execute tests in the container
            output, exit_code = self.docker_manager.exec_command_in_container(
                container_name=test_config.container_name,
                command=f"bash {test_config.test_script}",
                output_file=test_config.output_file,
            )
            
            logger.info(f"Tests completed with exit code: {exit_code}")
            
            # Step 4: Save test log to host machine
            import os
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_filename = f"test_log_{test_config.container_name}_{timestamp}.txt"
            log_path = f"/workspace/{log_filename}"
            
            try:
                with open(log_path, 'w') as f:
                    f.write(f"=== Test Execution Log ===\n")
                    f.write(f"Container: {test_config.container_name}\n")
                    f.write(f"Task Directory: {test_config.task_dir}\n")
                    f.write(f"Test Script: {test_config.test_script}\n")
                    f.write(f"Exit Code: {exit_code}\n")
                    f.write(f"Status: {'PASSED' if exit_code == 0 else 'FAILED'}\n")
                    f.write(f"Timestamp: {datetime.now().isoformat()}\n")
                    f.write(f"\n{'='*60}\n")
                    f.write(f"Test Output:\n")
                    f.write(f"{'='*60}\n\n")
                    f.write(output)
                logger.info(f"Test log saved to host: {log_filename}")
            except Exception as e:
                logger.error(f"Failed to save test log: {e}")
            
            # Prepare result message
            status = "✅ PASSED" if exit_code == 0 else "❌ FAILED"
            result_text = f"Test Execution {status}\n\n"
            result_text += f"Container: {test_config.container_name}\n"
            result_text += f"Task Directory: {test_config.task_dir}\n"
            result_text += f"Script: {test_config.test_script}\n"
            result_text += f"Exit Code: {exit_code}\n"
            result_text += f"Log File: /scr/yuan/devops-greeen-agent/{log_filename}\n\n"
            result_text += "=== Test Output ===\n"
            result_text += output
            
            # Return results
            await updater.add_artifact(
                parts=[
                    Part(root=TextPart(text=result_text)),
                    Part(root=DataPart(data={
                        "container_name": test_config.container_name,
                        "task_dir": test_config.task_dir,
                        "test_script": test_config.test_script,
                        "exit_code": exit_code,
                        "status": "passed" if exit_code == 0 else "failed",
                        "output": output,
                        "log_file": f"/scr/yuan/devops-greeen-agent/{log_filename}",
                    }))
                ],
                name="Test Results",
            )
            
        except ValidationError as e:
            await updater.reject(
                new_agent_text_message(f"Invalid test configuration: {e}")
            )
        except Exception as e:
            logger.error(f"Failed to run tests: {e}")
            await updater.reject(
                new_agent_text_message(f"Failed to run tests: {e}")
            )
    
    async def _handle_evaluation(
        self, request: EvalRequest, updater: TaskUpdater
    ) -> None:
        """Handle standard evaluation tasks."""
        # Replace example code below with your agent logic
        # Use request.participants to get participant agent URLs by role
        # Use request.config for assessment parameters

        await updater.update_status(
            TaskState.working, new_agent_text_message("Evaluating participants...")
        )
        
        # Example: communicate with participants
        results = {}
        for role, url in request.participants.items():
            try:
                response = await self.messenger.talk_to_agent(
                    message="Hello, please introduce yourself",
                    url=str(url),
                )
                results[role] = response
            except Exception as e:
                logger.error(f"Failed to communicate with {role}: {e}")
                results[role] = f"Error: {e}"
        
        await updater.add_artifact(
            parts=[
                Part(root=TextPart(text="Evaluation completed.")),
                Part(root=DataPart(data={
                    "participants": results,
                    "config": request.config,
                    # Add structured assessment results here
                }))
            ],
            name="Evaluation Result",
        )
