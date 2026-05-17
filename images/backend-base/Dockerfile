# witwave backend-base - the shared backend base + analyzer toolchain.
#
# Intent: claude/codex/gemini backend Dockerfiles use
# `FROM ghcr.io/witwave-ai/images/backend-base:<version>` as their common base,
# then layer only backend-specific runtime dependencies on top. The release
# workflow builds and publishes backend-base first, then builds those backend images
# against the same stripped release version.
#
# Important boundary: this image provides binaries and runtime convenience only.
# It does not grant cluster authority. In-pod Kubernetes credentials/RBAC are
# controlled separately by WitwaveAgent.spec.kubernetesApiAccess.
#
# Why split it out:
# - The seven Go tools (goimports, staticcheck, errcheck, ineffassign,
#   controller-gen, govulncheck, gosec) currently compile from source
#   in every backend x every platform = 42 redundant compiles per
#   release. Here they compile once, ship as binaries.
# - The `ww` CLI is also currently built from source in every backend
#   image; here it builds once in a dedicated stage and ships as a
#   binary alongside the Go tools.
# - The Go compiler lives once in this shared base instead of being
#   installed independently in each backend image. Agents still need real
#   `go`, `gofmt`, `go test`, and `go build` access when working on this
#   repo, so the final backend-base image carries both the toolchain and the
#   resulting analyzer binaries.
# - Tool versions are pinned in exactly one place. Bumping a version
#   touches this file and the backend-base version tag; no chance of three
#   backends drifting apart.
#
# All ARG VERSIONs below match what claude/codex/gemini previously installed
# directly so the base-image swap is image composition, not tool version drift.

# -----------------------------------------------------------------------------
# Stage 1 - Shared Go-toolchain base. Two downstream stages (go-build
# and ww-build) extend this; defining it once cuts the Go install +
# extract from twice to once per platform under buildx layer cache.
# -----------------------------------------------------------------------------

FROM debian:bookworm-20260316-slim AS go-toolchain

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

ARG GO_VERSION=1.26.3
RUN ARCH=$(dpkg --print-architecture) \
    && case "$ARCH" in \
        amd64) GO_ARCH=amd64 ;; \
        arm64) GO_ARCH=arm64 ;; \
        *) echo "Unsupported arch: $ARCH" >&2 && exit 1 ;; \
       esac \
    && curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-${GO_ARCH}.tar.gz" \
         -o /tmp/go.tar.gz \
    && tar -C /usr/local -xzf /tmp/go.tar.gz \
    && rm /tmp/go.tar.gz

ENV PATH="/usr/local/go/bin:$PATH" \
    GOPATH="/usr/local/gopath" \
    GOFLAGS="-mod=mod" \
    GOTOOLCHAIN=local

# -----------------------------------------------------------------------------
# Stage 2 - Go hygiene/analyzer tools. Compiler discarded; the seven
# binaries flow to the final stage via COPY.
# -----------------------------------------------------------------------------

FROM go-toolchain AS go-build

ARG GOIMPORTS_VERSION=v0.27.0
ARG STATICCHECK_VERSION=2026.1
ARG ERRCHECK_VERSION=v1.10.0
ARG INEFFASSIGN_VERSION=v0.1.0
ARG CONTROLLER_GEN_VERSION=v0.16.5
ARG GOVULNCHECK_VERSION=v1.1.4
ARG GOSEC_VERSION=v2.26.1

RUN go install "golang.org/x/tools/cmd/goimports@${GOIMPORTS_VERSION}" \
    && go install "honnef.co/go/tools/cmd/staticcheck@${STATICCHECK_VERSION}" \
    && go install "github.com/kisielk/errcheck@${ERRCHECK_VERSION}" \
    && go install "github.com/gordonklaus/ineffassign@${INEFFASSIGN_VERSION}" \
    && go install "sigs.k8s.io/controller-tools/cmd/controller-gen@${CONTROLLER_GEN_VERSION}" \
    && go install "golang.org/x/vuln/cmd/govulncheck@${GOVULNCHECK_VERSION}" \
    && go install "github.com/securego/gosec/v2/cmd/gosec@${GOSEC_VERSION}"

# -----------------------------------------------------------------------------
# Stage 3 - Build the `ww` CLI from in-repo source. Previously rebuilt
# in every backend x every platform; centralized here. Build context
# is the repo root (set by the workflow), so `clients/ww/` is reachable.
# -----------------------------------------------------------------------------

FROM go-toolchain AS ww-build

ARG VERSION=dev
ARG COMMIT=unknown
ARG BUILD_DATE=unknown

# Two-step COPY so `go mod download` stays in its own cache layer -
# layers above invalidate only when go.mod/go.sum change, not on every
# clients/ww/ source edit.
COPY clients/ww/go.mod clients/ww/go.sum /tmp/ww-src/
RUN cd /tmp/ww-src \
    && GOCACHE=/tmp/gocache go mod download

COPY clients/ww/ /tmp/ww-src/
RUN cd /tmp/ww-src \
    && GOCACHE=/tmp/gocache CGO_ENABLED=0 \
       go build -trimpath \
         -ldflags="-s -w \
           -X github.com/witwave-ai/witwave/clients/ww/cmd.Version=${VERSION} \
           -X github.com/witwave-ai/witwave/clients/ww/cmd.Commit=${COMMIT} \
           -X github.com/witwave-ai/witwave/clients/ww/cmd.BuildDate=${BUILD_DATE}" \
         -o /usr/local/bin/ww . \
    && /usr/local/bin/ww version

# -----------------------------------------------------------------------------
# Stage 4 - Python tools-venv with hygiene + test deps.
# Carried over to the final stage at /opt/tools-venv. Debian venvs keep
# lightweight entrypoint links to the system Python runtime, so consumers must
# provide /usr/bin/python3.
# -----------------------------------------------------------------------------

FROM debian:bookworm-20260316-slim AS python-build

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    python3 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Tools venv lives at /opt/tools-venv and is exposed on PATH for direct
# base-image usage and non-runtime checks. Backends still install pytest-class
# tooling into their agent venv so test commands see backend dependencies.
# Pinned versions match what claude/codex/gemini install in their agent venv.
RUN python3 -m venv /opt/tools-venv \
    && /opt/tools-venv/bin/pip install --no-cache-dir \
        'ruff==0.6.9' \
        'yamllint==1.35.1' \
        'pytest==8.3.3' \
        'pytest-asyncio==0.24.0' \
        'httpx==0.28.1' \
        'kubernetes==31.0.0' \
        'pip-audit==2.10.0' \
        'bandit==1.9.4'

# -----------------------------------------------------------------------------
# Stage 5 - final image: shared backend OS/runtime surface + Go/Node/Python
# tooling + downloaded binaries + ww. This is what gets published as
# ghcr.io/witwave-ai/images/backend-base.
# -----------------------------------------------------------------------------

FROM debian:bookworm-20260316-slim AS final

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV DEBIAN_FRONTEND=noninteractive

# This mirrors the common baseline the backend images used to install
# individually. The backend-specific Dockerfiles should only add their SDK or
# browser runtime pieces on top of this.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    gnupg \
    jq \
    procps \
    python3 \
    python3-pip \
    python3-venv \
    shellcheck \
    zsh \
    && rm -rf /var/lib/apt/lists/*

# GitHub CLI (gh) - release/status skills use this to inspect CI and tags.
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      | gpg --dearmor -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# Node.js 20 - dashboard verification and the Claude Code CLI both need npm.
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Keep the Go compiler in the shared base so agents can run gofmt/go test/go
# build, not just precompiled analyzer binaries.
COPY --from=go-toolchain /usr/local/go /usr/local/go
RUN ln -sf /usr/local/go/bin/go /usr/local/bin/go \
    && ln -sf /usr/local/go/bin/gofmt /usr/local/bin/gofmt

# Go hygiene/analyzer binaries from Stage 2.
COPY --from=go-build /usr/local/gopath/bin/ /usr/local/bin/

# `ww` CLI binary from Stage 3.
COPY --from=ww-build /usr/local/bin/ww /usr/local/bin/ww

# shfmt - shell-script formatter.
ARG SHFMT_VERSION=3.10.0
RUN ARCH=$(dpkg --print-architecture) \
    && curl -fsSL "https://github.com/mvdan/sh/releases/download/v${SHFMT_VERSION}/shfmt_v${SHFMT_VERSION}_linux_${ARCH}" \
         -o /usr/local/bin/shfmt \
    && chmod +x /usr/local/bin/shfmt

# actionlint - GitHub Actions workflow linter.
ARG ACTIONLINT_VERSION=1.7.7
RUN ARCH=$(dpkg --print-architecture) \
    && curl -fsSL "https://github.com/rhysd/actionlint/releases/download/v${ACTIONLINT_VERSION}/actionlint_${ACTIONLINT_VERSION}_linux_${ARCH}.tar.gz" \
         | tar -xz -C /usr/local/bin actionlint \
    && chmod +x /usr/local/bin/actionlint

# hadolint - Dockerfile linter. Naming uses kernel-arch (x86_64 / arm64)
# rather than dpkg-arch (amd64 / arm64).
ARG HADOLINT_VERSION=2.12.0
RUN ARCH=$(dpkg --print-architecture) \
    && case "$ARCH" in \
        amd64) HADOLINT_ARCH=x86_64 ;; \
        arm64) HADOLINT_ARCH=arm64 ;; \
        *) echo "Unsupported arch: $ARCH" >&2 && exit 1 ;; \
       esac \
    && curl -fsSL "https://github.com/hadolint/hadolint/releases/download/v${HADOLINT_VERSION}/hadolint-Linux-${HADOLINT_ARCH}" \
         -o /usr/local/bin/hadolint \
    && chmod +x /usr/local/bin/hadolint

# gitleaks - secret-in-source detection. Naming uses x64/arm64.
ARG GITLEAKS_VERSION=8.30.1
RUN ARCH=$(dpkg --print-architecture) \
    && case "$ARCH" in \
        amd64) GITLEAKS_ARCH=x64 ;; \
        arm64) GITLEAKS_ARCH=arm64 ;; \
        *) echo "Unsupported arch: $ARCH" >&2 && exit 1 ;; \
       esac \
    && curl -fsSL "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_${GITLEAKS_ARCH}.tar.gz" \
         | tar -xz -C /usr/local/bin gitleaks \
    && chmod +x /usr/local/bin/gitleaks

# trivy - filesystem CVE scan + image scan. Naming uses 64bit / ARM64.
ARG TRIVY_VERSION=0.70.0
RUN ARCH=$(dpkg --print-architecture) \
    && case "$ARCH" in \
        amd64) TRIVY_ARCH=64bit ;; \
        arm64) TRIVY_ARCH=ARM64 ;; \
        *) echo "Unsupported arch: $ARCH" >&2 && exit 1 ;; \
       esac \
    && curl -fsSL "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_Linux-${TRIVY_ARCH}.tar.gz" \
         | tar -xz -C /usr/local/bin trivy \
    && chmod +x /usr/local/bin/trivy

# helm - chart linting + diff inspection. Official installer auto-detects arch.
ARG HELM_VERSION=v3.16.3
RUN curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 \
      | DESIRED_VERSION="${HELM_VERSION}" USE_SUDO=false HELM_INSTALL_DIR=/usr/local/bin bash

# kubectl - read-only cluster diagnostics for platform-health / restart
# triage when a backend-base consumer is deliberately given Kubernetes credentials
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

# Python tools-venv from Stage 2.
COPY --from=python-build /opt/tools-venv /opt/tools-venv

RUN useradd -m -s /usr/bin/zsh -u 1000 agent

ENV PATH="/usr/local/go/bin:/opt/tools-venv/bin:/usr/local/bin:$PATH" \
    GOFLAGS="-mod=mod" \
    GOTOOLCHAIN=local

# Smoke-test every binary as the last build step. Catches any "binary
# present but unrunnable" failure (wrong arch, missing dep) before this
# image gets published. Each invocation just prints version; any
# non-zero exit fails the build.
RUN set -euo pipefail \
    && git --version >/dev/null \
    && gh --version >/dev/null \
    && jq --version >/dev/null \
    && node --version >/dev/null \
    && npm --version >/dev/null \
    && go version >/dev/null \
    && command -v gofmt >/dev/null \
    && shellcheck --version >/dev/null \
    && command -v goimports >/dev/null \
    && staticcheck --version >/dev/null \
    && command -v errcheck >/dev/null \
    && command -v ineffassign >/dev/null \
    && controller-gen --version >/dev/null \
    && govulncheck --version >/dev/null \
    && gosec --version >/dev/null 2>&1 \
    && shfmt --version >/dev/null \
    && actionlint --version >/dev/null \
    && hadolint --version >/dev/null \
    && gitleaks version >/dev/null \
    && trivy --version >/dev/null \
    && helm version --short >/dev/null \
    && kubectl version --client=true --output=yaml >/dev/null \
    && ww version >/dev/null \
    && /opt/tools-venv/bin/ruff --version >/dev/null \
    && /opt/tools-venv/bin/yamllint --version >/dev/null \
    && /opt/tools-venv/bin/pytest --version >/dev/null \
    && /opt/tools-venv/bin/pip-audit --version >/dev/null \
    && /opt/tools-venv/bin/bandit --version >/dev/null \
    && echo "backend-base smoke OK"

# Default command: print a one-line manifest of what's inside, useful for
# `docker run --rm ghcr.io/.../backend-base` ad-hoc inspection.
CMD ["bash", "-c", "echo 'witwave backend-base - installed tools:' && ls -1 /usr/local/bin/ /opt/tools-venv/bin/"]
