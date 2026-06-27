#!/usr/bin/env python3
"""Check local Markdown links while ignoring examples and generated noise."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

SKIP_DIR_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "temp",
    "tmp",
}
SKIP_PREFIXES = {
    "clients/ww/internal/operator/embedded",
}
TEMPLATE_TARGETS = {
    "file.md",
    "new",
    "old",
    "other/file.md",
    "path",
    "path/to/file.md",
    "target",
}
INLINE_LINK_RE = re.compile(r"(?<!!)\[[^\]\n]+\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
REFERENCE_LINK_RE = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*(\S+)", re.MULTILINE)
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


@dataclass(frozen=True)
class BrokenLink:
    path: Path
    line: int
    target: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        default=["."],
        help="Files or directories to scan. Defaults to the repository root.",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Repository root used for skip-prefix matching. Defaults to the current directory.",
    )
    return parser.parse_args()


def is_skipped(path: Path, root: Path) -> bool:
    if any(part in SKIP_DIR_NAMES for part in path.parts):
        return True
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        rel = path.as_posix()
    return any(rel == prefix or rel.startswith(f"{prefix}/") for prefix in SKIP_PREFIXES)


def markdown_files(paths: list[str], root: Path) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if is_skipped(path, root):
            continue
        if path.is_file() and path.suffix.lower() == ".md":
            files.append(path)
        elif path.is_dir():
            for candidate in path.rglob("*.md"):
                if not is_skipped(candidate, root) and candidate.is_file():
                    files.append(candidate)
    return sorted(set(files))


def strip_ignored_markdown(text: str) -> str:
    """Blank fenced code blocks and inline code while preserving line numbers."""
    stripped_lines: list[str] = []
    fence_char = ""
    fence_len = 0
    for line in text.splitlines(keepends=True):
        match = FENCE_RE.match(line)
        if fence_char:
            if match and match.group(1).startswith(fence_char * fence_len):
                fence_char = ""
                fence_len = 0
            stripped_lines.append("\n" if line.endswith("\n") else "")
            continue
        if match:
            marker = match.group(1)
            fence_char = marker[0]
            fence_len = len(marker)
            stripped_lines.append("\n" if line.endswith("\n") else "")
            continue
        stripped_lines.append(INLINE_CODE_RE.sub("", line))
    return "".join(stripped_lines)


def link_targets(text: str) -> list[tuple[int, str]]:
    targets: list[tuple[int, str]] = []
    for regex in (INLINE_LINK_RE, REFERENCE_LINK_RE):
        for match in regex.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            targets.append((line, match.group(1)))
    return targets


def normalize_target(raw: str) -> str | None:
    target = raw.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()
    if not target or target.startswith("#"):
        return None
    if SCHEME_RE.match(target):
        return None
    if target.startswith("/"):
        return None

    target = target.split("#", 1)[0]
    target = unquote(target)
    if not target:
        return None
    if target in TEMPLATE_TARGETS:
        return None
    if any(marker in target for marker in ("{", "}", "$", "*", "<", ">")):
        return None
    return target


def check_file(path: Path) -> tuple[int, list[BrokenLink]]:
    text = strip_ignored_markdown(path.read_text(encoding="utf-8", errors="replace"))
    checked = 0
    broken: list[BrokenLink] = []
    for line, raw_target in link_targets(text):
        target = normalize_target(raw_target)
        if target is None:
            continue
        checked += 1
        if not (path.parent / target).exists():
            broken.append(BrokenLink(path=path, line=line, target=target))
    return checked, broken


def main() -> int:
    args = parse_args()
    root = Path(args.root)
    files = markdown_files(args.paths, root)
    checked = 0
    broken: list[BrokenLink] = []
    for path in files:
        file_checked, file_broken = check_file(path)
        checked += file_checked
        broken.extend(file_broken)

    if broken:
        print("check_markdown_links: broken local Markdown links:", file=sys.stderr)
        for item in broken:
            print(f"  {item.path}:{item.line}: {item.target}", file=sys.stderr)
        print(f"Checked {checked} local link(s) across {len(files)} Markdown file(s).", file=sys.stderr)
        return 1

    print(f"check_markdown_links: checked {checked} local link(s) across {len(files)} Markdown file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
