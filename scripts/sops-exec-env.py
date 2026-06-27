#!/usr/bin/env python3
"""Run a command with one or more SOPS dotenv files loaded as env vars."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

KEY_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def usage() -> int:
    print(
        "usage: scripts/sops-exec-env.py <file.sops.env> [file.sops.env ...] -- <command> [args...]",
        file=sys.stderr,
    )
    return 2


def parse_dotenv(text: str, source: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_no, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        match = KEY_RE.match(raw)
        if not match:
            raise ValueError(f"{source}:{line_no}: expected KEY=value")
        values[match.group(1)] = match.group(2)
    return values


def decrypt_dotenv(path: str) -> dict[str, str]:
    proc = subprocess.run(
        [
            os.environ.get("SOPS_BIN", "sops"),
            "--input-type",
            "dotenv",
            "--output-type",
            "dotenv",
            "--decrypt",
            path,
        ],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"failed to decrypt {path}:\n{proc.stderr.rstrip()}")
    return parse_dotenv(proc.stdout, path)


def main(argv: list[str]) -> int:
    if "--" not in argv:
        return usage()

    split = argv.index("--")
    files = argv[:split]
    command = argv[split + 1 :]
    if not files or not command:
        return usage()

    env = os.environ.copy()
    for file_name in files:
        path = Path(file_name)
        if not path.is_file():
            print(f"missing SOPS env file: {file_name}", file=sys.stderr)
            return 1
        try:
            env.update(decrypt_dotenv(file_name))
        except (RuntimeError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1

    os.execvpe(command[0], command, env)
    return 127


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
