"""Tests for `export VAR=…` harvesting in the kustomize README parser.

Verifies that values are captured with proper bash quoting semantics — most
importantly that double-quoted values with spaces, `${VAR}` references, and
`$(cmd)` substitutions round-trip verbatim instead of being truncated at the
first whitespace/`$`.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from llmdbenchmark.kustomize.readme_parser import parse_guide_readme


def _write_readme(tmp_path: Path, body: str) -> Path:
    guide_dir = tmp_path / "test-guide"
    guide_dir.mkdir()
    readme = guide_dir / "README.md"
    readme.write_text(textwrap.dedent(body), encoding="utf-8")
    return readme


class TestUnquotedExports:
    """Bare values continue to work — this was the only shape the old regex
    supported and it must not regress."""

    def test_simple_value(self, tmp_path: Path):
        readme = _write_readme(
            tmp_path,
            """\
            # Prerequisites

            ```bash
            export NAMESPACE=demo
            ```
            """,
        )
        assert parse_guide_readme(readme).variables == {"NAMESPACE": "demo"}

    def test_path_value(self, tmp_path: Path):
        readme = _write_readme(
            tmp_path,
            """\
            # Prerequisites

            ```bash
            export WORKLOAD=guide_optimized-baseline_1.yaml
            ```
            """,
        )
        assert parse_guide_readme(readme).variables == {
            "WORKLOAD": "guide_optimized-baseline_1.yaml"
        }

    def test_empty_unquoted_value(self, tmp_path: Path):
        readme = _write_readme(
            tmp_path,
            """\
            # Prerequisites

            ```bash
            export MONITORING_VALUES=
            ```
            """,
        )
        assert parse_guide_readme(readme).variables == {"MONITORING_VALUES": ""}

    def test_trailing_comment_stripped(self, tmp_path: Path):
        readme = _write_readme(
            tmp_path,
            """\
            # Prerequisites

            ```bash
            export MODEL_SERVER=vllm # options: vllm, sglang, trtllm
            ```
            """,
        )
        assert parse_guide_readme(readme).variables == {"MODEL_SERVER": "vllm"}


class TestDoubleQuotedExports:
    """Double-quoted values were truncated by the old regex on spaces, `$`,
    and parentheses. They must now round-trip verbatim so the resolver can
    later substitute `${VAR}` refs and bash can expand `$(cmd)` at runtime."""

    def test_value_with_spaces(self, tmp_path: Path):
        readme = _write_readme(
            tmp_path,
            """\
            # Prerequisites

            ```bash
            export ROUTER_BASE_VALUES="-f ${REPO_ROOT}/guides/recipes/router/base.values.yaml"
            ```
            """,
        )
        assert parse_guide_readme(readme).variables == {
            "ROUTER_BASE_VALUES": "-f ${REPO_ROOT}/guides/recipes/router/base.values.yaml"
        }

    def test_empty_quoted_value(self, tmp_path: Path):
        readme = _write_readme(
            tmp_path,
            """\
            # Prerequisites

            ```bash
            export MONITORING_VALUES=""
            ```
            """,
        )
        assert parse_guide_readme(readme).variables == {"MONITORING_VALUES": ""}

    def test_value_with_command_substitution(self, tmp_path: Path):
        readme = _write_readme(
            tmp_path,
            """\
            # Prerequisites

            ```bash
            export ENDPOINT_URL="http://$(kubectl get service foo -o jsonpath='{.spec.clusterIP}')"
            ```
            """,
        )
        assert parse_guide_readme(readme).variables == {
            "ENDPOINT_URL": "http://$(kubectl get service foo -o jsonpath='{.spec.clusterIP}')"
        }

    def test_value_with_variable_reference(self, tmp_path: Path):
        readme = _write_readme(
            tmp_path,
            """\
            # Prerequisites

            ```bash
            export ROUTER_VALUES="-f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml"
            ```
            """,
        )
        assert parse_guide_readme(readme).variables == {
            "ROUTER_VALUES": "-f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml"
        }


class TestSingleQuotedExports:
    """Single-quoted values must be captured verbatim — bash treats them as
    fully literal (no expansion), but the parser only records the string."""

    def test_value_with_spaces(self, tmp_path: Path):
        readme = _write_readme(
            tmp_path,
            """\
            # Prerequisites

            ```bash
            export MESSAGE='hello world'
            ```
            """,
        )
        assert parse_guide_readme(readme).variables == {"MESSAGE": "hello world"}

    def test_value_with_double_quotes_inside(self, tmp_path: Path):
        readme = _write_readme(
            tmp_path,
            """\
            # Prerequisites

            ```bash
            export JSON='{"model": "qwen"}'
            ```
            """,
        )
        assert parse_guide_readme(readme).variables == {"JSON": '{"model": "qwen"}'}


class TestMalformedExports:
    """Malformed or non-export lines should not accidentally show up as
    harvested variables."""

    def test_line_without_export_prefix_is_skipped(self, tmp_path: Path):
        readme = _write_readme(
            tmp_path,
            """\
            # Prerequisites

            ```bash
            NAMESPACE=demo
            ```
            """,
        )
        assert "NAMESPACE" not in parse_guide_readme(readme).variables


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
