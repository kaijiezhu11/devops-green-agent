#!/usr/bin/env python3
"""
Start containerd__containerd-4847 benchmark task container
"""
import asyncio
import json
import os
import sys
import yaml
import docker
from pathlib import Path
from src.messenger import send_message


def load_task_instruction(task_dir: str) -> str:
    """Load instruction from task.yaml"""
    task_yaml_path = Path(task_dir) / "task.yaml"
    
    if not task_yaml_path.exists():
        raise FileNotFoundError(f"task.yaml not found at {task_yaml_path}")
    
    with open(task_yaml_path, 'r') as f:
        task_data = yaml.safe_load(f)
    
    instruction = task_data.get('instruction', '')
    if not instruction:
        raise ValueError("No instruction found in task.yaml")
    
    return instruction.strip()


async def start_containerd_task():
    """Start containerd benchmark task container"""
    
    print("=" * 60)
    print("  Starting containerd__containerd-4847 Task Container")
    print("=" * 60 + "\n")
    
    # Check for mode selection
    # Environment variable USE_SOLUTION=true or command line argument --solution
    use_solution = os.getenv("USE_SOLUTION", "").lower() == "true" or "--solution" in sys.argv
    
    if use_solution:
        print("🔧 Mode: Using solution.sh (pre-built solution)")
        print("   Will skip Claude Code and run solution.sh instead\n")
    else:
        print("🤖 Mode: Using Claude Code (AI solver)")
        print("   Set USE_SOLUTION=true or use --solution flag to use solution.sh instead\n")
    
    # Check for ANTHROPIC_API_KEY (only needed for Claude Code mode)
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not use_solution:
        if api_key:
            print(f"✓ Found ANTHROPIC_API_KEY: {api_key[:8]}...{api_key[-4:]}")
        else:
            print("⚠ ANTHROPIC_API_KEY not set - Claude Code will not run")
            print("  To use Claude Code, run: export ANTHROPIC_API_KEY=your_key_here")
            print("  Or use solution mode: USE_SOLUTION=true or --solution flag\n")
    
    # Load task instruction (only needed for Claude Code mode)
    task_dir = "containerd__containerd-4847"
    instruction = None
    if not use_solution:
        try:
            instruction = load_task_instruction(task_dir)
            print(f"✓ Loaded task instruction ({len(instruction)} chars)\n")
        except Exception as e:
            print(f"⚠ Could not load task instruction: {e}")
            instruction = None
    
    # Request format - build from local Dockerfile
    request = {
        "participants": {},
        "config": {
            "task_config": {
                "task_name": "containerd__containerd-4847",
                "image": "containerd__containerd-4847:local",
                "command": "sleep infinity",
                "ports": {"22/tcp": None},
                "environment": {
                    "TERM": "xterm-256color"
                },
                "build_context": "containerd__containerd-4847",
                "dockerfile": "Dockerfile"
            }
        }
    }
    
    print("Sending request to Green Agent...")
    print(f"Task name: {request['config']['task_config']['task_name']}")
    print(f"Image tag: {request['config']['task_config']['image']}")
    print(f"Build path: {request['config']['task_config']['build_context']}\n")
    
    # Retry logic for initial connection
    max_retries = 3
    retry_delay = 2
    
    for attempt in range(max_retries):
        try:
            if attempt > 0:
                print(f"Retrying... (attempt {attempt + 1}/{max_retries})")
            
            result = await send_message(
                message=json.dumps(request),
                base_url="http://localhost:9009",
                timeout=120
            )
            break  # Success, exit retry loop
            
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"⚠ Connection failed, waiting {retry_delay}s before retry...")
                await asyncio.sleep(retry_delay)
                continue
            else:
                # Last attempt failed, raise the error
                raise
    
    try:
        
        print("Container started successfully!\n")
        print("=" * 60)
        print(result["response"])
        print("=" * 60 + "\n")
        
        # Execute solution based on mode
        if use_solution:
            # Solution mode - run solution.sh
            print("\n" + "=" * 60)
            print("  Running Pre-built Solution")
            print("=" * 60 + "\n")
            
            try:
                # Connect to Docker
                client = docker.from_env()
                container = client.containers.get("containerd__containerd-4847")
                
                # Import docker_manager
                from src.docker_manager import DockerManager
                docker_mgr = DockerManager()
                
                # Copy solution.sh to container
                print("Copying solution.sh to container...")
                copy_output, copy_exit = docker_mgr.copy_to_container(
                    container_name="containerd__containerd-4847",
                    src_path=f"{task_dir}/solution.sh",
                    dest_path="/solution.sh"
                )
                if copy_exit == 0:
                    print("✓ Solution script copied successfully\n")
                else:
                    print(f"⚠ Failed to copy solution script: {copy_output}\n")
                
                # Run solution.sh
                print("Executing solution.sh (this may take a while)...\n")
                output, exit_code = docker_mgr.exec_command_in_container(
                    container_name="containerd__containerd-4847",
                    command="bash /solution.sh"
                )
                
                print("\n" + "=" * 60)
                print("  Solution Output")
                print("=" * 60)
                print(output)
                print("=" * 60)
                print(f"Exit code: {exit_code}\n")
                
            except Exception as e:
                print(f"\n⚠ Failed to run solution: {e}")
                import traceback
                traceback.print_exc()
                output = str(e)
                exit_code = 1
        
        elif api_key and instruction:
            # Claude Code mode
            print("\n" + "=" * 60)
            print("  Installing Claude Code and Running Task")
            print("=" * 60 + "\n")
            
            try:
                # Connect to Docker
                client = docker.from_env()
                container = client.containers.get("containerd__containerd-4847")
                
                # Import docker_manager
                from src.docker_manager import DockerManager
                docker_mgr = DockerManager()
                
                # Install and run Claude Code
                print("This may take a few minutes (installing Node.js and Claude Code)...\n")
                output, exit_code = docker_mgr.install_claude_code_in_container(
                    container=container,
                    api_key=api_key,
                    prompt=instruction
                )
                
                print("\n" + "=" * 60)
                print("  Claude Code Output")
                print("=" * 60)
                print(output)
                print("=" * 60)
                print(f"Exit code: {exit_code}\n")
                
            except Exception as e:
                print(f"\n⚠ Failed to run Claude Code: {e}")
                import traceback
                traceback.print_exc()
                output = str(e)
                exit_code = 1
        else:
            # No solution mode selected and no API key
            output = "No solution executed"
            exit_code = -1
        
        # Automatically trigger tests after solution completes (for both modes)
        if use_solution or (api_key and instruction):
            print("\n" + "=" * 60)
            print("  Triggering Tests Automatically")
            print("=" * 60 + "\n")
            
            try:
                # Import docker_manager if not already imported
                from src.docker_manager import DockerManager
                docker_mgr = DockerManager()
                
                # Copy tests directory to container
                print("Copying test files to container...")
                copy_output, copy_exit = docker_mgr.copy_to_container(
                    container_name="containerd__containerd-4847",
                    src_path=f"{task_dir}/tests",
                    dest_path="/tests"
                )
                if copy_exit == 0:
                    print("✓ Tests copied successfully")
                else:
                    print(f"⚠ Failed to copy tests: {copy_output}")
                
                # Copy test script
                print("Copying test script to container...")
                copy_output, copy_exit = docker_mgr.copy_to_container(
                    container_name="containerd__containerd-4847",
                    src_path=f"{task_dir}/run-tests.sh",
                    dest_path="/run-tests.sh"
                )
                if copy_exit == 0:
                    print("✓ Test script copied successfully")
                else:
                    print(f"⚠ Failed to copy test script: {copy_output}")
                
                # Run tests
                print("\nRunning tests (this may take a while)...")
                test_output, test_exit = docker_mgr.exec_command_in_container(
                    container_name="containerd__containerd-4847",
                    command="bash /run-tests.sh"
                )
                
                # Save test log to host
                from datetime import datetime
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                log_filename = f"/scr/yuan/devops-greeen-agent/test_log_containerd__containerd-4847_{timestamp}.txt"
                
                mode_name = "Solution" if use_solution else "Claude Code"
                with open(log_filename, 'w') as f:
                    f.write(f"Mode: {mode_name}\n")
                    f.write(f"{mode_name} Exit Code: {exit_code}\n")
                    f.write(f"Test Exit Code: {test_exit}\n")
                    f.write("=" * 60 + "\n")
                    f.write(f"{mode_name} Output:\n")
                    f.write("=" * 60 + "\n")
                    f.write(output + "\n\n")
                    f.write("=" * 60 + "\n")
                    f.write("Test Output:\n")
                    f.write("=" * 60 + "\n")
                    f.write(test_output)
                
                print(f"\n✓ Test log saved to: {log_filename}")
                
                # Print test summary
                print("\n" + "=" * 60)
                print("  Test Results Summary")
                print("=" * 60)
                
                # Try to extract pass/fail counts from output
                if "PASSED" in test_output or "FAILED" in test_output:
                    # Show last 50 lines which usually contain summary
                    summary_lines = test_output.split('\n')[-50:]
                    for line in summary_lines:
                        if any(keyword in line for keyword in ["PASSED", "FAILED", "ERROR", "passed", "failed", "error"]):
                            print(line)
                else:
                    # Show last 20 lines as summary
                    summary_lines = test_output.split('\n')[-20:]
                    print('\n'.join(summary_lines))
                
                print("=" * 60)
                print(f"Test exit code: {test_exit}")
                if test_exit == 0:
                    print("✓ All tests passed!")
                else:
                    print("✗ Some tests failed")
                print("=" * 60 + "\n")
                
            except Exception as e:
                print(f"\n⚠ Failed to run tests: {e}")
                import traceback
                traceback.print_exc()
        
        print("\nNext steps:")
        print("1. SSH into container (use ssh_command above)")
        print("2. (Optional) Apply solution: bash /solution.sh")
        print("3. Trigger test: python3 /trigger_test.py")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(start_containerd_task())
