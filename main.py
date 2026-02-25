"""CLI entry point for DevOps Green Agent."""

import typer
import asyncio
from pathlib import Path

from src.green_agent import start_green_agent
from src.purple_agent import start_purple_agent, start_oracle_purple_agent, start_claude_code_purple_agent
from src.launcher import launch_evaluation
from src.launcher_oracle import launch_oracle_evaluation
from src.dataset_manager import DatasetManager

app = typer.Typer(help="DevOps Green Agent - AI agent evaluation for DevOps tasks")


@app.command()
def green(
    host: str = typer.Option("0.0.0.0", help="Host to bind to"),
    port: int = typer.Option(9009, help="Port to bind to"),
):
    """Start the green agent (evaluation manager)."""
    start_green_agent(host=host, port=port)


@app.command()
def purple(
    host: str = typer.Option("localhost", help="Host to bind to"),
    port: int = typer.Option(9010, help="Port to bind to"),
):
    """Start the purple agent (Claude Code solver)."""
    start_purple_agent(host=host, port=port)


@app.command()
def oracle(
    host: str = typer.Option("localhost", help="Host to bind to"),
    port: int = typer.Option(9020, help="Port to bind to"),
):
    """Start the oracle purple agent (applies gold solutions directly)."""
    start_oracle_purple_agent(host=host, port=port)


@app.command(name="claude-code")
def claude_code(
    host: str = typer.Option("localhost", help="Host to bind to"),
    port: int = typer.Option(9030, help="Port to bind to"),
):
    """Start the Claude Code purple agent (uses Claude Code AI to solve tasks)."""
    start_claude_code_purple_agent(host=host, port=port)


@app.command()
def launch(
    task: str = typer.Argument(
        ..., 
        help="Task to evaluate. Examples: 'issue_resolving/fasterxml__jackson-core-729' or 'fasterxml__jackson-core-729'"
    ),
    dataset: str = typer.Option(
        None,
        "--dataset",
        help="Dataset directory path. If not specified, uses ./DevOps-Gym and auto-clones if needed."
    ),
    force_reclone: bool = typer.Option(
        False,
        "--force-reclone",
        help="Force re-clone the dataset from GitHub (removes existing dataset first)"
    ),
):
    """Launch complete evaluation workflow (starts both agents and auto-clones dataset if needed)."""
    dataset_dir = Path(dataset) if dataset else None
    asyncio.run(launch_evaluation(task, dataset_dir, force_reclone))


@app.command(name="launch-oracle")
def launch_oracle(
    tasks: list[str] = typer.Argument(
        ..., 
        help="Task(s) to evaluate. Can specify multiple tasks. Examples: 'issue_resolving/fasterxml__jackson-core-729' 'build/build_bugfix__elastic-logstash-49134052259'"
    ),
    dataset: str = typer.Option(
        None,
        "--dataset",
        help="Dataset directory path. If not specified, uses ./DevOps-Gym and auto-clones if needed."
    ),
    force_reclone: bool = typer.Option(
        False,
        "--force-reclone",
        help="Force re-clone the dataset from GitHub (removes existing dataset first)"
    ),
):
    """Launch evaluation with Oracle purple agent (applies gold solution for testing).
    
    Examples:
        # Single task
        python main.py launch-oracle issue_resolving/containerd__containerd-4847
        
        # Multiple tasks
        python main.py launch-oracle build/build_bugfix__elastic-logstash-49134052259 issue_resolving/containerd__containerd-4847
        
        # With custom dataset location
        python main.py launch-oracle --dataset /path/to/DevOps-Gym issue_resolving/containerd__containerd-4847
        
        # Force re-clone dataset
        python main.py launch-oracle --force-reclone issue_resolving/containerd__containerd-4847
    """
    dataset_dir = Path(dataset) if dataset else None
    asyncio.run(launch_oracle_evaluation(tasks, dataset_dir, force_reclone))


@app.command(name="launch-claude-code")
def launch_claude_code(
    tasks: list[str] = typer.Argument(
        ..., 
        help="Task(s) to evaluate with Claude Code. Can specify multiple tasks."
    ),
    dataset: str = typer.Option(
        None,
        "--dataset",
        help="Dataset directory path. If not specified, uses ./DevOps-Gym and auto-clones if needed."
    ),
    force_reclone: bool = typer.Option(
        False,
        "--force-reclone",
        help="Force re-clone the dataset from GitHub (removes existing dataset first)"
    ),
):
    """Launch evaluation with Claude Code purple agent (uses Claude Code AI to solve tasks).
    
    Examples:
        # Single task
        python main.py launch-claude-code build/build_bugfix__elastic-logstash-49134052259
        
        # Multiple tasks
        python main.py launch-claude-code build/build_bugfix__elastic-logstash-49134052259 issue_resolving/containerd__containerd-4847
        
        # With custom dataset location
        python main.py launch-claude-code --dataset /scr/yuan/DevOps-Gym build/build_bugfix__elastic-logstash-49134052259
    """
    from src.launcher_claude_code import launch_claude_code_evaluation
    dataset_dir = Path(dataset) if dataset else None
    asyncio.run(launch_claude_code_evaluation(tasks, dataset_dir, force_reclone))


@app.command(name="list")
def list_tasks(
    task_type: str = typer.Option(
        None,
        "--task-type",
        help="Filter by task type: build, end_to_end, issue_resolving, monitor, test_generation"
    ),
    dataset: str = typer.Option(
        None,
        "--dataset",
        help="Dataset directory path. If not specified, uses ./DevOps-Gym and auto-clones if needed."
    ),
    force_reclone: bool = typer.Option(
        False,
        "--force-reclone",
        help="Force re-clone the dataset from GitHub (removes existing dataset first)"
    ),
):
    """List all available tasks in DevOps-Gym (auto-clones if needed)."""
    dataset_dir = Path(dataset) if dataset else None
    dataset_mgr = DatasetManager(dataset_dir, force_reclone=force_reclone)
    
    tasks = dataset_mgr.list_tasks(task_type=task_type)
    
    if task_type:
        print(f"Tasks of type '{task_type}' ({len(tasks)} total):")
    else:
        print(f"All tasks ({len(tasks)} total):")
    
    for task in tasks:
        print(f"  {task}")


@app.command()
def batch(
    purple_url: str = typer.Option(
        "http://localhost:9010",
        "--purple-url",
        help="Purple agent URL"
    ),
    task_type: str = typer.Option(
        None,
        "--task-type",
        help="Filter by task type (omit to run all types)"
    ),
    task_ids: list[str] = typer.Option(
        None,
        "--task-id",
        help="Specific task IDs to run (can specify multiple times, omit to run all)"
    ),
    dataset: str = typer.Option(
        None,
        "--dataset",
        help="Dataset directory path. If not specified, uses ./DevOps-Gym and auto-clones if needed."
    ),
    force_reclone: bool = typer.Option(
        False,
        "--force-reclone",
        help="Force re-clone the dataset from GitHub (removes existing dataset first)"
    ),
    green_url: str = typer.Option(
        "http://localhost:9009",
        "--green-url",
        help="Green agent URL"
    ),
):
    """Run batch evaluation - evaluate multiple tasks in one command.
    
    Examples:
        # Run all issue_resolving tasks
        python main.py batch --task-type issue_resolving
        
        # Run specific tasks
        python main.py batch --task-id containerd__containerd-4847 --task-id fasterxml__jackson-core-729
        
        # Run all tasks
        python main.py batch
        
        # With custom dataset location
        python main.py batch --dataset /path/to/DevOps-Gym --task-type build
        
        # Force re-clone dataset before running
        python main.py batch --force-reclone --task-type issue_resolving
    """
    from src.batch_runner import run_batch_evaluation
    asyncio.run(run_batch_evaluation(
        green_agent_url=green_url,
        purple_agent_url=purple_url,
        task_type=task_type,
        task_ids=task_ids,
        dataset_dir=Path(dataset) if dataset else None,
        force_reclone=force_reclone
    ))


if __name__ == "__main__":
    app()
