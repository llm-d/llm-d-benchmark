"""Keep prometheus-adapter chart pins aligned across tracked files."""

from __future__ import annotations

import re
from pathlib import Path

import yaml


_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULTS = _REPO_ROOT / "config" / "templates" / "values" / "defaults.yaml"


def _read_expected_version() -> str:
    defaults = yaml.safe_load(_DEFAULTS.read_text(encoding="utf-8"))
    return str(defaults["chartVersions"]["prometheusAdapter"])


def _extract_inline_version(path: Path, pattern: str) -> str:
    match = re.search(pattern, path.read_text(encoding="utf-8"), re.MULTILINE)
    assert match is not None, f"Could not find prometheus-adapter version in {path}"
    return match.group(1)


def test_prometheus_adapter_pin_matches_examples_and_docs() -> None:
    expected = _read_expected_version()

    assert _extract_inline_version(
        _REPO_ROOT / "config" / "scenarios" / "guides" / "multi-model-wva.yaml",
        r"^\s*prometheusAdapter:\s*([^\s#]+)",
    ) == expected
    assert _extract_inline_version(
        _REPO_ROOT / "config" / "scenarios" / "guides" / "inference-scheduling-wva.yaml",
        r"^\s*prometheusAdapter:\s*([^\s#]+)",
    ) == expected
    assert _extract_inline_version(
        _REPO_ROOT / "docs" / "workload-variant-autoscaler.md",
        r"^\s*prometheusAdapter:\s*([^\s#]+)",
    ) == expected
    assert _extract_inline_version(
        _REPO_ROOT / "docs" / "workload-variant-autoscaler.md",
        r"prometheus-adapter\s+v([0-9.]+),",
    ) == expected
