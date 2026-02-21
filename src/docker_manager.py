"""Docker container management for the Green Agent."""
import os
import docker
from docker.models.containers import Container
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class DockerManager:
    """Manages Docker containers on the host machine from within a container."""
    
    def __init__(self):
        """
        Initialize Docker client.
        Connects to host Docker daemon via mounted socket.
        """
        try:
            # Connect to host Docker via mounted socket
            self.client = docker.DockerClient(base_url='unix://var/run/docker.sock')
            self.client.ping()
            logger.info("Successfully connected to Docker daemon")
        except Exception as e:
            logger.error(f"Failed to connect to Docker daemon: {e}")
            raise RuntimeError(
                "Cannot connect to Docker daemon. "
                "Make sure /var/run/docker.sock is mounted in the container."
            ) from e
    
    def setup_ssh_in_container(self, container: Container, ssh_pubkey: Optional[str] = None) -> bool:
        """
        Install and configure SSH server in a running container.
        
        Args:
            container: Container object
            ssh_pubkey: Optional SSH public key for passwordless login
        
        Returns:
            True if successful, False otherwise
        """
        try:
            logger.info(f"Setting up SSH in container: {container.name}")
            
            # Default SSH public key
            if not ssh_pubkey:
                ssh_pubkey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAID3nu+sajUcas+4SlXZrKydnvtu2oT9NGMnsnk1J59ty aa3101132006@gmail.com"
            
            # Get container's working directory
            workdir = self._get_container_workdir(container)
            
            # Install and configure SSH server
            setup_script = f"""#!/bin/bash
set -e
apt-get update -qq
apt-get install -y -qq openssh-server
mkdir -p /var/run/sshd /root/.ssh
chmod 700 /root/.ssh
echo "{ssh_pubkey}" > /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config
sed -i 's/#PubkeyAuthentication yes/PubkeyAuthentication yes/' /etc/ssh/sshd_config
# Set default directory to container's WORKDIR when SSH login
echo 'cd {workdir} 2>/dev/null || cd /root' >> /root/.bashrc
/usr/sbin/sshd
echo "SSH setup complete"
"""
            
            # Execute setup script
            result = container.exec_run(
                cmd=["bash", "-c", setup_script],
                stdout=True,
                stderr=True,
            )
            
            if result.exit_code == 0:
                logger.info(f"SSH configured successfully in {container.name}, default dir: {workdir}")
                return True
            else:
                logger.error(f"SSH setup failed: {result.output.decode()}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to setup SSH: {e}")
            return False
    
    def build_image(
        self,
        path: str,
        tag: str,
        dockerfile: str = "Dockerfile",
        nocache: bool = False,
    ) -> str:
        """
        Build a Docker image from a Dockerfile.
        
        Args:
            path: Build context path (relative to /workspace in container)
            tag: Tag for the built image
            dockerfile: Dockerfile name (default: "Dockerfile")
            nocache: If True, build without using cache
        
        Returns:
            Image ID
        """
        try:
            # Build path is relative to the mounted workspace
            build_path = f"/workspace/{path}" if not path.startswith("/workspace") else path
            
            logger.info(f"Building image from {build_path}/{dockerfile}")
            logger.info(f"Tag: {tag}")
            if nocache:
                logger.info("Building WITHOUT cache (--no-cache)")
            
            # Build the image
            image, build_logs = self.client.images.build(
                path=build_path,
                dockerfile=dockerfile,
                tag=tag,
                rm=True,  # Remove intermediate containers
                forcerm=True,  # Always remove intermediate containers
                nocache=nocache,  # Disable cache if requested
            )
            
            # Log build output
            for log in build_logs:
                if 'stream' in log:
                    logger.info(log['stream'].strip())
            
            logger.info(f"Image built successfully: {tag}")
            return image.id
            
        except Exception as e:
            logger.error(f"Failed to build image: {e}")
            raise RuntimeError(f"Failed to build image: {e}") from e
    
    def start_task_container(
        self,
        task_name: str,
        image: str,
        command: Optional[str] = None,
        environment: Optional[dict] = None,
        ports: Optional[dict] = None,
        network: Optional[str] = None,
        build_context: Optional[str] = None,
        dockerfile: Optional[str] = None,
        nocache: bool = False,
    ) -> tuple[Container, str]:
        """
        Start a new task container on the host.
        
        Args:
            task_name: Name for the container (e.g., "containerd__containerd-4847")
            image: Docker image to use (or tag name if building)
            command: Optional command to run
            environment: Optional environment variables
            ports: Optional port mappings (e.g., {"22/tcp": None} for random SSH port)
            network: Optional network name
            build_context: Optional path to build context (triggers local build)
            dockerfile: Optional Dockerfile name (default: "Dockerfile")
            nocache: If True, build image without using cache
        
        Returns:
            Tuple of (Container object, SSH command string)
        """
        try:
            # Check if container with same name already exists
            try:
                existing_container = self.client.containers.get(task_name)
                logger.warning(f"Container {task_name} already exists. Removing it...")
                
                # Stop the container if it's running
                try:
                    if existing_container.status in ['running', 'paused']:
                        logger.info(f"Stopping existing container {task_name}...")
                        existing_container.stop(timeout=10)
                except Exception as e:
                    logger.warning(f"Failed to stop container gracefully: {e}")
                
                # Force remove the container
                try:
                    logger.info(f"Removing existing container {task_name}...")
                    existing_container.remove(force=True)
                    logger.info(f"Successfully removed old container {task_name}")
                except Exception as e:
                    logger.error(f"Failed to remove container: {e}")
                    raise RuntimeError(f"Cannot remove existing container {task_name}: {e}")
                    
            except docker.errors.NotFound:
                # Container doesn't exist, which is fine
                logger.info(f"No existing container found with name {task_name}")
            
            # If build_context is provided, build the image first
            if build_context:
                logger.info(f"Building image from local Dockerfile: {build_context}")
                self.build_image(
                    path=build_context,
                    tag=image,  # Use image as tag name
                    dockerfile=dockerfile or "Dockerfile",
                    nocache=nocache,
                )
            
            # Default to exposing SSH if not specified
            if ports is None:
                ports = {"22/tcp": None}  # Random port for SSH
            
            container_config = {
                "name": task_name,
                "image": image,
                "detach": True,
                "ports": ports,
                "environment": environment or {},
            }
            
            if command:
                container_config["command"] = command
            
            if network:
                container_config["network"] = network
            
            # Start the container
            logger.info(f"Starting container: {task_name}")
            container = self.client.containers.run(**container_config)
            
            # Reload to get updated port information
            container.reload()
            
            # Automatically setup SSH in the container
            logger.info(f"Configuring SSH in container: {task_name}")
            self.setup_ssh_in_container(container)
            
            # Automatically copy trigger test script to container
            logger.info(f"Copying trigger test script to container: {task_name}")
            self._copy_trigger_script_to_container(container, task_name)
            
            # Fix reset.sh if it's empty (Dockerfile heredoc syntax issue)
            logger.info(f"Checking and fixing reset.sh if needed in container: {task_name}")
            self._fix_reset_sh_if_needed(container)
            
            # Generate SSH connection command
            ssh_command = self._generate_ssh_command(container, task_name)
            
            logger.info(f"Container {task_name} started successfully")
            return container, ssh_command
            
        except docker.errors.APIError as e:
            logger.error(f"Docker API error: {e}")
            raise RuntimeError(f"Failed to start container: {e}") from e
    
    def _get_container_workdir(self, container: Container) -> str:
        """
        Get the WORKDIR from container's config.
        
        Args:
            container: Docker container object
        
        Returns:
            Working directory path (defaults to /root if not set)
        """
        try:
            # Reload container to get fresh config
            container.reload()
            
            # Get WorkingDir from container config
            workdir = container.attrs.get('Config', {}).get('WorkingDir', '')
            
            if workdir:
                logger.info(f"Container WORKDIR: {workdir}")
                return workdir
            else:
                # If no WORKDIR set, default to /root
                logger.info("No WORKDIR set in container, using /root")
                return "/root"
                
        except Exception as e:
            logger.warning(f"Failed to get WORKDIR, defaulting to /root: {e}")
            return "/root"
    
    def install_claude_code_in_container(
        self, 
        container: Container, 
        api_key: str,
        prompt: str,
        timeout_sec: float = None
    ) -> tuple[str, int, bool]:
        """
        Install Claude Code in container and run it with a prompt.
        
        Args:
            container: Docker container object
            api_key: Anthropic API key
            prompt: Prompt to pass to Claude Code
            timeout_sec: Optional timeout in seconds for Claude Code execution
        
        Returns:
            Tuple of (output, exit_code, timeout_occurred)
        """
        timeout_occurred = False
        try:
            logger.info(f"Installing Claude Code in container {container.name}...")
            
            # Get container's working directory
            workdir = self._get_container_workdir(container)
            
            # Installation script (exactly matching terminal-bench)
            install_script = """
set -e
apt-get update
apt-get install -y curl

curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.2/install.sh | bash

source "$HOME/.nvm/nvm.sh"

nvm install 22
npm -v

npm install -g @anthropic-ai/claude-code@latest
"""
            
            # Run installation
            logger.info("Running Claude Code installation (terminal-bench style)...")
            exit_code, output = container.exec_run(
                ["/bin/bash", "-c", install_script],
                stream=False,
                demux=False
            )
            
            if exit_code != 0:
                logger.error(f"Claude Code installation failed: {output.decode()}")
                return output.decode(), exit_code, False
            
            logger.info("Claude Code installed successfully")
            
            # Prepare and run claude command (exactly matching terminal-bench)
            logger.info(f"Running Claude Code with prompt in {workdir}...")
            
            # Escape prompt for shlex (terminal-bench uses shlex.quote)
            import shlex
            escaped_prompt = shlex.quote(prompt)
            
            # Define allowed tools (exactly matching terminal-bench ClaudeCodeAgent.ALLOWED_TOOLS)
            allowed_tools = [
                "Bash",
                "Edit",
                "Write",
                "Read",
                "Glob",
                "Grep",
                "LS",
                "WebFetch",
                "NotebookEdit",
                "NotebookRead",
                "TodoRead",
                "TodoWrite",
                "Agent",
            ]
            
            # Build environment variables (matching terminal-bench _env property)
            env_vars = f"""
export ANTHROPIC_API_KEY="{api_key}"
export FORCE_AUTO_BACKGROUND_TASKS=1
export ENABLE_BACKGROUND_TASKS=1
"""
            # Add ANTHROPIC_MODEL if set in environment, otherwise use default
            import os
            model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
            # Remove "anthropic/" prefix if present
            if model.startswith("anthropic/"):
                model = model.removeprefix("anthropic/")
            env_vars += f'export ANTHROPIC_MODEL="{model}"\n'
            
            # Disallow git commands to prevent Claude Code from adding files to staging area
            # This prevents "already exists in working directory" errors during git apply
            claude_script = f"""
source "$HOME/.nvm/nvm.sh"
{env_vars}
cd {workdir}
claude --verbose --output-format stream-json -p {escaped_prompt} --allowedTools {' '.join(allowed_tools)}
"""
            
            # Execute with timeout if specified
            if timeout_sec:
                import threading
                import time
                
                logger.info(f"Running Claude Code with {timeout_sec}s timeout...")
                
                # Create exec instance
                exec_instance = container.client.api.exec_create(
                    container.id,
                    ["/bin/bash", "-c", claude_script],
                    workdir=workdir
                )
                exec_id = exec_instance['Id']
                
                # Start execution in background
                exec_stream = container.client.api.exec_start(exec_id, stream=True, demux=False)
                
                # Collect output with timeout
                start_time = time.time()
                output_chunks = []
                
                try:
                    for chunk in exec_stream:
                        if chunk:
                            output_chunks.append(chunk)
                        
                        # Check timeout
                        if time.time() - start_time > timeout_sec:
                            logger.warning(f"Claude Code timeout after {timeout_sec}s, stopping...")
                            timeout_occurred = True
                            
                            # Try to stop the exec gracefully by killing the process
                            try:
                                # Find and kill the claude process
                                container.exec_run(
                                    ["pkill", "-9", "-f", "claude"],
                                    detach=False
                                )
                            except Exception as e:
                                logger.error(f"Failed to kill claude process: {e}")
                            
                            break
                    
                    # Get final exit code
                    exec_info = container.client.api.exec_inspect(exec_id)
                    exit_code = exec_info.get('ExitCode', -1)
                    
                except Exception as e:
                    logger.error(f"Error during Claude Code execution: {e}")
                    exit_code = -1
                
                result_output = b''.join(output_chunks).decode() if output_chunks else ""
                
                if timeout_occurred:
                    result_output += f"\n\n===== TIMEOUT: Claude Code execution stopped after {timeout_sec} seconds ====="
                    logger.warning(f"Claude Code timed out after {timeout_sec}s")
                else:
                    logger.info(f"Claude Code execution completed with exit code {exit_code}")
            else:
                # No timeout, use simple exec_run
                exit_code, output = container.exec_run(
                    ["/bin/bash", "-c", claude_script],
                    stream=False,
                    demux=False,
                    workdir=workdir
                )
                
                result_output = output.decode() if isinstance(output, bytes) else output
                logger.info(f"Claude Code execution completed with exit code {exit_code}")
            
            return result_output, exit_code, timeout_occurred
            
        except Exception as e:
            logger.error(f"Failed to install/run Claude Code: {e}")
            raise RuntimeError(f"Claude Code setup failed: {e}") from e
    
    def _fix_reset_sh_if_needed(self, container: Container) -> None:
        """
        Fix reset.sh if it's empty due to Dockerfile heredoc syntax issues.
        
        The Dockerfile uses: RUN <<'EOF_RESET' cat > /home/reset.sh
        This syntax doesn't work correctly in some Docker versions, resulting in empty file.
        We fix it dynamically by rewriting the script based on WORKDIR.
        """
        try:
            # Check if reset.sh exists and is non-empty
            exit_code, output = container.exec_run(
                ["/bin/bash", "-c", "[ -s /home/reset.sh ] && echo 'exists' || echo 'empty'"],
                stream=False,
                demux=False
            )
            
            result = output.decode().strip() if isinstance(output, bytes) else str(output).strip()
            
            if result == 'exists':
                logger.info("reset.sh exists and is non-empty, no fix needed")
                return
            
            logger.warning("reset.sh is empty or missing, recreating it...")
            
            # Get container's WORKDIR
            workdir = self._get_container_workdir(container)
            dir_name = workdir.split('/')[-1]
            
            # Create correct reset.sh content
            reset_content = f"""#!/bin/bash
rm -rf {workdir}/*
cp -r /home/tmp_repo/* {workdir}
"""
            
            # Write the script using heredoc in bash
            fix_script = f"""cat > /home/reset.sh << 'EOF_RESET_FIX'
{reset_content}EOF_RESET_FIX
chmod +x /home/reset.sh
"""
            
            exit_code, output = container.exec_run(
                ["/bin/bash", "-c", fix_script],
                stream=False,
                demux=False
            )
            
            if exit_code == 0:
                logger.info("reset.sh has been recreated successfully")
            else:
                logger.error(f"Failed to recreate reset.sh: {output}")
                
        except Exception as e:
            logger.error(f"Error fixing reset.sh: {e}")
    
    def _copy_trigger_script_to_container(self, container: Container, task_name: str) -> bool:
        """
        Copy the trigger test script to the container.
        
        Args:
            container: Docker container object
            task_name: Name of the container
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # Create the trigger script content
            script_content = '''#!/usr/bin/env python3
"""
Trigger test execution from inside task container
Uses /trigger-test endpoint
"""
import json
import sys
import urllib.request
import urllib.error


def trigger_test(
    container_name="containerd__containerd-4847",
    green_agent_url="http://172.16.0.1:9009"
):
    """Trigger test execution"""
    
    # Test request data
    data = {
        "container_name": container_name,
        "task_dir": "containerd__containerd-4847",
        "copy_tests": True,
        "copy_script": True,
        "test_script": "/run-tests.sh"
    }
    
    print("=" * 60)
    print("  Triggering Test Execution")
    print("=" * 60)
    print(f"\\nContainer: {container_name}\\n")
    print("Sending test request...\\n")
    
    try:
        req = urllib.request.Request(
            green_agent_url + "/trigger-test",
            data=json.dumps(data).encode('utf-8'),
            headers={'Content-Type': 'application/json'}
        )
        
        with urllib.request.urlopen(req, timeout=300) as response:
            result = json.loads(response.read().decode('utf-8'))
            
            if result.get("success"):
                print("Test completed!\\n")
                print("=" * 60)
                print(result.get("message", ""))
                print("=" * 60)
                
                if "data" in result:
                    data = result["data"]
                    print(f"\\nResults:")
                    print(f"  Status: {data.get('status', 'unknown').upper()}")
                    print(f"  Exit code: {data.get('exit_code', 'N/A')}")
                    print(f"  Log file: {data.get('log_file', 'N/A')}")
                
                return 0
            else:
                print(f"Failed: {result.get('error', 'Unknown error')}")
                return 1
            
    except urllib.error.HTTPError as e:
        print(f"HTTP Error {e.code}: {e.reason}")
        print(e.read().decode('utf-8'))
        return 1
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    container_name = sys.argv[1] if len(sys.argv) > 1 else "containerd__containerd-4847"
    sys.exit(trigger_test(container_name=container_name))
'''
            
            # Write script to a temporary file in the container
            exec_result = container.exec_run(
                cmd=["bash", "-c", f"cat > /trigger_test.py << 'EOFSCRIPT'\n{script_content}\nEOFSCRIPT\nchmod +x /trigger_test.py"],
                stdout=True,
                stderr=True,
            )
            
            if exec_result.exit_code == 0:
                logger.info(f"Trigger script copied successfully to {task_name}")
                return True
            else:
                logger.error(f"Failed to copy trigger script: {exec_result.output.decode()}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to copy trigger script: {e}")
            return False
    
    def _generate_ssh_command(self, container: Container, task_name: str) -> str:
        """
        Generate SSH connection command for the container.
        
        Args:
            container: Docker container object
            task_name: Name of the container
        
        Returns:
            SSH command string
        """
        # Get the host IP (typically docker0 interface or host.docker.internal)
        host_ip = self._get_host_ip()
        
        # Get the mapped SSH port
        container.reload()
        ports = container.ports
        
        if "22/tcp" in ports and ports["22/tcp"]:
            ssh_port = ports["22/tcp"][0]["HostPort"]
            ssh_command = f"ssh -p {ssh_port} root@{host_ip}"
        else:
            # If no SSH port mapping, provide docker exec command
            ssh_command = f"docker exec -it {task_name} /bin/bash"
        
        return ssh_command
    
    def _get_host_ip(self) -> str:
        """
        Get the host IP address for SSH access.
        
        Returns:
            Host IP address as string
        """
        # Use localhost for SSH since ports are mapped to host
        return "localhost"
    
    def stop_container(self, container_name: str) -> bool:
        """
        Stop a running container.
        
        Args:
            container_name: Name of the container to stop
        
        Returns:
            True if successful, False otherwise
        """
        try:
            container = self.client.containers.get(container_name)
            container.stop()
            logger.info(f"Container {container_name} stopped")
            return True
        except docker.errors.NotFound:
            logger.warning(f"Container {container_name} not found")
            return False
        except Exception as e:
            logger.error(f"Failed to stop container {container_name}: {e}")
            return False
    
    def remove_container(self, container_name: str, force: bool = False) -> bool:
        """
        Remove a container.
        
        Args:
            container_name: Name of the container to remove
            force: Force removal even if running
        
        Returns:
            True if successful, False otherwise
        """
        try:
            container = self.client.containers.get(container_name)
            container.remove(force=force)
            logger.info(f"Container {container_name} removed")
            return True
        except docker.errors.NotFound:
            logger.warning(f"Container {container_name} not found")
            return False
        except Exception as e:
            logger.error(f"Failed to remove container {container_name}: {e}")
            return False
    
    def get_container_status(self, container_name: str) -> Optional[str]:
        """
        Get the status of a container.
        
        Args:
            container_name: Name of the container
        
        Returns:
            Container status string or None if not found
        """
        try:
            container = self.client.containers.get(container_name)
            return container.status
        except docker.errors.NotFound:
            return None
        except Exception as e:
            logger.error(f"Failed to get container status: {e}")
            return None
    
    def copy_to_container(
        self,
        container_name: str,
        src_path: str,
        dest_path: str,
    ) -> tuple[str, int]:
        """
        Copy files/directories from host to container.
        
        Args:
            container_name: Name of the container
            src_path: Source path on host (absolute path)
            dest_path: Destination path in container
        
        Returns:
            Tuple of (output, exit_code)
        """
        try:
            import subprocess
            container = self.client.containers.get(container_name)
            logger.info(f"Copying {src_path} to {container_name}:{dest_path}")
            
            # Use docker cp command
            result = subprocess.run(
                ["docker", "cp", src_path, f"{container_name}:{dest_path}"],
                capture_output=True,
                text=True,
            )
            
            if result.returncode == 0:
                logger.info(f"Successfully copied to {dest_path}")
                return result.stdout, 0
            else:
                logger.error(f"Failed to copy: {result.stderr}")
                return result.stderr, result.returncode
                
        except Exception as e:
            logger.error(f"Failed to copy files: {e}")
            return str(e), 1
    
    def exec_command_in_container(
        self,
        container_name: str,
        command: str,
        output_file: Optional[str] = None,
        timeout_sec: float = None,
    ) -> tuple[str, int, bool]:
        """
        Execute a command in a running container and capture output.
        
        Args:
            container_name: Name of the container
            command: Command to execute
            output_file: Optional file path to save output
            timeout_sec: Optional timeout in seconds
        
        Returns:
            Tuple of (output string, exit code, timeout_occurred)
        """
        timeout_occurred = False
        try:
            container = self.client.containers.get(container_name)
            logger.info(f"Executing command in {container_name}: {command}")
            
            # Execute with timeout if specified
            if timeout_sec:
                import time
                
                logger.info(f"Running command with {timeout_sec}s timeout...")
                
                # Create exec instance
                exec_instance = container.client.api.exec_create(
                    container.id,
                    ["bash", "-c", command]
                )
                exec_id = exec_instance['Id']
                
                # Start execution in background
                exec_stream = container.client.api.exec_start(exec_id, stream=True, demux=False)
                
                # Collect output with timeout
                start_time = time.time()
                output_chunks = []
                
                try:
                    for chunk in exec_stream:
                        if chunk:
                            output_chunks.append(chunk)
                        
                        # Check timeout
                        if time.time() - start_time > timeout_sec:
                            logger.warning(f"Command timeout after {timeout_sec}s, stopping...")
                            timeout_occurred = True
                            
                            # Try to kill the bash process
                            try:
                                container.exec_run(
                                    ["pkill", "-9", "-f", "bash.*run-tests.sh"],
                                    detach=False
                                )
                            except Exception as e:
                                logger.error(f"Failed to kill test process: {e}")
                            
                            break
                    
                    # Get final exit code
                    exec_info = container.client.api.exec_inspect(exec_id)
                    exit_code = exec_info.get('ExitCode', -1)
                    
                except Exception as e:
                    logger.error(f"Error during command execution: {e}")
                    exit_code = -1
                
                output = b''.join(output_chunks).decode('utf-8', errors='replace') if output_chunks else ""
                
                if timeout_occurred:
                    output += f"\n\n===== TIMEOUT: Test execution stopped after {timeout_sec} seconds ====="
                    logger.warning(f"Command timed out after {timeout_sec}s")
                else:
                    logger.info(f"Command completed with exit code: {exit_code}")
            else:
                # No timeout, use simple exec_run
                result = container.exec_run(
                    cmd=["bash", "-c", command],
                    stdout=True,
                    stderr=True,
                    stream=False,
                )
                
                output = result.output.decode('utf-8', errors='replace')
                exit_code = result.exit_code
                logger.info(f"Command completed with exit code: {exit_code}")
            
            # Save to file if requested
            if output_file:
                import os
                os.makedirs(os.path.dirname(output_file), exist_ok=True)
                with open(output_file, 'w') as f:
                    f.write(output)
                logger.info(f"Output saved to: {output_file}")
            
            return output, exit_code, timeout_occurred
            
        except docker.errors.NotFound:
            logger.error(f"Container {container_name} not found")
            raise RuntimeError(f"Container {container_name} not found")
        except Exception as e:
            logger.error(f"Failed to execute command: {e}")
            raise RuntimeError(f"Failed to execute command: {e}") from e
    
    def list_containers(self, all: bool = False) -> list[dict]:
        """
        List all containers.
        
        Args:
            all: Include stopped containers
        
        Returns:
            List of container information dicts
        """
        try:
            containers = self.client.containers.list(all=all)
            return [
                {
                    "id": c.id[:12],
                    "name": c.name,
                    "status": c.status,
                    "image": c.image.tags[0] if c.image.tags else c.image.id[:12],
                }
                for c in containers
            ]
        except Exception as e:
            logger.error(f"Failed to list containers: {e}")
            return []
