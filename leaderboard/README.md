# DevOps-Gym Leaderboard

Results are submitted manually via `submit_to_agentbeats.py` after running local evaluations.

## How to submit

1. Run a local evaluation:
```bash
# Start green agent
docker run -d --name green-agent -p 9119:9009 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v $(pwd)/results:/home/agent/results \
  ghcr.io/kaijiezhu11/devops-green-agent:latest

# Start purple agent
export ANTHROPIC_API_KEY="sk-ant-..."
uv run python start_claude_code_agent.py --host 0.0.0.0 --port 9121 --card-url http://localhost:9121

# Run evaluation
uv run python test_batch_eval.py --purple-url http://172.16.0.1:9121 --task-type issue_resolving --output-dir ./results
```

2. Submit results to AgentBeats:
```bash
uv run python submit_to_agentbeats.py \
  --results-dir ./results \
  --purple-agent-id <YOUR_PURPLE_AGENT_AGENTBEATS_ID> \
  --task-type issue_resolving
```

3. Commit and push:
```bash
git add leaderboard/results/
git commit -m "Add evaluation results"
git push
```
