#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/sync-social-website.sh <destination-directory>

Copies social/website/ into a destination directory with symlinks resolved,
generates Markdown-backed static pages for crawlers, and builds PDF versions of
the whitepapers.
This is intended for publishing the static site to witwave-ai.github.io.

Notes:
  - Symlinks are resolved so content/whitepapers/*.md becomes real files.
  - Whitepaper list rows are rendered from content/whitepapers.json.
  - Whitepapers and published blog posts are generated as static HTML pages.
  - Whitepaper PDFs are generated from the canonical Markdown sources.
  - .git/ is never touched in the destination.
  - CNAME is copied from the source site so GitHub Pages custom-domain settings
    stay source-controlled.
USAGE
}

if [ "$#" -ne 1 ]; then
  usage >&2
  exit 2
fi

repo_root="$(git rev-parse --show-toplevel)"
source_dir="${repo_root}/social/website/"
destination_dir="$1"

if [ ! -d "$source_dir" ]; then
  echo "source directory not found: $source_dir" >&2
  exit 1
fi

mkdir -p "$destination_dir"

build_dir="$(mktemp -d)"
trap 'rm -rf "$build_dir"' EXIT

rsync -aL --delete \
  --exclude '.git/' \
  "$source_dir" "$build_dir/"

node "$repo_root/scripts/render-social-whitepaper-lists.mjs" "$build_dir"
node "$repo_root/scripts/generate-social-static-pages.mjs" "$build_dir"
"$repo_root/scripts/build-whitepaper-pdfs.sh" "$build_dir/whitepapers"

rsync -aL --delete \
  --exclude '.git/' \
  "$build_dir/" "$destination_dir/"
