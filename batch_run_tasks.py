#!/usr/bin/env python3
"""
Batch run multiple tasks with concurrency control
"""
import asyncio
import json
import os
import sys
import yaml
import docker
import argparse
import re
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional
from enum import Enum
from src.messenger import send_message
from src.docker_manager import DockerManager


class UnitTestStatus(Enum):
    PASSED = "passed"
    FAILED = "failed"


class SWEBenchParser:
    """Parser for SWEBench format test output"""
    START_MARKER = "SWEBench results starts here"
    END_MARKER = "SWEBench results ends here"
    
    def parse(self, content: str) -> Dict[str, UnitTestStatus]:
        if self.START_MARKER not in content or self.END_MARKER not in content:
            raise ValueError("Couldn't find SWEBench results between markers")
        
        content = content.split(self.START_MARKER, 1)[-1]
        content = content.rsplit(self.END_MARKER, 1)[0]
        block = content.strip()
        
        results = {}
        if block == "PASSED":
            results["tests"] = UnitTestStatus.PASSED
        else:
            results["tests"] = UnitTestStatus.FAILED
        return results


class PytestParser:
    """Parser for pytest format test output"""
    SHORT_TEST_SUMMARY_INFO_PATTERN = r"=+\s*short test summary info\s*=+"
    
    def parse(self, content: str) -> Dict[str, UnitTestStatus]:
        parts = re.split(
            pattern=self.SHORT_TEST_SUMMARY_INFO_PATTERN,
            string=content,
            flags=re.IGNORECASE,
            maxsplit=1,
        )
        
        if len(parts) < 2:
            raise ValueError("No short test summary info found")
        
        short_test_summary = parts[1]
        results = {}
        
        for line in short_test_summary.splitlines():
            line = line.strip()
            if line.startswith("PASSED"):
                parts = line.split(maxsplit=1)
                if len(parts) > 1:
                    test_name = parts[1].strip()
                    results[test_name] = UnitTestStatus.PASSED
            elif line.startswith("FAILED"):
                parts = line.split(maxsplit=1)
                if len(parts) > 1:
                    test_name = parts[1].strip()
                    results[test_name] = UnitTestStatus.FAILED
        
        return results


class TaskRunner:
    """Handle individual task execution"""
    
    def __init__(self, task_dir: Path, output_dir: Path, use_solution: bool = False, no_run: bool = False, rebuild: bool = False):
        self.task_dir = task_dir
        self.task_name = task_dir.name
        self.output_dir = output_dir / self.task_name
        self.use_solution = use_solution
        self.no_run = no_run
        self.rebuild = rebuild
        self.agent_log = []
        self.test_output = ""
        self.status = "pending"
        self.container_name = None
        self.parser_name = None
        self.max_agent_timeout_sec = None
        self.max_test_timeout_sec = None
        self.agent_timeout_occurred = False
        self.test_timeout_occurred = False
        
    def log_agent_activity(self, message: str):
        """Log Claude Code activity"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] {message}"
        self.agent_log.append(log_entry)
        print(f"  [{self.task_name}] {message}")
    
    def parse_test_output(self, output: str) -> Dict[str, any]:
        """Parse test output based on parser_name"""
        parser_name = self.parser_name or 'swebench'
        
        try:
            if parser_name == 'pytest':
                parser = PytestParser()
            else:  # default to swebench
                parser = SWEBenchParser()
            
            results = parser.parse(output)
            
            # Calculate summary
            passed = sum(1 for status in results.values() if status == UnitTestStatus.PASSED)
            failed = sum(1 for status in results.values() if status == UnitTestStatus.FAILED)
            total = passed + failed
            
            return {
                'parser': parser_name,
                'total': total,
                'passed': passed,
                'failed': failed,
                'results': {k: v.value for k, v in results.items()}
            }
        except Exception as e:
            self.log_agent_activity(f"Failed to parse test output: {e}")
            return {
                'parser': parser_name,
                'error': str(e),
                'total': 0,
                'passed': 0,
                'failed': 0,
                'results': {}
            }
    
    async def run(self) -> Dict:
        """Execute the task and return results"""
        self.log_agent_activity(f"Starting task execution (mode: {'solution' if self.use_solution else 'claude-code'})")
        start_time = datetime.now()
        
        try:
            # Create output directory
            self.output_dir.mkdir(parents=True, exist_ok=True)
            
            # Load task instruction, parser name, and timeout settings
            instruction = None
            task_yaml_path = self.task_dir / "task.yaml"
            try:
                with open(task_yaml_path, 'r') as f:
                    task_data = yaml.safe_load(f)
                
                # Load timeout settings (always read these, even for solution mode)
                self.max_agent_timeout_sec = task_data.get('max_agent_timeout_sec')
                self.max_test_timeout_sec = task_data.get('max_test_timeout_sec')
                
                if not self.use_solution:
                    instruction = task_data.get('instruction', '').strip()
                    self.parser_name = task_data.get('parser_name', 'swebench').strip()
                    self.log_agent_activity(f"Loaded task instruction ({len(instruction)} chars)")
                else:
                    self.parser_name = task_data.get('parser_name', 'swebench').strip()
                
                if self.max_agent_timeout_sec:
                    self.log_agent_activity(f"Agent timeout: {self.max_agent_timeout_sec}s")
                if self.max_test_timeout_sec:
                    self.log_agent_activity(f"Test timeout: {self.max_test_timeout_sec}s")
            except Exception as e:
                self.log_agent_activity(f"Failed to load task.yaml: {e}")
            
            # Send request to Green Agent
            # Convert host path to Green Agent container path
            # Host: /scr/yuan/devops-greeen-agent/dataset_subset/task_name
            # Container: /workspace/dataset_subset/task_name
            green_agent_path = f"dataset_subset/{self.task_name}"
            
            request = {
                "participants": {},
                "config": {
                    "task_config": {
                        "task_name": self.task_name,
                        "image": f"{self.task_name}:local",
                        "command": "sleep infinity",
                        "ports": {"22/tcp": None},
                        "environment": {"TERM": "xterm-256color"},
                        "build_context": green_agent_path,
                        "dockerfile": "Dockerfile",
                        "nocache": self.rebuild
                    }
                }
            }
            
            self.log_agent_activity("Sending request to Green Agent...")
            
            # Retry logic
            max_retries = 3
            retry_delay = 2
            
            for attempt in range(max_retries):
                try:
                    if attempt > 0:
                        self.log_agent_activity(f"Retrying... (attempt {attempt + 1}/{max_retries})")
                    
                    result = await send_message(
                        message=json.dumps(request),
                        base_url="http://localhost:9009",
                        timeout=120
                    )
                    break
                    
                except Exception as e:
                    if attempt < max_retries - 1:
                        self.log_agent_activity(f"Connection failed, waiting {retry_delay}s...")
                        await asyncio.sleep(retry_delay)
                        continue
                    else:
                        raise
            
            self.log_agent_activity("Container started successfully")
            self.container_name = self.task_name
            
            # Get SSH command for the container
            try:
                client = docker.from_env()
                container = client.containers.get(self.task_name)
                docker_mgr = DockerManager()
                ssh_command = docker_mgr._generate_ssh_command(container, self.task_name)
                self.ssh_command = ssh_command
                self.log_agent_activity(f"SSH: {ssh_command}")
            except Exception as e:
                self.ssh_command = f"docker exec -it {self.task_name} /bin/bash"
                self.log_agent_activity(f"SSH: {self.ssh_command}")
            
            # If no_run mode, stop here and return container info
            if self.no_run:
                try:
                    client = docker.from_env()
                    container = client.containers.get(self.task_name)
                    docker_mgr = DockerManager()
                    
                    # Copy tests even in no-run mode
                    self.log_agent_activity("Copying test files...")
                    docker_mgr.copy_to_container(
                        container_name=self.task_name,
                        src_path=str(self.task_dir / "tests"),
                        dest_path="/tests"
                    )
                    self.log_agent_activity("Tests copied")
                    
                    docker_mgr.copy_to_container(
                        container_name=self.task_name,
                        src_path=str(self.task_dir / "run-tests.sh"),
                        dest_path="/run-tests.sh"
                    )
                    self.log_agent_activity("Test script copied")
                    
                    # Get WORKDIR from container
                    workdir = docker_mgr._get_container_workdir(container)
                    
                    # Install Claude Code in no-run mode
                    self.log_agent_activity("Installing Claude Code...")
                    install_script = """set -e
apt-get update
apt-get install -y curl

curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.2/install.sh | bash

source "$HOME/.nvm/nvm.sh"

nvm install 22
npm -v

npm install -g @anthropic-ai/claude-code@latest"""
                    
                    exit_code, output = container.exec_run(
                        ["/bin/bash", "-c", install_script],
                        stream=False,
                        demux=False
                    )
                    
                    if exit_code == 0:
                        self.log_agent_activity("Claude Code installed successfully")
                    else:
                        self.log_agent_activity(f"Claude Code installation failed (exit: {exit_code})")
                    
                    # Generate Claude Code run command with actual instruction
                    import shlex
                    api_key = os.getenv("ANTHROPIC_API_KEY", "YOUR_API_KEY_HERE")
                    escaped_prompt = shlex.quote(instruction) if instruction else '"<no instruction loaded>"'
                    
                    run_script = f"""export NVM_DIR="$HOME/.nvm"
source "$NVM_DIR/nvm.sh"
export ANTHROPIC_API_KEY={shlex.quote(api_key)}
export FORCE_AUTO_BACKGROUND_TASKS=1
export ENABLE_BACKGROUND_TASKS=1
cd {workdir}
claude --verbose --output-format stream-json -p {escaped_prompt} --allowedTools Bash Edit Write Read Glob Grep LS WebFetch NotebookEdit NotebookRead TodoRead TodoWrite Agent"""
                    
                    # Print commands for user
                    print(f"\n{'='*60}")
                    print(f"Container: {self.task_name}")
                    print(f"{'='*60}\n")
                    print(f"🔗 SSH Connection:")
                    print(f"{self.ssh_command}\n")
                    print(f"✅ Claude Code installed")
                    print(f"\n📝 To run Claude Code manually:")
                    print(f"\n{run_script}\n")
                    print(f"{'='*60}\n")
                    
                    duration = (datetime.now() - start_time).total_seconds()
                    self.status = "ready"
                    
                    return {
                        "task_name": self.task_name,
                        "container_name": self.container_name,
                        "status": "ready",
                        "duration": duration,
                        "ssh_command": self.ssh_command,
                        "message": "Container created and ready for manual testing",
                        "install_script": install_script,
                        "run_script": run_script,
                        "workdir": workdir
                    }
                except Exception as e:
                    self.log_agent_activity(f"Error in no-run mode: {e}")
                    self.status = "error"
                    return {
                        "task_name": self.task_name,
                        "container_name": self.container_name,
                        "status": "error",
                        "duration": (datetime.now() - start_time).total_seconds(),
                        "error": str(e)
                    }
            
            # Execute solution
            solution_output = ""
            solution_exit_code = 0
            
            try:
                client = docker.from_env()
                container = client.containers.get(self.task_name)
                docker_mgr = DockerManager()
                
                if self.use_solution:
                    # Solution mode
                    self.log_agent_activity("Running solution.sh...")
                    
                    # Copy solution
                    copy_output, copy_exit = docker_mgr.copy_to_container(
                        container_name=self.task_name,
                        src_path=str(self.task_dir / "solution.sh"),
                        dest_path="/solution.sh"
                    )
                    
                    if copy_exit == 0:
                        self.log_agent_activity("Solution script copied")
                    else:
                        self.log_agent_activity(f"Failed to copy solution: {copy_output}")
                    
                    # Execute solution
                    solution_output, solution_exit_code = docker_mgr.exec_command_in_container(
                        container_name=self.task_name,
                        command="bash /solution.sh"
                    )
                    self.log_agent_activity(f"Solution completed (exit: {solution_exit_code})")
                    
                else:
                    # Claude Code mode
                    api_key = os.getenv("ANTHROPIC_API_KEY")
                    if not api_key or not instruction:
                        self.log_agent_activity("Skipping Claude Code (no API key or instruction)")
                        solution_output = "No API key or instruction available"
                        solution_exit_code = -1
                    else:
                        self.log_agent_activity("Installing Claude Code...")
                        solution_output, solution_exit_code, agent_timeout = docker_mgr.install_claude_code_in_container(
                            container=container,
                            api_key=api_key,
                            prompt=instruction,
                            timeout_sec=self.max_agent_timeout_sec
                        )
                        
                        if agent_timeout:
                            self.agent_timeout_occurred = True
                            self.log_agent_activity(f"⏱️  AGENT TIMEOUT after {self.max_agent_timeout_sec}s")
                        
                        self.log_agent_activity(f"Claude Code completed (exit: {solution_exit_code})")
                
                # Run tests
                self.log_agent_activity("Copying test files...")
                
                # Copy tests
                copy_output, copy_exit = docker_mgr.copy_to_container(
                    container_name=self.task_name,
                    src_path=str(self.task_dir / "tests"),
                    dest_path="/tests"
                )
                
                if copy_exit == 0:
                    self.log_agent_activity("Tests copied successfully")
                
                # Copy test script
                copy_output, copy_exit = docker_mgr.copy_to_container(
                    container_name=self.task_name,
                    src_path=str(self.task_dir / "run-tests.sh"),
                    dest_path="/run-tests.sh"
                )
                
                if copy_exit == 0:
                    self.log_agent_activity("Test script copied successfully")
                
                # Execute tests
                self.log_agent_activity("Running tests...")
                test_output, test_exit, test_timeout = docker_mgr.exec_command_in_container(
                    container_name=self.task_name,
                    command="bash /run-tests.sh",
                    timeout_sec=self.max_test_timeout_sec
                )
                
                if test_timeout:
                    self.test_timeout_occurred = True
                    self.log_agent_activity(f"⏱️  TEST TIMEOUT after {self.max_test_timeout_sec}s")
                
                self.test_output = test_output
                self.log_agent_activity(f"Tests completed (exit: {test_exit})")
                
                # Determine pass/fail
                # Check for explicit FAILED marker in test output (SWEBench format)
                if "SWEBench results starts here" in test_output:
                    # Extract result between markers
                    lines = test_output.split('\n')
                    for i, line in enumerate(lines):
                        if "SWEBench results starts here" in line:
                            if i + 1 < len(lines):
                                result_line = lines[i + 1].strip()
                                passed = result_line == "PASSED"
                                break
                    else:
                        passed = test_exit == 0
                else:
                    # Fallback to exit code for non-SWEBench tests
                    passed = test_exit == 0 or "PASSED" in test_output
                
                self.status = "passed" if passed else "failed"
                
                # Save outputs
                self.log_agent_activity("Saving results...")
                
                # Save agent log
                with open(self.output_dir / "agent.log", 'w') as f:
                    f.write('\n'.join(self.agent_log) + '\n')
                    f.write('\n')
                    f.write('=' * 60 + '\n')
                    f.write('Solution/Claude Output:\n')
                    f.write('=' * 60 + '\n')
                    f.write(solution_output)
                
                # Parse test output
                test_results = self.parse_test_output(test_output)
                
                # Save test output
                with open(self.output_dir / "test_output.log", 'w') as f:
                    f.write(f"Mode: {'Solution' if self.use_solution else 'Claude Code'}\n")
                    f.write(f"Solution Exit Code: {solution_exit_code}\n")
                    f.write(f"Test Exit Code: {test_exit}\n")
                    f.write(f"Status: {self.status.upper()}\n")
                    f.write(f"Parser: {test_results['parser']}\n")
                    if 'error' not in test_results:
                        f.write(f"Tests Passed: {test_results['passed']}/{test_results['total']}\n")
                    
                    # Record timeout information
                    if self.agent_timeout_occurred:
                        f.write(f"⏱️  AGENT TIMEOUT: YES (limit: {self.max_agent_timeout_sec}s)\n")
                    if self.test_timeout_occurred:
                        f.write(f"⏱️  TEST TIMEOUT: YES (limit: {self.max_test_timeout_sec}s)\n")
                    
                    f.write('=' * 60 + '\n')
                    f.write('Solution/Claude Output:\n')
                    f.write('=' * 60 + '\n')
                    f.write(solution_output + '\n\n')
                    f.write('=' * 60 + '\n')
                    f.write('Test Output:\n')
                    f.write('=' * 60 + '\n')
                    f.write(test_output)
                
                # Save test results as JSON
                with open(self.output_dir / "test_results.json", 'w') as f:
                    json.dump(test_results, f, indent=2)
                
                end_time = datetime.now()
                duration = (end_time - start_time).total_seconds()
                
                # Log parsed results
                if 'error' in test_results:
                    self.log_agent_activity(f"Task completed: {self.status.upper()} (duration: {duration:.1f}s, parse error)")
                else:
                    self.log_agent_activity(f"Task completed: {self.status.upper()} (duration: {duration:.1f}s, tests: {test_results['passed']}/{test_results['total']} passed)")
                
                return {
                    "task_name": self.task_name,
                    "status": self.status,
                    "duration": duration,
                    "ssh_command": self.ssh_command,
                    "solution_exit_code": solution_exit_code,
                    "test_exit_code": test_exit,
                    "output_dir": str(self.output_dir),
                    "test_results": test_results,
                    "agent_timeout": self.agent_timeout_occurred,
                    "test_timeout": self.test_timeout_occurred
                }
                
            except Exception as e:
                self.log_agent_activity(f"Error during execution: {e}")
                self.status = "error"
                raise
                
        except Exception as e:
            self.log_agent_activity(f"Task failed: {e}")
            self.status = "error"
            
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            
            return {
                "task_name": self.task_name,
                "status": "error",
                "duration": duration,
                "error": str(e),
                "output_dir": str(self.output_dir)
            }


async def run_tasks_batch(
    dataset_dir: Path,
    n_concurrent: int,
    use_solution: bool,
    task_filter: Optional[List[str]] = None,
    no_run: bool = False,
    rebuild: bool = False
):
    """Run multiple tasks with concurrency control"""
    
    # Create run directory
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path("runs") / run_timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    
    print("=" * 60)
    print(f"  Batch Task Execution - {run_timestamp}")
    print("=" * 60)
    print(f"Dataset: {dataset_dir}")
    print(f"Concurrency: {n_concurrent}")
    mode_str = "Container Setup Only" if no_run else ('Solution' if use_solution else 'Claude Code')
    print(f"Mode: {mode_str}")
    if task_filter:
        print(f"Filter: {', '.join(task_filter)}")
    print("=" * 60 + "\n")
    
    # Find all tasks
    all_tasks = [d for d in dataset_dir.iterdir() if d.is_dir() and (d / "task.yaml").exists()]
    
    # Filter tasks if specified
    if task_filter:
        all_tasks = [t for t in all_tasks if t.name in task_filter]
    
    print(f"Found {len(all_tasks)} tasks to run:\n")
    for task in all_tasks:
        print(f"  - {task.name}")
    print()
    
    # Save run configuration
    config = {
        "timestamp": run_timestamp,
        "dataset_dir": str(dataset_dir),
        "n_concurrent": n_concurrent,
        "use_solution": use_solution,
        "task_filter": task_filter,
        "total_tasks": len(all_tasks),
        "tasks": [t.name for t in all_tasks]
    }
    
    with open(run_dir / "config.json", 'w') as f:
        json.dump(config, f, indent=2)
    
    # Run tasks with concurrency control
    semaphore = asyncio.Semaphore(n_concurrent)
    
    async def run_with_semaphore(task_dir: Path):
        async with semaphore:
            runner = TaskRunner(task_dir, run_dir, use_solution, no_run, rebuild)
            return await runner.run()
    
    # Execute all tasks
    print(f"\nStarting execution with {n_concurrent} concurrent tasks...\n")
    start_time = datetime.now()
    
    results = await asyncio.gather(*[run_with_semaphore(task) for task in all_tasks], return_exceptions=True)
    
    end_time = datetime.now()
    total_duration = (end_time - start_time).total_seconds()
    
    # Process results
    successful_results = []
    for result in results:
        if isinstance(result, Exception):
            print(f"Task failed with exception: {result}")
        else:
            successful_results.append(result)
    
    # Generate summary
    if no_run:
        ready = sum(1 for r in successful_results if r["status"] == "ready")
        errors = sum(1 for r in successful_results if r["status"] == "error")
        passed = 0
        failed = 0
    else:
        passed = sum(1 for r in successful_results if r["status"] == "passed")
        failed = sum(1 for r in successful_results if r["status"] == "failed")
        errors = sum(1 for r in successful_results if r["status"] == "error")
        ready = 0
    
    summary = {
        "run_id": run_timestamp,
        "total_tasks": len(all_tasks),
        "completed": len(successful_results),
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "total_duration": total_duration,
        "results": successful_results
    }
    
    with open(run_dir / "summary.json", 'w') as f:
        json.dump(summary, f, indent=2)
    
    # Print summary
    print("\n" + "=" * 60)
    print("  Execution Summary")
    print("=" * 60)
    print(f"Total tasks: {len(all_tasks)}")
    if no_run:
        print(f"Ready: {ready}")
        print(f"Errors: {errors}")
        print(f"Total duration: {total_duration:.1f}s")
        print(f"Results saved to: {run_dir}")
        print("=" * 60 + "\n")
        
        # Print container names
        print("Created Containers:")
        for result in successful_results:
            if result["status"] == "ready":
                print(f"  ✓ {result['container_name']}")
        print()
    else:
        print(f"Passed: {passed}")
        print(f"Failed: {failed}")
        print(f"Errors: {errors}")
        print(f"Total duration: {total_duration:.1f}s")
        print(f"Results saved to: {run_dir}")
        print("=" * 60 + "\n")
        
        # Print individual results
        print("Task Results:")
        for result in successful_results:
            status_symbol = "✓" if result["status"] == "passed" else "✗"
            ssh = result.get('ssh_command', 'N/A')
            test_results = result.get('test_results', {})
            
            # Print basic result
            print(f"  {status_symbol} {result['task_name']}: {result['status'].upper()} ({result['duration']:.1f}s)")
            
            # Print test results if available
            if test_results and 'error' not in test_results:
                passed = test_results.get('passed', 0)
                total = test_results.get('total', 0)
                parser = test_results.get('parser', 'unknown')
                print(f"      Tests: {passed}/{total} passed (parser: {parser})")
            elif test_results and 'error' in test_results:
                print(f"      Parse error: {test_results['error']}")
            
            # Print timeout information
            if result.get('agent_timeout'):
                print(f"      ⏱️  Agent timeout occurred")
            if result.get('test_timeout'):
                print(f"      ⏱️  Test timeout occurred")
            
            # Print SSH command
            if ssh != 'N/A':
                print(f"      SSH: {ssh}")
        print()
        
        # Print accuracy summary
        total_tests = 0
        total_passed = 0
        tasks_with_results = 0
        
        for result in successful_results:
            test_results = result.get('test_results', {})
            if test_results and 'error' not in test_results:
                total_tests += test_results.get('total', 0)
                total_passed += test_results.get('passed', 0)
                tasks_with_results += 1
        
        if tasks_with_results > 0:
            accuracy = (total_passed / total_tests * 100) if total_tests > 0 else 0
            print(f"Overall Test Accuracy: {total_passed}/{total_tests} ({accuracy:.1f}%)")
            print(f"Tasks with parsed results: {tasks_with_results}/{len(successful_results)}")
            print()
    
    return summary


def main():
    parser = argparse.ArgumentParser(description="Batch run multiple tasks")
    parser.add_argument("dataset_dir", type=str, help="Directory containing task folders")
    parser.add_argument("--n-concurrent", type=int, default=4, help="Number of concurrent tasks (default: 4)")
    parser.add_argument("--solution", action="store_true", help="Use solution.sh instead of Claude Code")
    parser.add_argument("--no-run", action="store_true", help="Only create containers and copy test files, don't run Claude Code or tests")
    parser.add_argument("--rebuild", action="store_true", help="Force rebuild images without using cache (--no-cache)")
    parser.add_argument("-t", "--task", action="append", dest="tasks", help="Specific tasks to run (can be repeated)")
    
    args = parser.parse_args()
    
    dataset_dir = Path(args.dataset_dir)
    if not dataset_dir.exists():
        print(f"Error: Dataset directory not found: {dataset_dir}")
        sys.exit(1)
    
    # Check for API key if not using solution
    if not args.solution:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            print("Warning: ANTHROPIC_API_KEY not set. Tasks will skip Claude Code execution.")
            print("Set the API key or use --solution flag to run solutions.")
            print()
    
    # Run tasks
    asyncio.run(run_tasks_batch(
        dataset_dir=dataset_dir,
        n_concurrent=args.n_concurrent,
        use_solution=args.solution,
        task_filter=args.tasks,
        no_run=args.no_run,
        rebuild=args.rebuild
    ))


if __name__ == "__main__":
    main()
