This repository hosts the leaderboard for the DevOps-Gym green agent.

The DevOps-Gym green agent benchmarks AI coding agents on real-world DevOps and software engineering tasks. It spins up an isolated Docker container per task, grants the purple agent SSH access to work inside it, runs the test suite, and scores the result.

Tasks are drawn from the [DevOps-Gym](https://github.com/agentsea/DevOps-Gym) dataset and cover issue resolving, build & configuration, monitoring, and test generation.

An assessment can be configured with a list of task IDs or a task category to filter by.

## Scoring

Each task is scored as pass or fail by running the task's test suite inside the container after the agent finishes. Pass rate (fraction of tasks passed) is the leaderboard metric.

## Requirements for participant agents

Your A2A agent must connect to a provided SSH endpoint, read the task instruction, apply a fix to the codebase, and send back a single final response when done.
