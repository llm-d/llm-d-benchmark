"""hostPath model storage mounts the node directory directly.

With ``storage.hostPath.enabled: true`` there is no model PV or PVC: the
download DaemonSet writes the weights into ``<hostPath.path>/<model.path>``
on every target node, and the serving pods mount ``hostPath.path`` as a
hostPath volume at the usual model mount point.

Behavior under test:
- The model PVC manifest renders empty; no static PV manifest is rendered.
- modelservice decode/prefill turn the chart's own model volume off and
  mount the node directory as ``model-storage`` at /model-cache.
- The standalone Deployment mounts the node directory instead of the PVC.
- The download DaemonSet reports Ready only once the node's copy is done,
  and the SCC binding covers every ServiceAccount in the namespace.
- ``--hostpath <dir>`` enables hostPath at <dir>, ``--hostpath none``
  disables it, and omitting the flag keeps the scenario's setting.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from llmdbenchmark.parser.render_plans import RenderPlans

_ROOT = Path(__file__).resolve().parents[1]
_HOSTPATH = {"storage": {"hostPath": {"enabled": True, "path": "/mnt/models"}}}


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


def _render(
    tmp_path: Path, scenario: str, overrides: dict, cli_hostpath: str | None = None
) -> Path:
    result = RenderPlans(
        template_dir=_ROOT / "config" / "templates" / "jinja",
        defaults_file=_ROOT / "config" / "templates" / "values" / "defaults.yaml",
        scenarios_file=_ROOT / "config" / "scenarios" / "guides" / f"{scenario}.yaml",
        output_dir=tmp_path / "plan",
        logger=_Logger(),
        setup_overrides=overrides,
        cli_hostpath=cli_hostpath,
    ).eval()
    assert not result.has_errors
    return tmp_path / "plan" / scenario


def _docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d]


def test_no_model_pv_or_pvc_is_rendered(tmp_path: Path) -> None:
    plan = _render(tmp_path, "pd-disaggregation", _HOSTPATH)
    assert _docs(plan / "02_pvc_model-pvc.yaml") == []
    assert not list(plan.glob("02a_*"))


def test_modelservice_mounts_node_directory(tmp_path: Path) -> None:
    plan = _render(tmp_path, "pd-disaggregation", _HOSTPATH)
    values = _docs(plan / "13_ms-values.yaml")[0]

    for role in ("decode", "prefill"):
        container = values[role]["containers"][0]
        assert container["mountModelVolume"] is False
        assert {
            "name": "model-storage",
            "mountPath": "/model-cache",
            "readOnly": True,
        } in container["volumeMounts"]
        assert {
            "name": "model-storage",
            "hostPath": {"path": "/mnt/models", "type": "Directory"},
        } in values[role]["volumes"]


def test_modelservice_without_hostpath_keeps_chart_pvc_volume(tmp_path: Path) -> None:
    plan = _render(tmp_path, "pd-disaggregation", {})
    values = _docs(plan / "13_ms-values.yaml")[0]

    for role in ("decode", "prefill"):
        assert values[role]["containers"][0]["mountModelVolume"] is True
        names = [v["name"] for v in values[role].get("volumes") or []]
        assert "model-storage" not in names
    assert _docs(plan / "02_pvc_model-pvc.yaml")[0]["kind"] == "PersistentVolumeClaim"


def test_standalone_mounts_node_directory(tmp_path: Path) -> None:
    overrides = {**_HOSTPATH, "standalone": {"enabled": True}}
    plan = _render(tmp_path, "pd-disaggregation", overrides)
    deployment = next(
        d
        for d in _docs(plan / "14_standalone-deployment_yaml.yaml")
        if d["kind"] == "Deployment"
    )
    pod = deployment["spec"]["template"]["spec"]
    mount = next(
        m for m in pod["containers"][0]["volumeMounts"] if m["name"] == "model-cache"
    )
    assert mount["readOnly"] is True
    volumes = {v["name"]: v for v in pod["volumes"]}
    assert volumes["model-cache"] == {
        "name": "model-cache",
        "hostPath": {"path": "/mnt/models", "type": "Directory"},
    }


def test_download_daemonset_ready_only_after_download(tmp_path: Path) -> None:
    plan = _render(tmp_path, "pd-disaggregation", _HOSTPATH)
    docs = _docs(plan / "03_download_daemonset.yaml")
    ds = next(d for d in docs if d["kind"] == "DaemonSet")
    container = ds["spec"]["template"]["spec"]["containers"][0]

    probe = " ".join(container["readinessProbe"]["exec"]["command"])
    assert "/mnt/models/${MODEL_PATH}/.download-complete" in probe
    env = {e["name"]: e.get("value") for e in container["env"]}
    assert env["MODEL_PATH"]

    binding = next(d for d in docs if d["kind"] == "RoleBinding")
    namespace = ds["metadata"]["namespace"]
    assert {
        "kind": "Group",
        "apiGroup": "rbac.authorization.k8s.io",
        "name": f"system:serviceaccounts:{namespace}",
    } in binding["subjects"]


def _storage(plan: Path) -> dict:
    return _docs(plan / "config.yaml")[0]["storage"]["hostPath"]


def test_cli_hostpath_enables_at_given_path(tmp_path: Path) -> None:
    plan = _render(tmp_path, "pd-disaggregation", {}, cli_hostpath="/var/lib/models")
    assert _storage(plan)["enabled"] is True
    assert _storage(plan)["path"] == "/var/lib/models"
    values = _docs(plan / "13_ms-values.yaml")[0]
    assert {
        "name": "model-storage",
        "hostPath": {"path": "/var/lib/models", "type": "Directory"},
    } in values["decode"]["volumes"]


def test_cli_hostpath_none_disables(tmp_path: Path) -> None:
    plan = _render(tmp_path, "pd-disaggregation", _HOSTPATH, cli_hostpath="none")
    assert _storage(plan)["enabled"] is False
    assert _docs(plan / "02_pvc_model-pvc.yaml")[0]["kind"] == "PersistentVolumeClaim"


def test_cli_hostpath_omitted_keeps_scenario(tmp_path: Path) -> None:
    plan = _render(tmp_path, "pd-disaggregation", _HOSTPATH)
    assert _storage(plan)["enabled"] is True
    assert _storage(plan)["path"] == "/mnt/models"
