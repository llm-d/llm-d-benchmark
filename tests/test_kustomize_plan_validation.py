"""Tests for kustomize validation during plan rendering."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from llmdbenchmark.parser.render_plans import RenderPlans


class _Logger:
    def log_info(self, *_: Any, **__: Any) -> None:
        pass

    def log_warning(self, *_: Any, **__: Any) -> None:
        pass

    def log_error(self, *_: Any, **__: Any) -> None:
        pass

    def log_debug(self, *_: Any, **__: Any) -> None:
        pass

    def line_break(self) -> None:
        pass


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def test_plan_reports_scalar_kustomize_patch(tmp_path: Path) -> None:
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    (template_dir / "00_dummy.yaml.j2").write_text(
        "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: dummy\n",
        encoding="utf-8",
    )
    defaults_file = tmp_path / "defaults.yaml"
    _write_yaml(defaults_file, {})
    scenario_file = tmp_path / "scenario.yaml"
    _write_yaml(
        scenario_file,
        {
            "scenario": [
                {
                    "name": "optimized-baseline",
                    "kustomize": {
                        "guideName": "optimized-baseline",
                        "patches": [
                            {
                                "patch": ('priorityClassName="nightly-gpu-critical"'),
                            }
                        ],
                    },
                }
            ]
        },
    )

    result = RenderPlans(
        template_dir=template_dir,
        defaults_file=defaults_file,
        scenarios_file=scenario_file,
        output_dir=tmp_path / "plan",
        logger=_Logger(),
        cli_methods="kustomize",
    ).eval()

    errors = result.stacks["optimized-baseline"].render_errors
    assert result.has_errors
    assert any("kustomize.patches[0].patch" in error for error in errors)
    assert any("expected YAML mapping" in error for error in errors)
