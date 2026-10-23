"""The pd-disaggregation experiment's standalone treatments deploy the TP they name.

The standalone Deployment reads ``standalone.parallelism.tensor``, not
``decode.parallelism.tensor``, so a treatment that sets only the decode key
renders the scenario's standalone default (TP=1) for every ``sa-d1xTP<N>``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from llmdbenchmark.experiment.parser import parse_experiment
from llmdbenchmark.parser.render_plans import RenderPlans

_ROOT = Path(__file__).resolve().parents[1]
_PLAN = parse_experiment(_ROOT / "experiments" / "pd-disaggregation.yaml")
_STANDALONE = [t for t in _PLAN.setup_treatments if t.name.startswith("sa-")]


class _Logger:
    def __getattr__(self, _name: str) -> Any:
        return lambda *_a, **_k: None


@pytest.mark.parametrize("treatment", _STANDALONE, ids=lambda t: t.name)
def test_standalone_treatment_renders_its_tensor_parallelism(tmp_path, treatment):
    expected_tp = re.search(r"TP(\d+)$", treatment.name).group(1)
    result = RenderPlans(
        template_dir=_ROOT / "config" / "templates" / "jinja",
        defaults_file=_ROOT / "config" / "templates" / "values" / "defaults.yaml",
        scenarios_file=_ROOT
        / "config"
        / "scenarios"
        / "guides"
        / "pd-disaggregation.yaml",
        output_dir=tmp_path,
        logger=_Logger(),
        setup_overrides=treatment.overrides,
    ).eval()
    assert not result.has_errors

    manifest = tmp_path / "pd-disaggregation" / "14_standalone-deployment_yaml.yaml"
    deployment = next(d for d in yaml.safe_load_all(manifest.read_text()) if d)
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    env = {e["name"]: e.get("value") for e in container["env"]}
    assert env["VLLM_TENSOR_PARALLELISM"] == expected_tp
