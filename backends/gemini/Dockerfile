ARG BACKEND_BASE_IMAGE=ghcr.io/witwave-ai/images/backend-base:latest
FROM ${BACKEND_BASE_IMAGE}

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV DEBIAN_FRONTEND=noninteractive \
    GH_PROMPT_DISABLED=1

USER agent
WORKDIR /home/agent

RUN python3 -m venv .venv
ENV PATH="/home/agent/.venv/bin:/opt/tools-venv/bin:/usr/local/go/bin:/usr/local/bin:$PATH"

COPY --chown=agent:agent backends/gemini/requirements.txt /home/agent/gemini/requirements.txt
RUN pip install --no-cache-dir -r /home/agent/gemini/requirements.txt

# Keep Python verification tools in the agent venv so pytest sees the same
# backend dependencies as runtime code. The shared backend base venv remains
# useful for direct base-image use and non-runtime checks.
RUN pip install --no-cache-dir \
    'ruff==0.6.9' \
    'yamllint==1.35.1' \
    'pytest==8.3.3' \
    'pytest-asyncio==0.24.0' \
    'httpx==0.28.1' \
    'kubernetes==31.0.0' \
    'pip-audit==2.10.0' \
    'bandit==1.9.4'

COPY --chown=agent:agent backends/gemini/ /home/agent/gemini/
COPY --chown=agent:agent shared/ /home/agent/shared/

ENV PYTHONPATH="/home/agent/shared"

RUN mkdir -p /home/agent/logs /home/agent/.gemini/memory
WORKDIR /home/agent/workspace

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${BACKEND_PORT:-8000}/health || exit 1

EXPOSE 8000
# Dedicated Prometheus metrics listener (#643). Served on 9000 by default;
# override with METRICS_PORT env var. Only active when METRICS_ENABLED is set.
EXPOSE 9000

CMD ["python3", "/home/agent/gemini/main.py"]
