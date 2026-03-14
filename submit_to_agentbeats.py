#!/usr/bin/env python3
"""
Submit local evaluation results to AgentBeats leaderboard.

Usage:
    uv run python submit_to_agentbeats.py \\
        --results-dir ./results \\
        --purple-agent-id <AGENTBEATS_AGENT_ID> \\
        --task-type issue_resolving \\
        --model claude-opus-4-5

The script reads summary.json files from --results-dir, computes pass rate,
and writes a result JSON to leaderboard/results/. Commit and push that file
to trigger the AgentBeats GitHub App to update the leaderboard.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def load_task_results(results_dir: Path) -> list[dict]:
    """Load all summary.json files from the results directory."""
    summaries = []
    for summary_file in results_dir.rglob("summary.json"):
        try:
            data = json.loads(summary_file.read_text())
            task_name = summary_file.parent.name
            summaries.append({"task": task_name, **data})
        except Exception as e:
            print(f"  Warning: could not read {summary_file}: {e}")
    return summaries


def compute_stats(summaries: list[dict]) -> dict:
    """Compute pass rate and timing stats from task summaries."""
    if not summaries:
        return {"tasks_total": 0, "tasks_passed": 0, "pass_rate": 0.0, "avg_duration": 0.0}

    tasks_total = len(summaries)
    # A task passes if test_exit_code == 0 OR success == True
    tasks_passed = sum(
        1 for s in summaries
        if s.get("test_exit_code") == 0 or s.get("success") is True
    )
    pass_rate = tasks_passed / tasks_total if tasks_total > 0 else 0.0
    avg_duration = sum(s.get("total_duration", 0) for s in summaries) / tasks_total

    return {
        "tasks_total": tasks_total,
        "tasks_passed": tasks_passed,
        "pass_rate": pass_rate,
        "avg_duration_sec": round(avg_duration, 1),
    }


def build_result(purple_agent_id: str, stats: dict, task_type: str, model: str) -> dict:
    """Build the AgentBeats result JSON matching the leaderboard format."""
    pass_rate = round(stats["pass_rate"] * 100, 2)

    # Format matches existing leaderboard results (e.g. openhands_claude-4-sonnet.json)
    agent_label = f"{model}" if model != "unknown" else purple_agent_id

    return {
        "participants": {
            "agent": agent_label,
        },
        "avg": pass_rate,
        "results": [
            {
                "task_type": task_type,
                "pass_rate": pass_rate,
                "tasks_passed": stats["tasks_passed"],
                "tasks_total": stats["tasks_total"],
                "avg_duration_sec": stats["avg_duration_sec"],
            }
        ],
    }


def main():
    parser = argparse.ArgumentParser(description="Submit local eval results to AgentBeats.")
    parser.add_argument(
        "--results-dir", type=Path, default=Path("./results"),
        help="Directory containing task result subdirs with summary.json (default: ./results)"
    )
    parser.add_argument(
        "--purple-agent-id", required=True,
        help="AgentBeats agent ID of the purple agent (from Edit Agent > Copy agent ID)"
    )
    parser.add_argument(
        "--task-type", default="issue_resolving",
        choices=["issue_resolving", "build", "end_to_end", "monitor", "test_generation"],
        help="Task type that was evaluated (default: issue_resolving)"
    )
    parser.add_argument(
        "--model", default="unknown",
        help="Model name used by the purple agent (e.g. claude-opus-4-5)"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("./DevOpsGym-AgentBeats-Leaderboard/results"),
        help="Where to write the AgentBeats result JSON (default: ./DevOpsGym-AgentBeats-Leaderboard/results)"
    )
    args = parser.parse_args()

    print("=" * 60)
    print("DevOps-Gym → AgentBeats Result Submission")
    print("=" * 60)

    # Load results
    if not args.results_dir.exists():
        print(f"Error: results directory not found: {args.results_dir}")
        sys.exit(1)

    print(f"\nLoading results from: {args.results_dir}")
    summaries = load_task_results(args.results_dir)

    if not summaries:
        print("Error: no summary.json files found.")
        sys.exit(1)

    print(f"Found {len(summaries)} task result(s)")

    # Compute stats
    stats = compute_stats(summaries)
    print(f"\nResults:")
    print(f"  Tasks:     {stats['tasks_passed']} / {stats['tasks_total']} passed")
    print(f"  Pass rate: {stats['pass_rate'] * 100:.1f}%")
    print(f"  Avg time:  {stats['avg_duration_sec']}s per task")

    # Build result JSON
    result = build_result(
        purple_agent_id=args.purple_agent_id,
        stats=stats,
        task_type=args.task_type,
        model=args.model,
    )

    # Write to leaderboard/results/
    args.output_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"run_{args.task_type}_{args.model.replace('-', '_')}_{date_str}.json"
    output_file = args.output_dir / filename

    output_file.write_text(json.dumps(result, indent=2))

    print(f"\nSaved result to: {output_file}")
    print("\nNext steps:")
    print("  git add leaderboard/results/")
    print(f'  git commit -m "Add {args.task_type} evaluation results ({args.model})"')
    print("  git push")
    print("\nAgentBeats GitHub App will detect the new file and update the leaderboard.")


if __name__ == "__main__":
    main()
