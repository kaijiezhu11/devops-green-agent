#!/usr/bin/env python3
"""Test batch evaluation by sending requests to the DevOps Green Agent."""

import sys
import argparse
import asyncio
import json
from pathlib import Path

# Disable output buffering
sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__

from a2a.client import A2ACardResolver, ClientFactory, ClientConfig
from a2a.types import (
    Part,
    TextPart,
    Message,
    Role,
)
import httpx


async def send_batch_eval_request(
    green_agent_url: str,
    purple_agent_url: str,
    task_type: str = None,
    task_ids: list[str] = None,
    dataset_dir: str = None,
    force_reclone: bool = False,
    output_dir: str = None,
):
    """
    Send batch evaluation request to green agent.
    
    Args:
        green_agent_url: URL of the green agent
        purple_agent_url: URL of the purple agent
        task_type: Optional task type filter
        task_ids: Optional list of specific task IDs
        dataset_dir: Optional dataset directory path
        force_reclone: Whether to force re-clone the dataset
        output_dir: Optional directory to save detailed results for each task
    """
    # Build request message
    request = {
        "participants": {
            "purple_agent": purple_agent_url
        },
        "config": {}
    }
    
    if task_type:
        request["config"]["task_type"] = task_type
    if task_ids:
        request["config"]["task_ids"] = task_ids
    if dataset_dir:
        request["config"]["dataset_dir"] = dataset_dir
    if force_reclone:
        request["config"]["force_reclone"] = force_reclone
    if output_dir:
        request["config"]["output_dir"] = output_dir
    
    message_text = json.dumps(request)
    
    print("=" * 80)
    print("DevOps Green Agent - Batch Evaluation Test")
    print("=" * 80)
    print(f"\nGreen Agent: {green_agent_url}")
    print(f"Purple Agent: {purple_agent_url}")
    print(f"\nRequest:")
    print(json.dumps(request, indent=2))
    print("\n" + "=" * 80)
    print("Sending request...\n")
    
    # Connect to green agent
    httpx_client = httpx.AsyncClient(timeout=3600.0)  # 1 hour timeout
    resolver = A2ACardResolver(httpx_client=httpx_client, base_url=green_agent_url)
    
    try:
        agent_card = await resolver.get_agent_card()
        print(f"✓ Connected to: {agent_card.name}")
        print(f"  Description: {agent_card.description}")
        print(f"  Version: {agent_card.version}\n")
    except Exception as e:
        print(f"✗ Failed to connect to green agent at {green_agent_url}")
        print(f"  Error: {e}")
        print(f"\nMake sure the green agent is running:")
        print(f"  uv run python server.py")
        await httpx_client.aclose()
        return
    
    # Create A2A client using new API
    config = ClientConfig(
        httpx_client=httpx_client,
        streaming=True,
    )
    factory = ClientFactory(config)
    client = factory.create(agent_card)
    
    # Create message
    import uuid
    outbound_msg = Message(
        kind="message",
        role=Role.user,
        parts=[Part(TextPart(kind="text", text=message_text))],
        message_id=uuid.uuid4().hex,
    )
    
    try:
        # Stream responses
        print("Receiving responses...\n")
        print("-" * 80)
        
        async for event in client.send_message(outbound_msg):
            # Event is a tuple: (Task, TaskEvent)
            if isinstance(event, tuple):
                task, task_event = event
                
                # Display status messages
                if hasattr(task, 'status') and task.status.message:
                    for part in task.status.message.parts:
                        if hasattr(part.root, 'text'):
                            print(part.root.text)
                
                # Display artifacts (final results)
                if hasattr(task, 'artifacts') and task.artifacts:
                    for artifact in task.artifacts:
                        for part in artifact.parts:
                            if hasattr(part.root, 'text'):
                                print(part.root.text)
                            elif hasattr(part.root, 'data'):
                                print("\nDetailed Results:")
                                print(json.dumps(part.root.data, indent=2))
            elif hasattr(event, 'parts'):
                # Message response
                for part in event.parts:
                    if hasattr(part.root, 'text'):
                        print(part.root.text)
        
        print("-" * 80)
        print("\n✓ Evaluation complete!")
        
    except Exception as e:
        print(f"\n✗ Error during evaluation: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await httpx_client.aclose()


def main():
    parser = argparse.ArgumentParser(
        description="Test batch evaluation with DevOps Green Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run all issue_resolving tasks with Oracle agent
  python test_batch_eval.py --task-type issue_resolving --purple-url http://localhost:9121

  # Run specific tasks with Claude Code agent
  python test_batch_eval.py --task-ids issue_resolving/containerd__containerd-4847 build/build_bugfix__elastic-logstash-49134052259 --purple-url http://localhost:9131

  # Run all tasks
  python test_batch_eval.py --purple-url http://localhost:9121

  # With custom dataset location
  python test_batch_eval.py --dataset /path/to/DevOps-Gym --task-type build --purple-url http://localhost:9121

  # Force re-clone dataset
  python test_batch_eval.py --force-reclone --task-type issue_resolving --purple-url http://localhost:9121

  # Save detailed results to output directory
  python test_batch_eval.py --task-ids issue_resolving/containerd__containerd-4847 --purple-url http://localhost:9121 --output-dir ./results
        """
    )
    
    parser.add_argument(
        "--green-url",
        type=str,
        default="http://localhost:9119",
        help="Green agent URL (default: http://localhost:9119)"
    )
    parser.add_argument(
        "--purple-url",
        type=str,
        required=True,
        help="Purple agent URL (e.g., http://localhost:9121 for Oracle, http://localhost:9131 for Claude Code)"
    )
    parser.add_argument(
        "--task-type",
        type=str,
        choices=["build", "end_to_end", "issue_resolving", "monitor", "test_generation"],
        help="Filter by task type (omit to run all types)"
    )
    parser.add_argument(
        "--task-ids",
        type=str,
        nargs="+",
        help="Specific task IDs to run (can specify multiple, omit to run all)"
    )
    parser.add_argument(
        "--dataset",
        type=str,
        help="Dataset directory path (if not specified, uses ./DevOps-Gym and auto-clones if needed)"
    )
    parser.add_argument(
        "--force-reclone",
        action="store_true",
        help="Force re-clone the dataset from GitHub"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        help="Directory to save detailed results for each task (creates subdirectories per task)"
    )
    
    args = parser.parse_args()
    
    asyncio.run(send_batch_eval_request(
        green_agent_url=args.green_url,
        purple_agent_url=args.purple_url,
        task_type=args.task_type,
        task_ids=args.task_ids,
        dataset_dir=args.dataset,
        force_reclone=args.force_reclone,
        output_dir=args.output_dir,
    ))


if __name__ == "__main__":
    main()
