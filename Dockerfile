FROM ghcr.io/astral-sh/uv:python3.13-bookworm

# Install Docker CLI (needed to communicate with host Docker)
RUN apt-get update && \
    apt-get install -y docker.io && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /home/agent

COPY pyproject.toml uv.lock README.md server.py ./
COPY src src

RUN uv sync

ENTRYPOINT ["uv", "run", "python", "server.py"]
CMD ["--host", "0.0.0.0", "--port", "9009"]
EXPOSE 9009