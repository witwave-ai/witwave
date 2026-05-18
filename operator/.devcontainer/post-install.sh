#!/bin/bash
#
# Operator devcontainer post-install bootstrap.
#
# Installs the toolchain the operator developer flow expects in
# /usr/local/bin: `kind` (local Kubernetes-in-Docker), `kubebuilder`
# (the controller scaffolder + envtest assets), and the current stable
# `kubectl` (looked up via dl.k8s.io/release/stable.txt). All three are
# pulled from upstream "latest" — pin if reproducibility matters more
# than freshness for a given workstream.
#
# Also pre-creates the docker bridge network named `kind` with an
# explicit 172.19.0.0/24 subnet. `kind create cluster` will reuse a
# pre-existing network of that name instead of auto-creating its own,
# so this pin gives every cluster on this devcontainer the same node
# IP range — useful when other host services (or another docker
# bridge) already sit on kind's auto-chosen default subnet.
#
# `set -x` (no `-e`) — log each command but allow the script to
# continue past idempotent failures (network already exists, binary
# already present from a previous run).
set -x

curl -Lo ./kind https://kind.sigs.k8s.io/dl/latest/kind-linux-amd64
chmod +x ./kind
mv ./kind /usr/local/bin/kind

curl -L -o kubebuilder https://go.kubebuilder.io/dl/latest/linux/amd64
chmod +x kubebuilder
mv kubebuilder /usr/local/bin/

KUBECTL_VERSION=$(curl -L -s https://dl.k8s.io/release/stable.txt)
curl -LO "https://dl.k8s.io/release/$KUBECTL_VERSION/bin/linux/amd64/kubectl"
chmod +x kubectl
mv kubectl /usr/local/bin/kubectl

docker network create -d=bridge --subnet=172.19.0.0/24 kind

kind version
kubebuilder version
docker --version
go version
kubectl version --client
