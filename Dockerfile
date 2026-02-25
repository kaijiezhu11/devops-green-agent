FROM ghcr.io/astral-sh/uv:python3.13-bookworm

# Install Docker CLI (needed to communicate with host Docker)
RUN apt-get update && \
    apt-get install -y docker.io && \
    rm -rf /var/lib/apt/lists/*

# Create agent user and add to host's docker group (GID 999)
# Also add to container's docker group for compatibility
RUN adduser agent && \
    usermod -aG docker agent && \
    usermod -aG 999 agent

USER agent
WORKDIR /home/agent

COPY pyproject.toml uv.lock README.md main.py ./
COPY src src

RUN \
    --mount=type=cache,target=/home/agent/.cache/uv,uid=1000 \
    uv sync --locked

ENTRYPOINT ["uv", "run", "python", "main.py"]
CMD ["green", "--host", "0.0.0.0", "--port", "9009"]
EXPOSE 9009