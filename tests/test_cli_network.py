"""``--network`` sets the Multus networks annotation on every serving pod.

It is a shortcut for a ``--set`` of ``k8s.v1.cni.cncf.io/networks`` in each
deploy method's pod annotations:
- modelservice: ``annotations.decode.pod`` and ``annotations.prefill.pod``
- standalone: ``standalone.podAnnotations``
- FMA: ``fma.launcher.podTemplate.metadata.annotations``

``--network none`` removes the annotation; omitting the flag keeps the
scenario's annotations.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from llmdbenchmark.parser.render_plans import RenderPlans
from llmdbenchmark.update import FLAG_KEYS
from llmdbenchmark.utilities.standup_parameters import INVOCATION_FIELDS

_ROOT = Path(__file__).resolve().parents[1]
_KEY = "k8s.v1.cni.cncf.io/networks"
_NETWORKS = "multi-nic-compute"


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


def _render(tmp_path: Path, overrides: dict, cli_network: str | None = None) -> Path:
    scenario = "pd-disaggregation"
    result = RenderPlans(
        template_dir=_ROOT / "config" / "templates" / "jinja",
        defaults_file=_ROOT / "config" / "templates" / "values" / "defaults.yaml",
        scenarios_file=_ROOT / "config" / "scenarios" / "guides" / f"{scenario}.yaml",
        output_dir=tmp_path / "plan",
        logger=_Logger(),
        setup_overrides=overrides,
        cli_network=cli_network,
    ).eval()
    assert not result.has_errors
    return tmp_path / "plan" / scenario


def _docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d]


def _config(plan: Path) -> dict:
    return _docs(plan / "config.yaml")[0]


def _annotation_maps(config: dict) -> list[dict]:
    return [
        config["annotations"]["decode"]["pod"],
        config["annotations"]["prefill"]["pod"],
        config["standalone"]["podAnnotations"],
        config["fma"]["launcher"]["podTemplate"]["metadata"]["annotations"],
    ]


def test_network_sets_annotation_for_every_deploy_method(tmp_path: Path) -> None:
    plan = _render(tmp_path, {}, cli_network=_NETWORKS)
    for annotation_map in _annotation_maps(_config(plan)):
        assert annotation_map[_KEY] == _NETWORKS


def test_network_reaches_modelservice_pods(tmp_path: Path) -> None:
    plan = _render(tmp_path, {}, cli_network=_NETWORKS)
    values = _docs(plan / "13_ms-values.yaml")[0]
    for role in ("decode", "prefill"):
        assert values[role]["podAnnotations"][_KEY] == _NETWORKS


def test_network_reaches_standalone_pods(tmp_path: Path) -> None:
    plan = _render(tmp_path, {"standalone": {"enabled": True}}, cli_network=_NETWORKS)
    deployment = next(
        d
        for d in _docs(plan / "14_standalone-deployment_yaml.yaml")
        if d["kind"] == "Deployment"
    )
    assert deployment["spec"]["template"]["metadata"]["annotations"][_KEY] == _NETWORKS


def test_network_keeps_other_annotations(tmp_path: Path) -> None:
    overrides = {"annotations": {"decode": {"pod": {"team": "perf"}}}}
    plan = _render(tmp_path, overrides, cli_network=_NETWORKS)
    decode = _config(plan)["annotations"]["decode"]["pod"]
    assert decode == {"team": "perf", _KEY: _NETWORKS}


def test_network_none_removes_annotation(tmp_path: Path) -> None:
    overrides = {
        "annotations": {
            "decode": {"pod": {_KEY: "old"}},
            "prefill": {"pod": {_KEY: "old"}},
        },
        "standalone": {"podAnnotations": {_KEY: "old"}},
    }
    plan = _render(tmp_path, overrides, cli_network="none")
    for annotation_map in _annotation_maps(_config(plan)):
        assert _KEY not in annotation_map


def test_network_omitted_keeps_scenario(tmp_path: Path) -> None:
    # pd-disaggregation already sets multi-nic-inference on decode/prefill.
    overrides = {"standalone": {"podAnnotations": {_KEY: "scenario-net"}}}
    plan = _render(tmp_path, overrides)
    config = _config(plan)
    assert config["standalone"]["podAnnotations"] == {_KEY: "scenario-net"}
    assert config["annotations"]["decode"]["pod"][_KEY] == "multi-nic-inference"


def test_network_overrides_scenario_value(tmp_path: Path) -> None:
    plan = _render(tmp_path, {}, cli_network=_NETWORKS)
    assert _config(plan)["annotations"]["decode"]["pod"][_KEY] == _NETWORKS


def test_network_is_an_annotations_change_for_update() -> None:
    assert FLAG_KEYS["network"] == "annotations"
    assert INVOCATION_FIELDS["invocation_network"] == "network"
