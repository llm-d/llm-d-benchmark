"""Tests for the nok8s (no-Kubernetes) deployment method.

Covers render-time gating (templates + config.yaml flags) and the step
should_skip selection logic, without needing a cluster or GPUs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from llmdbenchmark.parser.render_plans import RenderPlans

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = REPO_ROOT / "config" / "templates" / "jinja"
DEFAULTS_FILE = REPO_ROOT / "config" / "templates" / "values" / "defaults.yaml"
NOK8S_SCENARIO = REPO_ROOT / "config" / "scenarios" / "guides" / "nok8s.yaml"


class _Logger:
    def log_info(self, *_: Any, **__: Any) -> None:
        pass

    log_warning = log_error = log_debug = log_info

    def line_break(self) -> None:
        pass


def _render(tmp_path: Path, cli_methods: str | None = None, version_resolver=None):
    return RenderPlans(
        template_dir=TEMPLATE_DIR,
        defaults_file=DEFAULTS_FILE,
        scenarios_file=NOK8S_SCENARIO,
        output_dir=tmp_path / "plan",
        logger=_Logger(),
        cli_methods=cli_methods,
        version_resolver=version_resolver,
    ).eval()


def _stack_dir(result) -> Path:
    paths = getattr(result, "rendered_paths", None) or []
    assert paths, "no rendered stacks produced"
    return Path(paths[0])


def test_nok8s_scenario_renders_templates_and_flags(tmp_path: Path) -> None:
    stack = _stack_dir(_render(tmp_path))

    # config.yaml: nok8s enabled, the other methods disabled.
    cfg = yaml.safe_load((stack / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["nok8s"]["enabled"] is True
    assert cfg["standalone"]["enabled"] is False
    assert cfg["modelservice"]["enabled"] is False
    assert cfg["kustomize"]["enabled"] is False
    assert cfg["fma"]["enabled"] is False
    assert cfg["images"]["routerEndpointPicker"]["tag"] == "v0.10.0"
    assert cfg["nok8s"]["epp"]["tag"] == "v0.10.0"

    # All four nok8s artifacts rendered with content.
    for prefix in (
        "31_nok8s-epp-config",
        "32_nok8s-epp-endpoints",
        "33_nok8s-envoy",
        "34_nok8s-containers",
    ):
        matches = list(stack.glob(f"{prefix}*"))
        assert matches, f"missing rendered file for {prefix}"
        assert matches[0].read_text(encoding="utf-8").strip(), f"{prefix} is empty"

    # endpoints file lists the single worker with the model label.
    endpoints = yaml.safe_load(
        next(stack.glob("32_nok8s-epp-endpoints*")).read_text(encoding="utf-8")
    )
    assert endpoints["endpoints"][0]["address"] == "127.0.0.1"
    assert endpoints["endpoints"][0]["port"] == "8000"
    assert endpoints["endpoints"][0]["labels"]["model"] == "Qwen/Qwen2.5-0.5B-Instruct"

    # container launch spec exposes the local endpoint.
    spec = yaml.safe_load(
        next(stack.glob("34_nok8s-containers*")).read_text(encoding="utf-8")
    )
    assert spec["endpoint"] == "http://localhost:8081"
    kinds = sorted(c["kind"] for c in spec["containers"])
    assert kinds == ["envoy", "epp", "vllm"]


def test_should_skip_selects_by_method() -> None:
    from llmdbenchmark.standup.steps.step_06_nok8s_deploy import NoK8sDeployStep
    from llmdbenchmark.run.steps.step_07_deploy_harness_local import (
        DeployHarnessLocalStep,
    )
    from llmdbenchmark.executor.context import ExecutionContext

    nok8s_ctx = ExecutionContext(
        plan_dir=Path("/tmp"), workspace=Path("/tmp"), deployed_methods=["nok8s"]
    )
    other_ctx = ExecutionContext(
        plan_dir=Path("/tmp"), workspace=Path("/tmp"), deployed_methods=["standalone"]
    )

    assert NoK8sDeployStep().should_skip(nok8s_ctx) is False
    assert NoK8sDeployStep().should_skip(other_ctx) is True

    assert DeployHarnessLocalStep().should_skip(other_ctx) is True
    assert DeployHarnessLocalStep().should_skip(nok8s_ctx) is False


class _FakeResult:
    def __init__(self, success: bool, stdout: str = "") -> None:
        self.success = success
        self.stdout = stdout
        self.stderr = ""
        self.exit_code = 0 if success else 1


class _FakeCmd:
    """Maps command substrings to success/failure for preflight testing."""

    def __init__(self, fail_substrings=(), stdout_for=None) -> None:
        self.fail_substrings = fail_substrings
        self.stdout_for = stdout_for or {}

    def execute(self, cmd, *_, **__):
        ok = not any(s in cmd for s in self.fail_substrings)
        out = ""
        for key, val in self.stdout_for.items():
            if key in cmd:
                out = val
        return _FakeResult(ok, out)


class _CapturingLogger(_Logger):
    """Logger that keeps the warning lines the preflight emits."""

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def log_warning(self, msg: str, *_: Any, **__: Any) -> None:
        self.warnings.append(msg)


def _nok8s_ctx(tmp_path: Path, cmd, nok8s: dict | None = None):
    from llmdbenchmark.executor.context import ExecutionContext

    ctx = ExecutionContext(
        plan_dir=tmp_path,
        workspace=tmp_path,
        deployed_methods=["nok8s"],
        container_only=True,
        container_runtime="docker",
    )
    ctx.cmd = cmd
    ctx.logger = _CapturingLogger()
    if nok8s is not None:
        stack = tmp_path / "stack"
        stack.mkdir(exist_ok=True)
        (stack / "config.yaml").write_text(
            yaml.safe_dump({"nok8s": nok8s}), encoding="utf-8"
        )
        ctx.rendered_stacks = [stack]
    return ctx


def test_nok8s_preflight_fatal_when_runtime_missing(tmp_path: Path) -> None:
    from llmdbenchmark.standup.steps.step_00_ensure_infra import EnsureInfraStep

    ctx = _nok8s_ctx(tmp_path, _FakeCmd(fail_substrings=("command -v docker",)))
    result = EnsureInfraStep().execute(ctx)
    assert result.success is False
    assert any("runtime" in e.lower() for e in result.errors)


def test_nok8s_preflight_passes_when_runtime_and_gpu_present(tmp_path: Path) -> None:
    from llmdbenchmark.standup.steps.step_00_ensure_infra import EnsureInfraStep

    # Everything succeeds; ss reports no listeners -> no busy ports.
    ctx = _nok8s_ctx(tmp_path, _FakeCmd(stdout_for={"ss -ltn": "State  Recv-Q\n"}))
    import os

    os.environ["HUGGING_FACE_HUB_TOKEN"] = "hf_test"
    try:
        result = EnsureInfraStep().execute(ctx)
    finally:
        os.environ.pop("HUGGING_FACE_HUB_TOKEN", None)
    assert result.success is True


def _busy_warning(ctx) -> str:
    return next((w for w in ctx.logger.warnings if "already in use" in w), "")


def test_nok8s_preflight_checks_every_replica_port(tmp_path: Path) -> None:
    """Replicas occupy hostPort..hostPort+N-1, not just hostPort."""
    from llmdbenchmark.standup.steps.step_00_ensure_infra import EnsureInfraStep

    ss_out = "LISTEN 0 4096 0.0.0.0:8002 0.0.0.0:*\n"
    ctx = _nok8s_ctx(
        tmp_path,
        _FakeCmd(stdout_for={"ss -ltn": ss_out}),
        nok8s={"vllm": {"replicas": 3, "hostPort": 8000, "accelerator": "cpu"}},
    )
    EnsureInfraStep().execute(ctx)
    assert "8002" in _busy_warning(ctx)


def test_nok8s_preflight_checks_envoy_admin_port(tmp_path: Path) -> None:
    """19000 is hard-coded in the Envoy bootstrap and must be free."""
    from llmdbenchmark.standup.steps.step_00_ensure_infra import EnsureInfraStep

    ss_out = "LISTEN 0 4096 127.0.0.1:19000 0.0.0.0:*\n"
    ctx = _nok8s_ctx(
        tmp_path,
        _FakeCmd(stdout_for={"ss -ltn": ss_out}),
        nok8s={"vllm": {"accelerator": "cpu"}},
    )
    EnsureInfraStep().execute(ctx)
    assert "19000" in _busy_warning(ctx)


def test_nok8s_preflight_falls_back_to_lsof(tmp_path: Path) -> None:
    """Hosts without iproute2 (macOS) still get a real port check."""
    from llmdbenchmark.standup.steps.step_00_ensure_infra import EnsureInfraStep

    lsof_out = "envoy 42 u 10u IPv4 0t0 TCP 127.0.0.1:8081 (LISTEN)\n"
    ctx = _nok8s_ctx(
        tmp_path,
        _FakeCmd(
            fail_substrings=("command -v ss",),
            stdout_for={"lsof": lsof_out},
        ),
        nok8s={"vllm": {"accelerator": "cpu"}},
    )
    EnsureInfraStep().execute(ctx)
    assert "8081" in _busy_warning(ctx)


def test_nok8s_preflight_warns_when_no_port_probe_available(tmp_path: Path) -> None:
    """Neither ss nor lsof present -> say so instead of silently passing."""
    from llmdbenchmark.standup.steps.step_00_ensure_infra import EnsureInfraStep

    ctx = _nok8s_ctx(
        tmp_path,
        _FakeCmd(fail_substrings=("command -v ss", "command -v lsof")),
        nok8s={"vllm": {"accelerator": "cpu"}},
    )
    EnsureInfraStep().execute(ctx)
    assert any("Cannot verify host ports" in w for w in ctx.logger.warnings)


# Scenario YAML is not type-validated, so any of these fields can arrive as a
# string (a quoted port), None (a key with no value), or something stranger.
# The preflight is warnings-only: it must never abort standup on a bad value.

_STRING_PORT_CASES = (
    ({"envoy": {"listenPort": "8081"}}, 8081),
    ({"envoy": {"adminPort": "19000"}}, 19000),
    ({"epp": {"grpcPort": "9002"}}, 9002),
    ({"epp": {"grpcHealthPort": "9003"}}, 9003),
    ({"epp": {"metricsPort": "9090"}}, 9090),
    ({"vllm": {"hostPort": "8000"}}, 8000),
)


@pytest.mark.parametrize("override,port", _STRING_PORT_CASES)
def test_nok8s_preflight_accepts_quoted_ports(
    tmp_path: Path, override: dict, port: int
) -> None:
    """A quoted port is still checked, not a TypeError from sorted()."""
    from llmdbenchmark.standup.steps.step_00_ensure_infra import EnsureInfraStep

    nok8s = {"vllm": {"accelerator": "cpu"}}
    for section, fields in override.items():
        nok8s.setdefault(section, {}).update(fields)

    ss_out = f"LISTEN 0 4096 0.0.0.0:{port} 0.0.0.0:*\n"
    ctx = _nok8s_ctx(tmp_path, _FakeCmd(stdout_for={"ss -ltn": ss_out}), nok8s=nok8s)
    result = EnsureInfraStep().execute(ctx)
    assert result.success is True
    assert str(port) in _busy_warning(ctx)


@pytest.mark.parametrize("value", ["auto", "", True, [8081], {"a": 1}, 8.5])
def test_nok8s_preflight_reports_unusable_port_instead_of_crashing(
    tmp_path: Path, value
) -> None:
    """A value that is not a port is named as unchecked, not raised."""
    from llmdbenchmark.standup.steps.step_00_ensure_infra import EnsureInfraStep

    ctx = _nok8s_ctx(
        tmp_path,
        _FakeCmd(stdout_for={"ss -ltn": "State  Recv-Q\n"}),
        nok8s={"vllm": {"accelerator": "cpu"}, "envoy": {"listenPort": value}},
    )
    result = EnsureInfraStep().execute(ctx)
    assert result.success is True
    assert any(
        "nok8s.envoy.listenPort" in w and "not a whole number" in w.lower()
        for w in ctx.logger.warnings
    ), ctx.logger.warnings


def test_nok8s_preflight_treats_empty_port_as_default(tmp_path: Path) -> None:
    """`listenPort:` with no value renders as the Jinja default, so check that."""
    from llmdbenchmark.standup.steps.step_00_ensure_infra import EnsureInfraStep

    ss_out = "LISTEN 0 4096 0.0.0.0:8081 0.0.0.0:*\n"
    ctx = _nok8s_ctx(
        tmp_path,
        _FakeCmd(stdout_for={"ss -ltn": ss_out}),
        nok8s={"vllm": {"accelerator": "cpu"}, "envoy": {"listenPort": None}},
    )
    result = EnsureInfraStep().execute(ctx)
    assert result.success is True
    assert "8081" in _busy_warning(ctx)


@pytest.mark.parametrize("replicas", ["3", "abc", None, 0, -1, True])
def test_nok8s_preflight_survives_any_replicas_value(tmp_path: Path, replicas) -> None:
    """replicas feeds the port span and the GPU-capacity math; neither may raise."""
    from llmdbenchmark.standup.steps.step_00_ensure_infra import EnsureInfraStep

    ctx = _nok8s_ctx(
        tmp_path,
        _FakeCmd(stdout_for={"ss -ltn": "State  Recv-Q\n"}),
        nok8s={"vllm": {"accelerator": "nvidia", "replicas": replicas}},
    )
    result = EnsureInfraStep().execute(ctx)
    assert result.success is True


def test_nok8s_preflight_survives_non_numeric_tensor_parallel(tmp_path: Path) -> None:
    """tensorParallel multiplies replicas in the GPU-capacity check."""
    from llmdbenchmark.standup.steps.step_00_ensure_infra import EnsureInfraStep

    ctx = _nok8s_ctx(
        tmp_path,
        _FakeCmd(stdout_for={"ss -ltn": "State  Recv-Q\n"}),
        nok8s={
            "vllm": {"accelerator": "nvidia", "replicas": 2, "tensorParallel": "abc"}
        },
    )
    result = EnsureInfraStep().execute(ctx)
    assert result.success is True


def test_nok8s_preflight_quoted_replicas_still_spans_replica_ports(
    tmp_path: Path,
) -> None:
    """replicas: "3" must still claim hostPort..hostPort+2, like the int does."""
    from llmdbenchmark.standup.steps.step_00_ensure_infra import EnsureInfraStep

    ss_out = "LISTEN 0 4096 0.0.0.0:8002 0.0.0.0:*\n"
    ctx = _nok8s_ctx(
        tmp_path,
        _FakeCmd(stdout_for={"ss -ltn": ss_out}),
        nok8s={"vllm": {"replicas": "3", "hostPort": 8000, "accelerator": "cpu"}},
    )
    EnsureInfraStep().execute(ctx)
    assert "8002" in _busy_warning(ctx)


def test_device_args_per_accelerator() -> None:
    from llmdbenchmark.standup.steps.step_06_nok8s_deploy import NoK8sDeployStep

    dev = NoK8sDeployStep._device_args
    assert dev("docker", {"accelerator": "nvidia", "gpus": "all"}) == "--gpus all"
    assert (
        dev("podman", {"accelerator": "nvidia", "gpus": "all"})
        == "--device nvidia.com/gpu=all"
    )
    assert "/dev/kfd" in dev("docker", {"accelerator": "amd"})
    assert dev("docker", {"accelerator": "intel"}) == "--device /dev/dri"
    assert "habana" in dev("docker", {"accelerator": "gaudi"})
    assert dev("docker", {"accelerator": "cpu"}) == ""
    # spyre has no preset -> expects deviceArgs; empty without it.
    assert dev("docker", {"accelerator": "spyre"}) == ""
    # raw deviceArgs override wins regardless of accelerator.
    assert (
        dev(
            "docker",
            {"accelerator": "spyre", "deviceArgs": ["--device", "/dev/vfio/1"]},
        )
        == "--device /dev/vfio/1"
    )


def test_pin_env_per_replica() -> None:
    from llmdbenchmark.standup.steps.step_06_nok8s_deploy import NoK8sDeployStep

    pin = NoK8sDeployStep._pin_env
    # Single replica -> no pinning (back-compat, uses --gpus all).
    assert pin({"replicas": 1, "accelerator": "nvidia"}) == ""
    # 3 replicas, TP=1 -> one GPU each, distinct indices.
    assert pin({"replicas": 3, "replicaIndex": 0, "tensorParallel": 1}) == (
        "-e CUDA_VISIBLE_DEVICES=0"
    )
    assert pin({"replicas": 3, "replicaIndex": 2, "tensorParallel": 1}) == (
        "-e CUDA_VISIBLE_DEVICES=2"
    )
    # 2 replicas, TP=4 -> contiguous 4-GPU slices.
    assert pin({"replicas": 2, "replicaIndex": 1, "tensorParallel": 4}) == (
        "-e CUDA_VISIBLE_DEVICES=4,5,6,7"
    )
    # accelerator-specific env var.
    assert pin({"replicas": 2, "replicaIndex": 1, "accelerator": "amd"}) == (
        "-e HIP_VISIBLE_DEVICES=1"
    )
    assert pin({"replicas": 2, "replicaIndex": 0, "accelerator": "intel"}) == (
        "-e ZE_AFFINITY_MASK=0"
    )
    # gaudi/spyre/cpu -> no index-pinning env.
    assert pin({"replicas": 2, "replicaIndex": 0, "accelerator": "gaudi"}) == ""
    # deviceArgs override disables auto-pinning (caller controls devices).
    assert (
        pin({"replicas": 2, "replicaIndex": 1, "deviceArgs": ["--device", "x"]}) == ""
    )


class _RecordingCmd(_FakeCmd):
    """_FakeCmd that records every command it was asked to run."""

    def __init__(self, fail_substrings=(), stdout_for=None) -> None:
        super().__init__(fail_substrings, stdout_for)
        self.commands: list[str] = []

    def execute(self, cmd, *args, **kwargs):
        self.commands.append(cmd)
        return super().execute(cmd, *args, **kwargs)

    def removed(self) -> set[str]:
        return {c.split()[-1] for c in self.commands if " rm -f " in c}

    def launched(self) -> set[str]:
        return {
            c.split("--name ")[1].split()[0] for c in self.commands if "--name " in c
        }


def _nok8s_stack(tmp_path: Path) -> Path:
    """Minimal rendered stack dir with a three-container launch spec."""
    stack = tmp_path / "stack"
    stack.mkdir()
    spec = {
        "runtime": "docker",
        "workspaceHostDir": str(tmp_path / "ws"),
        "model": "Qwen/Qwen2.5-0.5B-Instruct",
        "endpoint": "http://localhost:8081",
        "containers": [
            {
                "name": "vllm-0",
                "kind": "vllm",
                "image": "nonexistent:v0",
                "hostPort": 8000,
            },
            {
                "name": "epp",
                "kind": "epp",
                "image": "epp:v0",
                "grpcPort": 9002,
                "grpcHealthPort": 9003,
                "metricsPort": 9090,
            },
            {"name": "envoy", "kind": "envoy", "image": "envoy:v0"},
        ],
    }
    (stack / "34_nok8s-containers.yaml").write_text(yaml.safe_dump(spec), "utf-8")
    return stack


def test_nok8s_launch_failure_stops_and_rolls_back(tmp_path: Path) -> None:
    """A container that fails to start aborts the launch and removes the rest."""
    from llmdbenchmark.standup.steps.step_06_nok8s_deploy import NoK8sDeployStep

    stack = _nok8s_stack(tmp_path)
    # vllm-0 is launched first and fails.
    cmd = _RecordingCmd(fail_substrings=("--name vllm-0",))
    ctx = _nok8s_ctx(tmp_path, cmd)

    result = NoK8sDeployStep().execute(ctx, stack)

    assert result.success is False
    assert "vllm-0" in result.message
    # Nothing after the failing container is launched.
    assert not any("run -d --name epp" in c for c in cmd.commands)
    assert not any("run -d --name envoy" in c for c in cmd.commands)


def test_nok8s_rollback_dumps_logs_before_removing(tmp_path: Path) -> None:
    """Already-launched containers are removed, with logs captured first."""
    from llmdbenchmark.standup.steps.step_06_nok8s_deploy import NoK8sDeployStep

    stack = _nok8s_stack(tmp_path)
    # vllm-0 and epp come up; envoy (launched last) fails.
    cmd = _RecordingCmd(fail_substrings=("--name envoy",))
    ctx = _nok8s_ctx(tmp_path, cmd)

    result = NoK8sDeployStep().execute(ctx, stack)

    assert result.success is False
    for name in ("vllm-0", "epp"):
        logs = cmd.commands.index(f"docker logs {name} --tail 100")
        # rm -f appears twice per container: the idempotency wipe before the
        # launch, and the rollback afterwards. The rollback one must come after
        # the log dump, or the evidence is gone.
        removals = [
            i for i, c in enumerate(cmd.commands) if c == f"docker rm -f {name}"
        ]
        assert len(removals) == 2, cmd.commands
        assert removals[-1] > logs, f"{name} removed before its logs were dumped"
        assert (ctx.setup_logs_dir() / f"nok8s-{name}.log").exists()


def test_resolve_deploy_method_forces_nok8s() -> None:
    """--methods nok8s wins and disables the other methods (mutual exclusion)."""
    rp = RenderPlans(
        template_dir=TEMPLATE_DIR,
        defaults_file=DEFAULTS_FILE,
        scenarios_file=NOK8S_SCENARIO,
        output_dir=Path("/tmp/unused-nok8s-plan"),
        logger=_Logger(),
        cli_methods="nok8s",
    )
    out = rp._resolve_deploy_method(
        {"standalone": {"enabled": True}, "modelservice": {"enabled": True}}
    )
    assert out["nok8s"]["enabled"] is True
    assert out["standalone"]["enabled"] is False
    assert out["modelservice"]["enabled"] is False


# ---------------------------------------------------------------------- #
# Multiple nok8s stacks on one host (issue #1699)
# ---------------------------------------------------------------------- #
SECOND_STACK_PORTS = {
    ("vllm", "hostPort"): 8100,
    ("epp", "grpcPort"): 9102,
    ("epp", "grpcHealthPort"): 9103,
    ("epp", "metricsPort"): 9190,
    ("envoy", "listenPort"): 8181,
    ("envoy", "adminPort"): 19100,
}


def _two_stack_scenario(
    tmp_path: Path,
    distinct_ports: bool,
    names: tuple[str, str] = ("nok8s-single", "nok8s-second"),
    both_nok8s: dict | None = None,
) -> Path:
    """Duplicate the shipped single-stack nok8s scenario into a two-stack one."""
    import copy

    doc = yaml.safe_load(NOK8S_SCENARIO.read_text(encoding="utf-8"))
    first = doc["scenario"][0]
    second = copy.deepcopy(first)
    first["name"], second["name"] = names
    if distinct_ports:
        for (section, key), port in SECOND_STACK_PORTS.items():
            second["nok8s"].setdefault(section, {})[key] = port
    for stack in (first, second):
        stack["nok8s"].update(copy.deepcopy(both_nok8s or {}))
    doc["scenario"] = [first, second]

    path = tmp_path / f"two-stack-{'ok' if distinct_ports else 'clash'}.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return path


def _render_scenario(tmp_path: Path, scenario_file: Path, out_name: str = "plan"):
    return RenderPlans(
        template_dir=TEMPLATE_DIR,
        defaults_file=DEFAULTS_FILE,
        scenarios_file=scenario_file,
        output_dir=tmp_path / out_name,
        logger=_Logger(),
    ).eval()


def _spec_of(stack_dir: Path) -> dict:
    return yaml.safe_load(
        next(stack_dir.glob("34_nok8s-containers*")).read_text(encoding="utf-8")
    )


def test_multi_stack_nok8s_identities_are_disjoint(tmp_path: Path) -> None:
    """Two nok8s stacks must not share names, workspace, endpoint or admin port."""
    result = _render_scenario(tmp_path, _two_stack_scenario(tmp_path, True))
    assert not result.has_errors, result.to_dict()
    first, second = (Path(p) for p in result.rendered_paths)

    spec_a, spec_b = _spec_of(first), _spec_of(second)

    names_a = {c["name"] for c in spec_a["containers"]}
    names_b = {c["name"] for c in spec_b["containers"]}
    assert names_a == {"vllm-0-nok8s-single", "epp-nok8s-single", "envoy-nok8s-single"}
    assert names_b == {"vllm-0-nok8s-second", "epp-nok8s-second", "envoy-nok8s-second"}
    assert not names_a & names_b

    assert spec_a["workspaceHostDir"] != spec_b["workspaceHostDir"]
    assert spec_a["workspaceHostDir"].endswith("/nok8s-single")
    assert spec_b["workspaceHostDir"].endswith("/nok8s-second")

    assert spec_a["endpoint"] == "http://localhost:8081"
    assert spec_b["endpoint"] == "http://localhost:8181"

    # Envoy runs --network host: the admin ports must differ too.
    admin_a = yaml.safe_load(
        next(first.glob("33_nok8s-envoy*")).read_text(encoding="utf-8")
    )["admin"]["address"]["socket_address"]["port_value"]
    admin_b = yaml.safe_load(
        next(second.glob("33_nok8s-envoy*")).read_text(encoding="utf-8")
    )["admin"]["address"]["socket_address"]["port_value"]
    assert (admin_a, admin_b) == (19000, 19100)


def test_single_stack_nok8s_names_stay_unsuffixed(tmp_path: Path) -> None:
    """Back-compat: one stack keeps vllm-0 / epp / envoy and the shared workspace."""
    spec = _spec_of(_stack_dir(_render(tmp_path)))
    assert {c["name"] for c in spec["containers"]} == {"vllm-0", "epp", "envoy"}
    assert spec["workspaceHostDir"] == "~/.llmdbench/nok8s"


def test_nok8s_port_collision_is_a_render_error(tmp_path: Path) -> None:
    """Two nok8s stacks on the same host ports fail the render, not the standup."""
    result = _render_scenario(tmp_path, _two_stack_scenario(tmp_path, False))
    assert result.has_errors

    errors = result.stacks["nok8s-second"].render_errors
    assert any(
        "8081" in e and "nok8s.envoy.listenPort" in e and "nok8s-single" in e
        for e in errors
    ), errors
    # The clashing stack is not rendered, so nothing downstream can launch it.
    assert [Path(p).name for p in result.rendered_paths] == ["nok8s-single"]


def test_nok8s_container_name_collision_is_a_render_error(tmp_path: Path) -> None:
    """Stack names that differ only by punctuation slug to one container name."""
    result = _render_scenario(
        tmp_path, _two_stack_scenario(tmp_path, True, names=("chat one", "chat-one"))
    )
    assert result.has_errors

    errors = result.stacks["chat-one"].render_errors
    assert any("epp-chat-one" in e and "chat one" in e for e in errors), errors
    assert [Path(p).name for p in result.rendered_paths] == ["chat one"]


def test_shared_nok8s_name_suffix_is_a_render_error(tmp_path: Path) -> None:
    """An explicit nameSuffix on both stacks puts them back on one identity."""
    result = _render_scenario(
        tmp_path,
        _two_stack_scenario(tmp_path, True, both_nok8s={"nameSuffix": "-shared"}),
    )
    assert result.has_errors

    errors = result.stacks["nok8s-second"].render_errors
    assert any("envoy-shared" in e and "nok8s-single" in e for e in errors), errors


def _validator() -> RenderPlans:
    return RenderPlans(
        template_dir=TEMPLATE_DIR,
        defaults_file=DEFAULTS_FILE,
        scenarios_file=NOK8S_SCENARIO,
        output_dir=Path("/tmp/unused-nok8s-plan"),
        logger=_Logger(),
    )


def test_nok8s_one_stack_claiming_a_port_twice_is_a_render_error() -> None:
    """Envoy on the worker's port would fail to bind, silently, at standup."""
    errors = _validator()._validate_nok8s_host_claims(
        {
            "nok8s": {
                "enabled": True,
                "vllm": {"hostPort": 8000},
                "envoy": {"listenPort": 8000},
            }
        },
        "solo",
    )
    assert any(
        "8000" in e and "nok8s.vllm.hostPort" in e and "nok8s.envoy.listenPort" in e
        for e in errors
    ), errors


def test_nok8s_claims_validator_survives_a_non_int_replicas() -> None:
    """A bad replicas value is the template's error to report, not a traceback."""
    assert (
        _validator()._validate_nok8s_host_claims(
            {
                "nok8s": {
                    "enabled": True,
                    "vllm": {"replicas": "auto", "hostPort": 8000},
                }
            },
            "solo",
        )
        == []
    )


def test_nok8s_quoted_replicas_still_spans_every_worker_port() -> None:
    """`replicas: "2"` must not under-count the span and hide a port clash."""
    errors = _validator()._validate_nok8s_host_claims(
        {
            "nok8s": {
                "enabled": True,
                "vllm": {"replicas": "2", "hostPort": 8000},
                "envoy": {"listenPort": 8001},
            }
        },
        "solo",
    )
    assert any("8001" in e and "claimed by both" in e for e in errors), errors


# A non-integer port is only caught by template arithmetic on `vllm.hostPort`.
# The other five interpolate verbatim, so without this validation they render a
# nonsense port into the Envoy bootstrap and the endpoint URL and exit 0.
_BAD_PORT_VALUES = ("auto", "", "80a1", "8081x", True, [8081], {"a": 1}, 8.5)


@pytest.mark.parametrize("value", _BAD_PORT_VALUES)
def test_nok8s_non_integer_port_is_a_render_error(value) -> None:
    errors = _validator()._validate_nok8s_host_claims(
        {"nok8s": {"enabled": True, "envoy": {"listenPort": value}}},
        "solo",
    )
    assert any(
        "nok8s.envoy.listenPort" in e and "must be an integer port" in e for e in errors
    ), (value, errors)


@pytest.mark.parametrize("value", (0, -1, 65536, 99999))
def test_nok8s_out_of_range_port_is_a_render_error(value: int) -> None:
    errors = _validator()._validate_nok8s_host_claims(
        {"nok8s": {"enabled": True, "envoy": {"listenPort": value}}},
        "solo",
    )
    assert any("nok8s.envoy.listenPort" in e and "1-65535" in e for e in errors), (
        value,
        errors,
    )


@pytest.mark.parametrize("value", ("8000", " 8000 ", "08000"))
def test_nok8s_quoted_port_is_accepted_as_its_number(value: str) -> None:
    """A digit-string is the number it looks like, so it can clash like one."""
    errors = _validator()._validate_nok8s_host_claims(
        {
            "nok8s": {
                "enabled": True,
                "vllm": {"hostPort": 8000},
                "envoy": {"listenPort": value},
            }
        },
        "solo",
    )
    assert any("8000" in e and "claimed by both" in e for e in errors), errors


def test_nok8s_absent_port_is_not_a_render_error() -> None:
    """defaults.yaml supplies every port; presence is not this check's contract."""
    assert (
        _validator()._validate_nok8s_host_claims(
            {"nok8s": {"enabled": True, "vllm": {"hostPort": 8000}}},
            "solo",
        )
        == []
    )


def test_nok8s_sibling_stacks_sharing_an_unusable_port_are_both_reported() -> None:
    """Two stacks with the same non-port value must not both slip through."""
    v = _validator()
    values = {"nok8s": {"enabled": True, "envoy": {"listenPort": "auto"}}}
    first = v._validate_nok8s_host_claims(values, "stack-a")
    second = v._validate_nok8s_host_claims(values, "stack-b")
    assert first and second, (first, second)


def test_nok8s_run_endpoint_needs_one_stack() -> None:
    """The run phase refuses to benchmark every stack through stack 1's Envoy."""
    from llmdbenchmark.cli import PhaseError, _nok8s_endpoint_url

    stacks = [
        {"stack_name": "chat", "nok8s_enabled": True, "nok8s_listen_port": 8081},
        {"stack_name": "code", "nok8s_enabled": True, "nok8s_listen_port": 8181},
    ]

    assert _nok8s_endpoint_url(stacks[:1]) == "http://localhost:8081"
    assert _nok8s_endpoint_url(stacks, ["code"]) == "http://localhost:8181"
    try:
        _nok8s_endpoint_url(stacks)
    except PhaseError as e:
        assert "--stack" in str(e) and "8181" in str(e)
    else:
        raise AssertionError("expected a PhaseError for a multi-stack nok8s run")


def test_nok8s_deploy_never_removes_a_sibling_stacks_containers(
    tmp_path: Path,
) -> None:
    """Stack B's idempotency sweep must not delete the containers stack A launched."""
    from llmdbenchmark.standup.steps.step_06_nok8s_deploy import NoK8sDeployStep

    result = _render_scenario(tmp_path, _two_stack_scenario(tmp_path, True))
    first, second = (Path(p) for p in result.rendered_paths)

    cmd_a, cmd_b = _RecordingCmd(), _RecordingCmd()
    for stack, cmd in ((first, cmd_a), (second, cmd_b)):
        ctx = _nok8s_ctx(tmp_path, cmd)
        ctx.dry_run = True
        ctx.rendered_stacks = [first, second]
        assert NoK8sDeployStep().execute(ctx, stack).success is True

    assert cmd_a.launched() and cmd_b.launched()
    assert not cmd_b.removed() & cmd_a.launched()
    assert not cmd_a.removed() & cmd_b.launched()


def test_nok8s_teardown_leaves_siblings_alone_without_a_spec(tmp_path: Path) -> None:
    """No spec + sibling stacks -> remove nothing, rather than the well-known names."""
    from llmdbenchmark.teardown.steps.step_06_nok8s_teardown import NoK8sTeardownStep

    specless = tmp_path / "modelservice-stack"
    specless.mkdir()
    cmd = _RecordingCmd()
    ctx = _nok8s_ctx(tmp_path, cmd)
    ctx.rendered_stacks = [specless, tmp_path / "nok8s-stack"]

    assert NoK8sTeardownStep().execute(ctx, specless).success is True
    assert cmd.removed() == set()

    # Single-stack plan keeps the well-known-names fallback.
    solo = _nok8s_ctx(tmp_path, _RecordingCmd())
    solo.rendered_stacks = [specless]
    NoK8sTeardownStep().execute(solo, specless)
    assert solo.cmd.removed() == {"envoy", "epp", "vllm-0"}


class _RecordingVersionResolver:
    """Records how the renderer invokes version resolution."""

    def __init__(self) -> None:
        self.calls: list[bool] = []

    def resolve_all(self, values: dict, skip_kubernetes: bool = False) -> dict:
        self.calls.append(skip_kubernetes)
        return values


def test_nok8s_skips_kubernetes_version_resolution(tmp_path: Path) -> None:
    """nok8s must not try to resolve helm chart versions or the WVA image.

    Resolving them needs helm/skopeo, which docs/nok8s.md says are not
    required, and nothing on this path consumes the results.
    """
    resolver = _RecordingVersionResolver()
    _render(tmp_path, version_resolver=resolver)

    assert resolver.calls, "version resolver was never invoked"
    assert all(resolver.calls), (
        f"expected skip_kubernetes=True for every nok8s stack, got {resolver.calls}"
    )


def test_kubernetes_scenario_still_resolves_versions(tmp_path: Path) -> None:
    """Guard the test above: the flag is not unconditionally True."""
    resolver = _RecordingVersionResolver()
    RenderPlans(
        template_dir=TEMPLATE_DIR,
        defaults_file=DEFAULTS_FILE,
        scenarios_file=REPO_ROOT
        / "config"
        / "scenarios"
        / "guides"
        / "optimized-baseline.yaml",
        output_dir=tmp_path / "plan-k8s",
        logger=_Logger(),
        version_resolver=resolver,
    ).eval()

    assert resolver.calls, "version resolver was never invoked"
    assert not any(resolver.calls), (
        f"expected skip_kubernetes=False off the nok8s path, got {resolver.calls}"
    )
