#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/build-whitepaper-pdfs.sh [output-directory]

Builds PDF versions of the public whitepapers from their canonical Markdown
sources. The pipeline mirrors the career repo pattern: Pandoc renders
standalone embedded HTML, then Chrome/Puppeteer prints that HTML to PDF.

When no output directory is provided, PDFs are written under build/whitepapers/.
For website publishing, pass the generated site directory's whitepapers folder.
USAGE
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

repo_root="$(git rev-parse --show-toplevel)"
output_root="${1:-${repo_root}/build/whitepapers}"
catalog_path="${repo_root}/social/website/content/whitepapers.json"
css_path="${repo_root}/social/papers/whitepaper-pdf.css"
tmp_dir="$(mktemp -d)"

cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

find_chrome() {
  if [ -n "${CHROME:-}" ] && [ -x "$CHROME" ]; then
    printf '%s\n' "$CHROME"
    return 0
  fi

  local mac_chrome="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  if [ -x "$mac_chrome" ]; then
    printf '%s\n' "$mac_chrome"
    return 0
  fi

  local candidate
  for candidate in google-chrome google-chrome-stable chromium chromium-browser; do
    if command -v "$candidate" >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done

  return 1
}

if ! command -v pandoc >/dev/null 2>&1; then
  echo "pandoc is required to build whitepaper PDFs." >&2
  exit 1
fi

chrome_path="$(find_chrome || true)"
if [ -z "$chrome_path" ]; then
  echo "Chrome or Chromium is required to print whitepaper PDFs." >&2
  echo "Set CHROME=/path/to/chrome if it is installed in a non-standard location." >&2
  exit 1
fi

mkdir -p "$output_root"

while IFS=$'\t' read -r slug title source_path; do
  if [ -z "$slug" ] || [ -z "$source_path" ]; then
    continue
  fi

  source_file="${repo_root}/${source_path}"
  if [ ! -f "$source_file" ]; then
    echo "whitepaper source not found: $source_path" >&2
    exit 1
  fi

  paper_output_dir="${output_root}/${slug}"
  html_file="${tmp_dir}/${slug}.html"
  pdf_file="${paper_output_dir}/${slug}.pdf"

  mkdir -p "$paper_output_dir"

  pandoc "$source_file" \
    --from gfm \
    --standalone \
    --embed-resources \
    --metadata "pagetitle=${title}" \
    --metadata "author=Witwave" \
    --css "$css_path" \
    --output "$html_file"

  CHROME="$chrome_path" node "$repo_root/scripts/print-whitepaper-pdf.mjs" "$html_file" "$pdf_file"
  printf 'Built %s\n' "${pdf_file#"$repo_root/"}"
done < <(
  node - "$catalog_path" <<'NODE'
const fs = require('node:fs');

const catalogPath = process.argv[2];
const catalog = JSON.parse(fs.readFileSync(catalogPath, 'utf8'));

for (const paper of catalog.whitepapers || []) {
  if (paper.display === false || paper.status === 'archived') continue;
  const slug = paper.slug || '';
  const title = paper.title || paper.shortTitle || slug;
  const sourcePath = paper.sourcePath || '';
  console.log([slug, title, sourcePath].join('\t'));
}
NODE
)
