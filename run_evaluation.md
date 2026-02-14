# Running Evaluation

## Quick Start

### 1. Install dependencies
```bash
uv sync
```

### 2. Run the server

With Docker:
```bash
docker compose up -d
```

Or locally (requires Docker socket access):
```bash
uv run python src/server.py --host 0.0.0.0
```

### 3. Create a task container
```bash
uv run python start_containerd_task.py
```

This outputs an SSH command like: `ssh -p 34290 root@localhost`

### 4. Connect to task container
```bash
ssh -p <port> root@localhost

# Optional: Apply solution
bash /solution.sh
```

### 5. Trigger test execution
Inside the task container:
```bash
python3 /trigger_test.py
```

Exit the container. Test logs appear in `./test_log_*.txt`.

## Testing

Run tests against the agent:
```bash
# Start the agent first (see above)

# Run tests
uv run pytest --agent-url http://localhost:9009
```


## Install claude code
```
apt-get update && apt-get install -y curl
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.2/install.sh | bash
export NVM_DIR="$HOME/.nvm" && \. "$NVM_DIR/nvm.sh"
nvm install 22
npm install -g @anthropic-ai/claude-code@latest

export ANTHROPIC_API_KEY="your-api-key-here"

claude -p "Fix the undefined variable error in main.py"
```