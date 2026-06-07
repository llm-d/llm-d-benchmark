"""Tests for the ``llm-d-cicd:skip`` markers in the kustomize README parser.

These cover only the new skip-region behaviour; the rest of
``parse_guide_readme`` is exercised end-to-end in the kustomize standup
step's integration tests.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from llmdbenchmark.kustomize.readme_parser import (
    CommandPhase,
    parse_guide_readme,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_readme(tmp_path: Path, body: str) -> Path:
    """Write *body* to a fresh guide README and return its path."""
    guide_dir = tmp_path / "test-guide"
    guide_dir.mkdir()
    readme = guide_dir / "README.md"
    readme.write_text(textwrap.dedent(body), encoding="utf-8")
    return readme


# ---------------------------------------------------------------------------
# Skip-region tests
# ---------------------------------------------------------------------------


class TestSkipRegion:
    """The ``<!-- llm-d-cicd:skip start --> ... <!-- llm-d-cicd:skip end -->``
    markers should drop any bash blocks they enclose without affecting
    blocks outside the region."""

    def test_block_inside_skip_region_is_dropped(self, tmp_path: Path):
        readme = _write_readme(
            tmp_path,
            """\
            # Prerequisites

            <!-- llm-d-cicd:skip start -->
            ```bash
            kubectl create namespace skip-me
            ```
            <!-- llm-d-cicd:skip end -->
            """,
        )

        parsed = parse_guide_readme(readme)

        assert parsed.get_commands(CommandPhase.PREREQUISITES) == []

    def test_blocks_outside_region_are_kept(self, tmp_path: Path):
        readme = _write_readme(
            tmp_path,
            """\
            # Prerequisites

            ```bash
            kubectl apply -f keep-me-before.yaml
            ```

            <!-- llm-d-cicd:skip start -->
            ```bash
            kubectl apply -f skip-me.yaml
            ```
            <!-- llm-d-cicd:skip end -->

            ```bash
            kubectl apply -f keep-me-after.yaml
            ```
            """,
        )

        parsed = parse_guide_readme(readme)
        cmds = [c.raw for c in parsed.get_commands(CommandPhase.PREREQUISITES)]

        assert cmds == [
            "kubectl apply -f keep-me-before.yaml",
            "kubectl apply -f keep-me-after.yaml",
        ]

    def test_export_inside_skip_region_does_not_pollute_variables(self, tmp_path: Path):
        """The bug that motivated this feature: an ``export VAR=<placeholder>``
        inside a block we mean to skip must not leak into ``parsed.variables``.
        """
        readme = _write_readme(
            tmp_path,
            """\
            # Prerequisites

            <!-- llm-d-cicd:skip start -->
            ```bash
            export HF_TOKEN=<your HuggingFace token>
            kubectl create secret generic llm-d-hf-token \\
              --from-literal="HF_TOKEN=${HF_TOKEN}" \\
              --namespace "${NAMESPACE}" \\
              --dry-run=client -o yaml | kubectl apply -f -
            ```
            <!-- llm-d-cicd:skip end -->

            ```bash
            export KEEP_ME=ok
            kubectl apply -f resource.yaml
            ```
            """,
        )

        parsed = parse_guide_readme(readme)

        # The skipped export must not appear; the unskipped one must.
        assert "HF_TOKEN" not in parsed.variables
        assert parsed.variables.get("KEEP_ME") == "ok"
        # The skipped kubectl line must not be captured as a command.
        prereqs = [c.raw for c in parsed.get_commands(CommandPhase.PREREQUISITES)]
        assert prereqs == ["kubectl apply -f resource.yaml"]

    def test_markers_are_case_insensitive_and_whitespace_tolerant(self, tmp_path: Path):
        readme = _write_readme(
            tmp_path,
            """\
            # Prerequisites

            <!--LLM-D-CICD:Skip Start-->
            ```bash
            kubectl apply -f drop.yaml
            ```
            <!--   llm-d-cicd :  skip   end   -->

            ```bash
            kubectl apply -f keep.yaml
            ```
            """,
        )

        parsed = parse_guide_readme(readme)
        cmds = [c.raw for c in parsed.get_commands(CommandPhase.PREREQUISITES)]
        assert cmds == ["kubectl apply -f keep.yaml"]

    def test_skip_spans_multiple_blocks(self, tmp_path: Path):
        readme = _write_readme(
            tmp_path,
            """\
            # Prerequisites

            <!-- llm-d-cicd:skip start -->
            ```bash
            kubectl apply -f one.yaml
            ```

            Some prose between the blocks.

            ```bash
            kubectl apply -f two.yaml
            ```
            <!-- llm-d-cicd:skip end -->

            ```bash
            kubectl apply -f three.yaml
            ```
            """,
        )

        parsed = parse_guide_readme(readme)
        cmds = [c.raw for c in parsed.get_commands(CommandPhase.PREREQUISITES)]
        assert cmds == ["kubectl apply -f three.yaml"]

    def test_section_transitions_still_apply_inside_skip(self, tmp_path: Path):
        """Headings inside a skip region must still advance section state
        so that blocks after the region land in the correct phase."""
        readme = _write_readme(
            tmp_path,
            """\
            # Prerequisites

            ```bash
            kubectl apply -f prereq.yaml
            ```

            <!-- llm-d-cicd:skip start -->
            # Deploy the Model Server

            ```bash
            kubectl apply -k modelserver/skip-this
            ```
            <!-- llm-d-cicd:skip end -->

            ```bash
            kubectl apply -k modelserver/keep-this
            ```
            """,
        )

        parsed = parse_guide_readme(readme)
        prereqs = [c.raw for c in parsed.get_commands(CommandPhase.PREREQUISITES)]
        modelserver = [c.raw for c in parsed.get_commands(CommandPhase.MODELSERVER)]
        assert prereqs == ["kubectl apply -f prereq.yaml"]
        assert modelserver == ["kubectl apply -k modelserver/keep-this"]

    def test_unterminated_skip_drops_everything_after(self, tmp_path: Path):
        """An author who forgets the close marker should see the rest of
        the file ignored.  This is a defensive behaviour, not a feature
        to rely on -- the test pins it so future changes are deliberate."""
        readme = _write_readme(
            tmp_path,
            """\
            # Prerequisites

            ```bash
            kubectl apply -f keep.yaml
            ```

            <!-- llm-d-cicd:skip start -->
            ```bash
            kubectl apply -f drop.yaml
            ```
            """,
        )

        parsed = parse_guide_readme(readme)
        cmds = [c.raw for c in parsed.get_commands(CommandPhase.PREREQUISITES)]
        assert cmds == ["kubectl apply -f keep.yaml"]

    def test_marker_inside_code_fence_is_literal(self, tmp_path: Path):
        """A marker that appears between fence lines is part of the bash
        body, not a directive -- the block must still be captured."""
        readme = _write_readme(
            tmp_path,
            """\
            # Prerequisites

            ```bash
            kubectl apply -f keep.yaml
            # <!-- llm-d-cicd:skip start -->
            ```

            ```bash
            kubectl apply -f also-keep.yaml
            ```
            """,
        )

        parsed = parse_guide_readme(readme)
        cmds = [c.raw for c in parsed.get_commands(CommandPhase.PREREQUISITES)]
        assert cmds == [
            "kubectl apply -f keep.yaml",
            "kubectl apply -f also-keep.yaml",
        ]


# ---------------------------------------------------------------------------
# Regression coverage for a guide with no markers at all
# ---------------------------------------------------------------------------


def test_no_markers_behaves_identically(tmp_path: Path):
    """A guide that never uses the markers must parse the same way it
    did before this feature landed."""
    readme = _write_readme(
        tmp_path,
        """\
        # Prerequisites

        ```bash
        export NAMESPACE=demo
        kubectl create namespace ${NAMESPACE}
        ```

        # Deploy the Model Server

        ```bash
        kubectl apply -k modelserver/gpu/vllm
        ```
        """,
    )

    parsed = parse_guide_readme(readme)
    assert parsed.variables.get("NAMESPACE") == "demo"
    prereqs = [c.raw for c in parsed.get_commands(CommandPhase.PREREQUISITES)]
    modelserver = [c.raw for c in parsed.get_commands(CommandPhase.MODELSERVER)]
    assert prereqs == ["kubectl create namespace ${NAMESPACE}"]
    assert modelserver == ["kubectl apply -k modelserver/gpu/vllm"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
