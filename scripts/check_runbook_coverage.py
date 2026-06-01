"""Helper for scripts/check-runbook-coverage.sh (#1698).

Reads rendered chart YAML from stdin, extracts every PrometheusRule
alert name, lowercases each (Prometheus markdown convention), and
verifies docs/runbooks.md has a matching `## <name>` H2 anchor. PVC
alerts share a heading like `## witwavepvcfillwarning /
witwavepvcfillcritical` so slash-separated multi-anchor headings are
accepted.

Exits 0 on full coverage, 1 on missing entries, 2 on environmental
issues.
"""

from __future__ import annotations

import os
import pathlib
import re
import sys


def _extract_alerts(rendered: str) -> list[str]:
    """Return every `alert:` name found in PrometheusRule docs."""
    try:
        import yaml
    except ImportError:
        # Fallback line walker for envs without PyYAML.
        return _extract_alerts_lines(rendered)

    alerts: list[str] = []
    for doc in yaml.safe_load_all(rendered):
        if not isinstance(doc, dict):
            continue
        if doc.get("kind") != "PrometheusRule":
            continue
        spec = doc.get("spec") or {}
        for grp in spec.get("groups") or []:
            for rule in grp.get("rules") or []:
                name = rule.get("alert")
                if isinstance(name, str) and name:
                    alerts.append(name)
    return alerts


def _extract_alerts_lines(rendered: str) -> list[str]:
    """Fallback line walker for environments without PyYAML."""
    alerts: list[str] = []
    in_rule = False
    for line in rendered.splitlines():
        if line.startswith("kind:"):
            in_rule = line.strip() == "kind: PrometheusRule"
            continue
        if not in_rule:
            continue
        m = re.match(r"\s*-\s*alert:\s*(\S+)", line)
        if m:
            alerts.append(m.group(1))
    return alerts


def _runbook_anchors(runbooks_path: pathlib.Path) -> set[str]:
    """Return the set of lowercase anchor strings from H2 headings.

    A heading like `## witwavepvcfillwarning / witwavepvcfillcritical`
    contributes both `witwavepvcfillwarning` and
    `witwavepvcfillcritical`."""
    anchors: set[str] = set()
    text = runbooks_path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if not m:
            continue
        # Strip backticks (Markdown heading style: `## \`name\``).
        raw = m.group(1).replace("`", "").strip()
        # Slash-separated multi-anchor heading.
        for part in raw.split("/"):
            anchors.add(part.strip().lower())
    return anchors


def main() -> int:
    rendered = sys.stdin.read()
    if not rendered:
        print("check_runbook_coverage: empty stdin", file=sys.stderr)
        return 2

    repo_root = pathlib.Path(os.environ.get("REPO_ROOT", "."))
    runbooks_path = repo_root / "docs" / "runbooks.md"
    if not runbooks_path.is_file():
        print(f"check_runbook_coverage: {runbooks_path} not found", file=sys.stderr)
        return 2

    alerts = _extract_alerts(rendered)
    if not alerts:
        print(
            "check_runbook_coverage: no alerts found in PrometheusRule",
            file=sys.stderr,
        )
        return 2

    anchors = _runbook_anchors(runbooks_path)
    missing = sorted({a for a in alerts if a.lower() not in anchors})
    if missing:
        print(
            "check_runbook_coverage: alerts without a `## <name>` heading in docs/runbooks.md:",
            file=sys.stderr,
        )
        for name in missing:
            print(f"  - {name}  (expected anchor: {name.lower()})", file=sys.stderr)
        print("", file=sys.stderr)
        print(
            f"Checked {len(alerts)} alert(s) against {len(anchors)} anchor(s).",
            file=sys.stderr,
        )
        return 1

    print(f"check_runbook_coverage: all {len(alerts)} alert(s) have a runbook anchor")
    return 0


if __name__ == "__main__":
    sys.exit(main())
