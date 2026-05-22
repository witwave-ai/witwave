ARG BACKEND_BASE_IMAGE=ghcr.io/witwave-ai/images/backend-base:latest
FROM ${BACKEND_BASE_IMAGE}

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV GH_PROMPT_DISABLED=1 \
    NODE_ENV=production \
    PATH="/usr/local/go/bin:/opt/tools-venv/bin:/usr/local/bin:$PATH"

USER agent
WORKDIR /home/agent

COPY --chown=agent:agent backends/codex/package*.json /home/agent/codex/
WORKDIR /home/agent/codex
RUN npm ci --omit=dev

COPY --chown=agent:agent backends/codex/ /home/agent/codex/

RUN mkdir -p /home/agent/logs /home/agent/.codex/memory /home/agent/.codex/sessions /home/agent/workspace
WORKDIR /home/agent/workspace

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${BACKEND_PORT:-8000}/health || exit 1

EXPOSE 8000
# Dedicated Prometheus metrics listener (#643). Served on 9000 by default;
# override with METRICS_PORT env var. Only active when METRICS_ENABLED is set.
EXPOSE 9000

CMD ["node", "/home/agent/codex/main.js"]
