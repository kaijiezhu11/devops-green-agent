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
# Set default directory to /home/containerd when SSH login
echo 'cd /home/containerd 2>/dev/null || true' >> /root/.bashrc
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
                logger.info(f"SSH configured successfully in {container.name}")
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
    ) -> str:
        """
        Build a Docker image from a Dockerfile.
        
        Args:
            path: Build context path (relative to /workspace in container)
            tag: Tag for the built image
            dockerfile: Dockerfile name (default: "Dockerfile")
        
        Returns:
            Image ID
        """
        try:
            # Build path is relative to the mounted workspace
            build_path = f"/workspace/{path}" if not path.startswith("/workspace") else path
            
            logger.info(f"Building image from {build_path}/{dockerfile}")
            logger.info(f"Tag: {tag}")
            
            # Build the image
            image, build_logs = self.client.images.build(
                path=build_path,
                dockerfile=dockerfile,
                tag=tag,
                rm=True,  # Remove intermediate containers
                forcerm=True,  # Always remove intermediate containers
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
        
        Returns:
            Tuple of (Container object, SSH command string)
        """
        try:
            # If build_context is provided, build the image first
            if build_context:
                logger.info(f"Building image from local Dockerfile: {build_context}")
                self.build_image(
                    path=build_context,
                    tag=image,  # Use image as tag name
                    dockerfile=dockerfile or "Dockerfile",
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
            
            # Generate SSH connection command
            ssh_command = self._generate_ssh_command(container, task_name)
            
            logger.info(f"Container {task_name} started successfully")
            return container, ssh_command
            
        except docker.errors.APIError as e:
            logger.error(f"Docker API error: {e}")
            raise RuntimeError(f"Failed to start container: {e}") from e
    
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
    ) -> bool:
        """
        Copy files/directories from host to container.
        
        Args:
            container_name: Name of the container
            src_path: Source path on host (absolute path)
            dest_path: Destination path in container
        
        Returns:
            True if successful, False otherwise
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
                return True
            else:
                logger.error(f"Failed to copy: {result.stderr}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to copy files: {e}")
            return False
    
    def exec_command_in_container(
        self,
        container_name: str,
        command: str,
        output_file: Optional[str] = None,
    ) -> tuple[str, int]:
        """
        Execute a command in a running container and capture output.
        
        Args:
            container_name: Name of the container
            command: Command to execute
            output_file: Optional file path to save output
        
        Returns:
            Tuple of (output string, exit code)
        """
        try:
            container = self.client.containers.get(container_name)
            logger.info(f"Executing command in {container_name}: {command}")
            
            # Execute command
            result = container.exec_run(
                cmd=["bash", "-c", command],
                stdout=True,
                stderr=True,
                stream=False,
            )
            
            output = result.output.decode('utf-8', errors='replace')
            exit_code = result.exit_code
            
            # Save to file if requested
            if output_file:
                import os
                os.makedirs(os.path.dirname(output_file), exist_ok=True)
                with open(output_file, 'w') as f:
                    f.write(output)
                logger.info(f"Output saved to: {output_file}")
            
            logger.info(f"Command completed with exit code: {exit_code}")
            return output, exit_code
            
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
