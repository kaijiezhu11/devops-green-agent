# Run the server

With Docker:
```bash
docker compose up -d
```

Or locally (requires Docker socket access):
```bash
uv run python src/server.py --host 0.0.0.0
```

# Run a few tasks without running claude code (but it installs claude code)
```bash
uv run python batch_run_tasks.py dataset_subset --no-run \
```

This outputs an SSH command like: `ssh -p 34290 root@localhost`

# Run claude code
```bash
uv run python batch_run_tasks.py dataset_subset \
# use -t to specify tasks.
```