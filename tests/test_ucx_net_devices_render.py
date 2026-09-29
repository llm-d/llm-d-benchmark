"""Tests for ``vllmCommon.ucxNetDevices`` rendering into UCX_NET_DEVICES.

Pinning UCX to a device only matters once a pod has more than one interface,
which is exactly the case a ``k8s.v1.cni.cncf.io/networks`` annotation creates.
The knob must therefore be renderable, and must stay absent by default so that
single-interface pods keep letting UCX auto-select.

``wide-ep.yaml`` carries the default-absence case; ``pd-disaggregation.yaml``
covers the RDMA transport and the explicit-pin override.
"""

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


def _render(
    tmp_path: Path,
    overrides: dict | None = None,
    guide: str = "wide-ep",
) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    result = RenderPlans(
        template_dir=root / "config" / "templates" / "jinja",
        defaults_file=root / "config" / "templates" / "values" / "defaults.yaml",
        scenarios_file=(root / "config" / "scenarios" / "guides" / f"{guide}.yaml"),
        output_dir=tmp_path / "plan",
        logger=_Logger(),
        setup_overrides=overrides,
    ).eval()

    assert not result.has_errors
    values_path = tmp_path / "plan" / guide / "13_ms-values.yaml"
    return yaml.safe_load(values_path.read_text(encoding="utf-8"))


def _env(values: dict[str, Any], role: str) -> dict[str, str]:
    container = values[role]["containers"][0]
    return {item["name"]: item["value"] for item in container["env"] if "value" in item}


def test_ucx_net_devices_is_absent_by_default(tmp_path: Path) -> None:
    values = _render(tmp_path)

    for role in ("prefill", "decode"):
        env = _env(values, role)
        # The neighbouring UCX knobs do render, so absence here is the default
        # value taking effect rather than the whole block being skipped.
        assert "UCX_TLS" in env
        assert "UCX_NET_DEVICES" not in env


def test_ucx_net_devices_renders_when_set(tmp_path: Path) -> None:
    values = _render(tmp_path, {"vllmCommon": {"ucxNetDevices": "eth0"}})

    for role in ("prefill", "decode"):
        assert _env(values, role)["UCX_NET_DEVICES"] == "eth0"


def test_pd_disaggregation_enables_rdma_transport(tmp_path: Path) -> None:
    """The PD guide selects the RDMA transport and leaves the rail pin open.

    ``rc`` has to stay in UCX_TLS. The guide ships ``ucxNetDevices`` empty, so
    the preprocess init container picks the rails and no UCX_NET_DEVICES is
    rendered onto the containers.
    """
    values = _render(tmp_path, guide="pd-disaggregation")

    for role in ("prefill", "decode"):
        env = _env(values, role)

        assert "rc" in env["UCX_TLS"].split(",")
        assert "UCX_NET_DEVICES" not in env


def test_pd_disaggregation_pin_override_renders_in_hca_form(tmp_path: Path) -> None:
    """An explicit pin reaches both roles of the PD guide unchanged.

    Each rail stays in HCA form (``mlx5_1:1``) rather than netdev form
    (``eth0``), which is what the RDMA transport requires.
    """
    pin = "mlx5_1:1,mlx5_3:1"
    values = _render(
        tmp_path,
        {"vllmCommon": {"ucxNetDevices": pin}},
        guide="pd-disaggregation",
    )

    for role in ("prefill", "decode"):
        env = _env(values, role)

        assert env["UCX_NET_DEVICES"] == pin
        for rail in env["UCX_NET_DEVICES"].split(","):
            device, _, port = rail.partition(":")
            assert device.startswith("mlx5_"), f"{rail!r} is not an HCA device"
            assert port, f"{rail!r} is missing the :<port> suffix"


def _render_standalone(tmp_path: Path, overrides: dict | None = None) -> str:
    """Render the standalone deployment, whose UCX env lives in its own template."""
    root = Path(__file__).resolve().parents[1]
    setup_overrides: dict[str, Any] = {
        "modelservice": {"enabled": False},
        "standalone": {"enabled": True},
    }
    if overrides:
        setup_overrides.update(overrides)
    result = RenderPlans(
        template_dir=root / "config" / "templates" / "jinja",
        defaults_file=root / "config" / "templates" / "values" / "defaults.yaml",
        scenarios_file=root / "config" / "scenarios" / "examples" / "gpu.yaml",
        output_dir=tmp_path / "plan",
        logger=_Logger(),
        setup_overrides=setup_overrides,
    ).eval()

    assert not result.has_errors
    deployment = result.rendered_paths[0] / "14_standalone-deployment_yaml.yaml"
    text = deployment.read_text(encoding="utf-8")
    # Parsing guards the hand-indented env entries: a wrong indent would still
    # read fine as text but break the document.
    yaml.safe_load(text)
    return text


def test_standalone_omits_ucx_net_devices_by_default(tmp_path: Path) -> None:
    text = _render_standalone(tmp_path)

    assert "UCX_TLS" in text
    assert "UCX_NET_DEVICES" not in text


def test_standalone_renders_ucx_net_devices_when_set(tmp_path: Path) -> None:
    text = _render_standalone(tmp_path, {"vllmCommon": {"ucxNetDevices": "mlx5_1:1"}})

    deployment = yaml.safe_load(text)
    containers = deployment["spec"]["template"]["spec"]["containers"]
    env = {
        item["name"]: item["value"]
        for container in containers
        for item in container.get("env", [])
        if "value" in item
    }
    assert env["UCX_NET_DEVICES"] == "mlx5_1:1"
