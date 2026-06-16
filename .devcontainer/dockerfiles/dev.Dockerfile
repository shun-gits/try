FROM mcr.microsoft.com/devcontainers/python:3.11-bookworm

# Install essential system utilities for VS Code/Antigravity/Codex sandboxing
RUN apt-get update && export DEBIAN_FRONTEND=noninteractive \
    && apt-get -y install --no-install-recommends \
        bubblewrap \
        procps \
        sqlite3 \
        socat \
        uidmap \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install dependencies from pyproject.toml
COPY pyproject.toml README.md /tmp/pip-tmp/
RUN pip3 --disable-pip-version-check --no-cache-dir install "/tmp/pip-tmp/[dev]" \
    && rm -rf /tmp/pip-tmp

# Create DAGSTER_HOME directory
RUN mkdir -p /workspaces/local_dagster_state_metadata && \
    chmod 777 /workspaces/local_dagster_state_metadata

# [Optional] Uncomment this section to install additional OS packages.
# RUN apt-get update && export DEBIAN_FRONTEND=noninteractive \
#     && apt-get -y install --no-install-recommends <your-package-list-here>
