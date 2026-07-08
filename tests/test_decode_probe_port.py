"""Tests for decode probe port rendering."""

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


def _render_pd_disaggregation(tmp_path: Path) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    result = RenderPlans(
        template_dir=root / "config" / "templates" / "jinja",
        defaults_file=root / "config" / "templates" / "values" / "defaults.yaml",
        scenarios_file=(
            root / "config" / "scenarios" / "guides" / "pd-disaggregation.yaml"
        ),
        output_dir=tmp_path / "plan",
        logger=_Logger(),
    ).eval()

    assert not result.has_errors
    values_path = tmp_path / "plan" / "pd-disaggregation" / "13_ms-values.yaml"
    return yaml.safe_load(values_path.read_text(encoding="utf-8"))


def test_decode_probes_use_vllm_port_for_pd_disaggregation(tmp_path: Path) -> None:
    values = _render_pd_disaggregation(tmp_path)

    decode_container = values["decode"]["containers"][0]
    env = {
        item["name"]: item["value"]
        for item in decode_container["env"]
        if "value" in item
    }
    extra_config = decode_container["extraConfig"]

    assert env["VLLM_METRICS_PORT"] == "8200"
    assert extra_config["startupProbe"]["httpGet"]["port"] == 8200
    assert extra_config["livenessProbe"]["tcpSocket"]["port"] == 8200
    assert extra_config["readinessProbe"]["httpGet"]["port"] == 8200
