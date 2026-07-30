"""Specification required-key validation.

A file that merely parses as YAML is not a specification. Passing a scenario
file to --spec used to sail through validation and blow up later with a raw
KeyError on 'template_dir'; it must fail with an actionable message instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from llmdbenchmark.config import config
from llmdbenchmark.exceptions.exceptions import ConfigurationError
from llmdbenchmark.parser.render_specification import RenderSpecification

REPO_ROOT = Path(__file__).resolve().parents[1]
SCENARIO = REPO_ROOT / "config" / "scenarios" / "guides" / "nok8s.yaml"
SPECIFICATION = REPO_ROOT / "config" / "specification" / "guides" / "nok8s.yaml.j2"


def _workspace(tmp_path: Path) -> None:
    config.plan_dir = tmp_path
    config.log_dir = tmp_path


def test_scenario_passed_as_specification_is_rejected(tmp_path: Path) -> None:
    _workspace(tmp_path)

    with pytest.raises(ConfigurationError) as excinfo:
        RenderSpecification(specification_file=SCENARIO).eval()

    rendered = str(excinfo.value)
    for key in ("template_dir", "values_file", "scenario_file"):
        assert key in rendered
    assert "--spec guides/nok8s" in rendered


def test_empty_specification_is_rejected(tmp_path: Path) -> None:
    _workspace(tmp_path)

    empty = tmp_path / "empty.yaml.j2"
    empty.write_text("# nothing here\n", encoding="utf-8")

    with pytest.raises(ConfigurationError):
        RenderSpecification(specification_file=empty).eval()


def test_real_specification_still_validates(tmp_path: Path) -> None:
    _workspace(tmp_path)

    specification = RenderSpecification(
        specification_file=SPECIFICATION, base_dir=REPO_ROOT
    ).eval()

    for key in ("template_dir", "values_file", "scenario_file"):
        assert Path(specification[key]["path"]).exists()
