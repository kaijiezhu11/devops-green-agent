# DevOps Green Agent

AI agent evaluation framework for DevOps tasks.

## Purple Agents

This framework supports multiple "purple agents" (solvers):

1. **Oracle Agent** (`launch-oracle`): Applies gold solutions for testing infrastructure
2. **Claude Code Agent** (`launch-claude-code`): Uses Claude Code AI to solve tasks (requires `ANTHROPIC_API_KEY`)

### Claude Code Documentation

- **[Quick Start (快速开始)](QUICK_START.md)** - Get started in 5 minutes
- **[中文说明 (README_CN.md)](README_CN.md)** - Complete Chinese documentation
- **[Detailed Documentation (CLAUDE_CODE_AGENT.md)](CLAUDE_CODE_AGENT.md)** - Full English documentation
- **[Implementation Summary](IMPLEMENTATION_SUMMARY.md)** - Technical implementation details

### Helper Scripts

```bash
# Check if your environment is ready
uv run python check_setup.py

# Compare Oracle vs Claude Code to identify issues
./compare_agents.sh build/build_bugfix__elastic-logstash-49134052259
```

## Quick Start

### Local (uv)

```bash
# Install dependencies
uv sync

# Run a task with Oracle agent (applies gold solution for testing)
uv run python main.py launch-oracle issue_resolving/containerd__containerd-4847

# Run a task with Claude Code AI agent (requires ANTHROPIC_API_KEY)
export ANTHROPIC_API_KEY="sk-ant-..."
uv run python main.py launch-claude-code --dataset /scr/yuan/DevOps-Gym build/build_bugfix__elastic-logstash-49134052259
```

### Docker

```bash
# 1. Start containers
cd /scr/yuan/devops-greeen-agent && \
export HOST_WORKSPACE_PATH="$(pwd)" && \
export HOST_DEVOPS_GYM_PATH="$(pwd)/DevOps-Gym" && \
docker-compose -f docker-compose.full.yml up -d --build

# 2. Run evaluation (single task)
./docker-run-oracle.sh monitor/real_world_repos__grafana-high-cpu-usage

# 3. Run evaluation (multiple tasks)
./docker-run-oracle.sh \
  issue_resolving/containerd__containerd-4847 \
  monitor/real_world_repos__grafana-high-cpu-usage

# 4. Stop containers
docker-compose -f docker-compose.full.yml down
```

The Docker setup runs evaluations inside the green agent container:
- Green agent: `http://localhost:9009`
- Oracle agent: `http://localhost:9020`

## Commands

### List Tasks

```bash
# All tasks
uv run python main.py list

# Filter by type
uv run python main.py list --task-type issue_resolving
```

Task types: `build`, `end_to_end`, `issue_resolving`, `monitor`, `test_generation`

### Run Single/Multiple Tasks

```bash
# Single task
uv run python main.py launch-oracle issue_resolving/containerd__containerd-4847

# Multiple tasks
uv run python main.py launch-oracle \
  build/build_bugfix__elastic-logstash-49134052259 \
  issue_resolving/containerd__containerd-4847 \
  monitor/real_world_repos__grafana-high-cpu-usage
```

### Batch Evaluation

```bash
# Run all issue_resolving tasks
uv run python main.py batch --task-type issue_resolving

# Run specific tasks
uv run python main.py batch \
  --task-id containerd__containerd-4847 \
  --task-id build_bugfix__elastic-logstash-49134052259

# Run ALL tasks
uv run python main.py batch
```

## Options

### Specify Dataset Location

```bash
--dataset /path/to/DevOps-Gym
```

Example:
```bash
uv run python main.py launch-oracle --dataset /scr/yuan/DevOps-Gym issue_resolving/containerd__containerd-4847
```

### Force Re-clone Dataset

```bash
--force-reclone
```

Example:
```bash
uv run python main.py launch-oracle --force-reclone issue_resolving/containerd__containerd-4847
```

## Results

Terminal output shows progress and results:

```
Green agent: Batch evaluation complete. Passed: 4/4
```

## Recommended Test (4 tasks, ~5 minutes)

### Local

```bash
uv run python main.py launch-oracle \
  --dataset /scr/yuan/DevOps-Gym \
  build/build_bugfix__elastic-logstash-49134052259 \
  issue_resolving/containerd__containerd-4847 \
  monitor/real_world_repos__grafana-high-cpu-usage \
  test_generation/containerd__containerd-4847
```

### Docker

```bash
# 1. Start agents
./docker-start.sh

# 2. Send evaluation request (from another terminal, or use AgentBeats)
curl -X POST http://localhost:9009 \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "message.send",
    "params": {
      "message": {
        "role": "user",
        "parts": [{"text": "<purple_agent_url>http://oracle-agent:9020</purple_agent_url>\n<task_ids>issue_resolving/containerd__containerd-4847</task_ids>\n<dataset_dir>/DevOps-Gym</dataset_dir>"}]
      }
    },
    "id": "1"
  }'

# 3. View results in logs
docker-compose -f docker-compose.full.yml logs green-agent
```

Expected: **4/4 PASSED** ✅
