FROM debian:bookworm-20260316-slim

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    ca-certificates \
    curl \
    python3 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# kubectl — read-only cluster diagnostics for platform-health / restart
# triage when a backend is deliberately given Kubernetes credentials
# via service account token or kubeconfig. Installing the binary here
# does not grant cluster access by itself.
ARG KUBECTL_VERSION=v1.31.0
RUN ARCH=$(dpkg --print-architecture) \
    && case "$ARCH" in \
        amd64) KUBECTL_ARCH=amd64 ;; \
        arm64) KUBECTL_ARCH=arm64 ;; \
        *) echo "Unsupported arch: $ARCH" >&2 && exit 1 ;; \
       esac \
    && curl -fsSL -o /usr/local/bin/kubectl \
         "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${KUBECTL_ARCH}/kubectl" \
    && curl -fsSL -o /tmp/kubectl.sha256 \
         "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${KUBECTL_ARCH}/kubectl.sha256" \
    && echo "$(cat /tmp/kubectl.sha256)  /usr/local/bin/kubectl" | sha256sum -c - \
    && chmod 0755 /usr/local/bin/kubectl \
    && rm -f /tmp/kubectl.sha256

RUN useradd -m -s /bin/bash -u 1000 agent

USER agent
WORKDIR /home/agent

RUN python3 -m venv .venv
ENV PATH="/home/agent/.venv/bin:$PATH"

COPY --chown=agent:agent backends/echo/requirements.txt /home/agent/echo/requirements.txt
RUN pip install --no-cache-dir -r /home/agent/echo/requirements.txt

COPY --chown=agent:agent backends/echo/ /home/agent/echo/
COPY --chown=agent:agent shared/ /home/agent/shared/

# Resolves both `import shared.foo` (future use) and the bare `import
# metrics_server` pattern the shared helpers use internally.
ENV PYTHONPATH="/home/agent/shared:/home/agent/echo"

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${BACKEND_PORT:-8000}/health || exit 1

EXPOSE 8000
# Dedicated Prometheus metrics listener (#643). Served on 9000 by default;
# override with METRICS_PORT env var. Only active when METRICS_ENABLED is set.
EXPOSE 9000

# WORKDIR alignment — match harness, claude, openai, gemini. Echo had
# previously stayed at /home/agent (the initial build-time WORKDIR);
# every other container in a witwave agent pod runs at
# /home/agent/workspace, so a `pwd` from any container's shell now
# returns the same path. Workspace volume mounts (when present) live
# at /workspaces/<workspace>/<volume> regardless — this WORKDIR change
# only affects the container's default working directory.
RUN mkdir -p /home/agent/workspace
WORKDIR /home/agent/workspace

CMD ["python3", "/home/agent/echo/main.py"]
