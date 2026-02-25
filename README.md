# DevOps Green Agent

A green agent for batch evaluation of DevOps tasks using the [DevOps-Gym](https://github.com/agentsea/DevOps-Gym) dataset. Coordinates with purple agents (solvers) to evaluate tasks and run tests.

## Overview

This agent evaluates purple agents on real-world DevOps tasks including:
1. **Build and Configuration**: Project deployment and build system management
2. **Monitoring**: Runtime problem detection and system diagnosis  
3. **Issue Resolving**: Problem fixing in Java and Go projects
4. **Test Generation**: Patch validation through test case generation

The green agent:
1. Receives an assessment request with purple agent endpoint and task configuration
2. Discovers and prepares DevOps tasks from DevOps-Gym dataset
3. Spins up isolated Docker containers for each task
4. Sends task instructions to purple agents via A2A protocol
5. Runs tests to verify solutions
6. Reports results with detailed metrics

## Quick Start

### Prerequisites

- Docker installed and running
- Python 3.13+
- `uv` package manager

### Installation

```bash
# Clone repository
git clone https://github.com/yourusername/devops-greeen-agent
cd devops-greeen-agent

# Install dependencies
uv sync
```

### Running Locally

#### 1. Start Green Agent

```bash
uv run python server.py --host 0.0.0.0 --port 9119
```

#### 2. Start Purple Agent (Example: Claude Code agent)
`src/purple_agent/claude_code_agent.py`

```bash
uv run python start_claude_code_agent.py --host localhost --port 9121
```


#### 3. Run Evaluation

```bash
uv run python test_batch_eval.py \
  --purple-url http://localhost:9121 \
  --task-ids issue_resolving/containerd__containerd-4847
```


#### Run Docker
```bash
uv run python start_oracle_agent.py \
  --host 0.0.0.0 \
  --port 9121 \
  --card-url http://host.docker.internal:9121


cd /scr/yuan/devops-greeen-agent
docker build -t green-agent .
docker run -d \
  --name green-agent \
  -p 9200:9009 \
  --add-host=host.docker.internal:host-gateway \
  -v /var/run/docker.sock:/var/run/docker.sock \
  green-agent \
  --host 0.0.0.0 --port 9009 --card-url http://localhost:9200


cd /scr/yuan/devops-greeen-agent
uv run python test_batch_eval.py \
  --green-url http://localhost:9200 \
  --purple-url http://host.docker.internal:9121 \
  --task-ids issue_resolving/containerd__containerd-4847
```

## Assessment Request Format

Send an A2A message to the green agent with the following JSON structure:

```json
{
  "participants": {
    "purple_agent": "http://purple-agent-url:port"
  },
  "config": {
    "task_ids": ["issue_resolving/containerd__containerd-4847"],
    "task_type": "issue_resolving",
    "dataset": "/path/to/DevOps-Gym",
    "force_reclone": false
  }
}
```

### Request Fields

- **participants.purple_agent** (required): A2A endpoint URL of the purple agent to evaluate
- **config.task_ids** (optional): Specific task IDs to evaluate (e.g., `["issue_resolving/containerd__containerd-4847"]`)
- **config.task_type** (optional): Filter by task type: `"issue_resolving"`, `"qa"`, or omit for all types
- **config.dataset** (optional): Path to DevOps-Gym dataset (defaults to `./DevOps-Gym`)
- **config.force_reclone** (optional): Force re-clone of dataset (default: `false`)

## Purple Agent Requirements

Purple agents must:
1. Expose an A2A server endpoint
2. Handle task instructions sent by the green agent
3. Connect to provided SSH endpoints to solve tasks
4. Send **exactly one** `enqueue_event` call with the final result — this single message ends the A2A stream and signals task completion to the green agent. Do **not** send intermediate progress messages before the final result, as the first `enqueue_event` closes the stream.

### Task Message Format

The green agent sends task instructions in this format:

```xml
<ssh_command>ssh -p PORT root@localhost</ssh_command>

<instruction>
[Task description, issue details, and requirements]
</instruction>

<timeout>
You have 800.0 seconds to complete this task.
</timeout>

Please connect via SSH and solve the task.
```

# Contributions
This project is mainly built and maintained by [Michael Yang](https://github.com/MichaelY310).
