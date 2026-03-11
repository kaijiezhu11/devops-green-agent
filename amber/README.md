# Amber — DevOps-Gym Scenario

## Setup

```bash
cp sample.env .env
```

Fill in your `ANTHROPIC_API_KEY`.

## Build images

From the repo root:

```bash
docker build -t devops-green-agent:local .
docker build -t devops-claude-code-agent:local -f Dockerfile.claude .
```

## Compile

```bash
docker run --rm -v "$PWD":/work -w /work ghcr.io/rdi-foundation/amber-cli:main compile amber-scenario.json5 --docker-compose devopsgym.yml
```

## Run

```bash
export $(grep -v '^#' .env | xargs) && docker compose -f devopsgym.yml up
```
