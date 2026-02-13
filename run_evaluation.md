# Create a container that contains the green agent
```
cd /scr/yuan/devops-greeen-agent
docker compose build --no-cache
docker compose up -d
```

# Ask the green agent to create a task container
```
docker rm -f containerd__containerd-4847
uv run python start_containerd_task.py
```
This will give you a ssh command

# Imagine you are the purple agent
```
ssh -p <port from the previous command> root@localhost

# optional: copy paste the content under containerd__containerd-4847/solution.sh to the container and run the solution
```

# Send finish signal
```
# Inside the task container
python3 /trigger_test.py
```
You can exit the container immediately after sending the signal. After a few seconds a log file that reports the test result will be created here.