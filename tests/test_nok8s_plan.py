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
        self.seen: list[str] = []

    def execute(self, cmd, *_, **__):
        self.seen.append(cmd)
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


def test_remote_preflight_does_not_require_a_client_here(tmp_path: Path) -> None:
    """Under the default ssh transport nothing runs a container locally.

    So a missing local docker/podman must not block a remote standup -- that
    requirement is what forced people to install a client that only relays, and
    a client of the *same family* as the node's daemon at that.
    """
    from llmdbenchmark.standup.steps.step_00_ensure_infra import EnsureInfraStep

    ctx = _nok8s_ctx(
        tmp_path,
        _FakeCmd(
            fail_substrings=("command -v docker",),
            stdout_for={"ss -ltn": "State  Recv-Q\n"},
        ),
        nok8s={"enabled": True, "connection": "bench@10.0.0.7"},
    )
    result = EnsureInfraStep().execute(ctx)
    assert not any("not found on PATH" in e and "docker" in e for e in result.errors), (
        result.errors
    )


def test_native_transport_still_requires_a_client_here(tmp_path: Path) -> None:
    """`docker -H` cannot run without docker, and the error should say why."""
    from llmdbenchmark.standup.steps.step_00_ensure_infra import EnsureInfraStep

    ctx = _nok8s_ctx(
        tmp_path,
        _FakeCmd(fail_substrings=("command -v docker",)),
        nok8s={
            "enabled": True,
            "connection": "bench@10.0.0.7",
            "transport": "native",
        },
    )
    result = EnsureInfraStep().execute(ctx)
    assert not result.success
    missing = next(e for e in result.errors if "not found on PATH" in e)
    # It points at the way out rather than only at the missing binary.
    assert "transport" in missing and "ssh" in missing


def test_timeout_is_probed_on_the_client_not_the_node(tmp_path: Path) -> None:
    """`timeout` wraps the *client* command (ssh, or the local client binary).

    Probing it on the node passed on a node that had it while the client did
    not, and the harness wait then ran unbounded. `curl` is the opposite case:
    it probes readiness from the node, so it is checked there.
    """
    from llmdbenchmark.standup.steps.step_00_ensure_infra import EnsureInfraStep

    ctx = _nok8s_ctx(
        tmp_path,
        _FakeCmd(stdout_for={"ss -ltn": "State  Recv-Q\n"}),
        nok8s={"enabled": True, "connection": "bench@10.0.0.7"},
    )
    EnsureInfraStep().execute(ctx)
    probes = [c for c in ctx.cmd.seen if "command -v timeout" in c]
    assert probes and not any(c.startswith("ssh ") for c in probes), probes
    node_probes = [c for c in ctx.cmd.seen if "command -v curl" in c]
    assert node_probes and all(c.startswith("ssh ") for c in node_probes), node_probes


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
        # Commands passed force=True, which is what actually runs under
        # --dry-run: everything else the executor only prints.
        self.forced: list[str] = []
        self.stdins: list[str] = []

    def execute(self, cmd, *args, **kwargs):
        self.commands.append(cmd)
        self.stdins.append(kwargs.get("stdin", ""))
        if kwargs.get("force"):
            self.forced.append(cmd)
        return super().execute(cmd, *args, **kwargs)

    def removed(self) -> set[str]:
        # Under ssh transport the command is quoted as a unit, so the last
        # token carries the closing quote; the container name is the same.
        return {c.split()[-1].rstrip("'") for c in self.commands if " rm -f " in c}

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


# ---------------------------------------------------------------------- #
# Envoy hot-restart base ID
# ---------------------------------------------------------------------- #
def _envoy_of(stack_dir: Path) -> dict:
    return next(c for c in _spec_of(stack_dir)["containers"] if c["kind"] == "envoy")


def test_two_stacks_get_distinct_envoy_base_ids(tmp_path: Path) -> None:
    """Envoy's base ID is a host-wide claim under --network host.

    Two Envoys sharing one would leave the second dead with errno=98 before it
    bound its listener, which the CLI could only report as a readiness timeout.
    """
    result = _render_scenario(tmp_path, _two_stack_scenario(tmp_path, True))
    assert not result.has_errors, result.to_dict()
    first, second = (Path(p) for p in result.rendered_paths)

    base_a, base_b = _envoy_of(first)["baseId"], _envoy_of(second)["baseId"]
    assert base_a != base_b
    # Seeded from listenPort, which the host-claim validator already proves
    # unique -- so the IDs are not a second set of numbers to hand-sync.
    assert (base_a, base_b) == (8081, 8181)


def test_single_stack_envoy_also_gets_a_base_id(tmp_path: Path) -> None:
    """One stack needs it too: the Envoy it collides with can be last run's."""
    assert _envoy_of(_stack_dir(_render(tmp_path)))["baseId"] == 8081


def test_authored_envoy_base_id_is_preserved(tmp_path: Path) -> None:
    """An explicit baseId wins over the listenPort-derived default."""
    scenario = _two_stack_scenario(tmp_path, True, both_nok8s={"envoy": {"baseId": 42}})
    result = _render_scenario(tmp_path, scenario)
    first = Path(result.rendered_paths[0])
    assert _envoy_of(first)["baseId"] == 42


@pytest.mark.parametrize("port", ["auto", None, 0, 99999])
def test_unusable_listen_port_leaves_the_base_id_alone(port) -> None:
    """A port the host-claim validator rejects must not seed a base ID.

    Reporting a plausible-looking ID in front of the real complaint would only
    make the port error harder to read.
    """
    values = {"nok8s": {"enabled": True, "envoy": {"listenPort": port}}}
    out = _validator()._resolve_nok8s_envoy_base_id(values)
    assert "baseId" not in out["nok8s"]["envoy"]


def test_quoted_listen_port_still_seeds_the_base_id() -> None:
    """`listenPort: '8081'` is a string; the derived ID is still an int."""
    values = {"nok8s": {"enabled": True, "envoy": {"listenPort": "8081"}}}
    out = _validator()._resolve_nok8s_envoy_base_id(values)
    assert out["nok8s"]["envoy"]["baseId"] == 8081


def test_base_id_resolver_skips_a_disabled_nok8s() -> None:
    values = {"nok8s": {"enabled": False, "envoy": {"listenPort": 8081}}}
    out = _validator()._resolve_nok8s_envoy_base_id(values)
    assert "baseId" not in out["nok8s"]["envoy"]


def test_envoy_is_launched_with_its_base_id(tmp_path: Path) -> None:
    """The resolved ID reaches the docker command as --base-id."""
    from llmdbenchmark.standup.steps.step_06_nok8s_deploy import NoK8sDeployStep

    stack = _nok8s_stack(tmp_path)
    spec = yaml.safe_load(
        (stack / "34_nok8s-containers.yaml").read_text(encoding="utf-8")
    )
    for container in spec["containers"]:
        if container["kind"] == "envoy":
            container["baseId"] = 8081
    (stack / "34_nok8s-containers.yaml").write_text(yaml.safe_dump(spec), "utf-8")

    cmd = _RecordingCmd(fail_substrings=("--name envoy",))
    NoK8sDeployStep().execute(_nok8s_ctx(tmp_path, cmd), stack)

    envoy_run = next(c for c in cmd.commands if " run -d --name envoy" in c)
    assert "--base-id 8081" in envoy_run


def test_a_plan_without_a_base_id_launches_envoy_unchanged(tmp_path: Path) -> None:
    """Back-compat: a plan rendered before baseId existed gets no flag."""
    from llmdbenchmark.standup.steps.step_06_nok8s_deploy import NoK8sDeployStep

    cmd = _RecordingCmd(fail_substrings=("--name envoy",))
    NoK8sDeployStep().execute(_nok8s_ctx(tmp_path, cmd), _nok8s_stack(tmp_path))

    envoy_run = next(c for c in cmd.commands if " run -d --name envoy" in c)
    assert "--base-id" not in envoy_run


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


# ---------------------------------------------------------------------------
# Remote connection (nok8s.connection)
# ---------------------------------------------------------------------------

REMOTE = "ssh://bench@10.0.0.7"


def _remote_scenario(tmp_path: Path, connection: str = REMOTE, **nok8s) -> Path:
    """The shipped single-stack scenario, pointed at a remote node."""
    doc = yaml.safe_load(NOK8S_SCENARIO.read_text(encoding="utf-8"))
    doc["scenario"][0]["nok8s"].update({"connection": connection, **nok8s})
    path = tmp_path / "remote.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return path


def test_local_render_keeps_both_endpoints_on_localhost(tmp_path: Path) -> None:
    """Back-compat: the default connection changes nothing a consumer reads."""
    spec = _spec_of(_stack_dir(_render(tmp_path)))
    assert spec["connection"] == "localhost"
    assert spec["endpoint"] == "http://localhost:8081"
    assert spec["clientEndpoint"] == "http://localhost:8081"


def test_remote_render_splits_in_host_and_client_endpoints(tmp_path: Path) -> None:
    """The harness runs on the node; the smoketest dials it from the client.

    Collapsing these two would either point the smoketest at the client's own
    port 8081 or make every harness request traverse the SSH link and report
    that latency as the stack's.
    """
    result = _render_scenario(tmp_path, _remote_scenario(tmp_path))
    assert not result.has_errors, result.to_dict()
    spec = _spec_of(Path(result.rendered_paths[0]))

    assert spec["connection"] == REMOTE
    assert spec["endpoint"] == "http://localhost:8081"
    assert spec["clientEndpoint"] == "http://10.0.0.7:8081"


def test_remote_render_carries_ssh_identity_and_args(tmp_path: Path) -> None:
    """The steps re-parse the spec, so the SSH options have to survive render."""
    scenario = _remote_scenario(
        tmp_path,
        sshIdentity="/keys/id_ed25519",
        sshArgs=["-o", "StrictHostKeyChecking=yes"],
    )
    spec = _spec_of(Path(_render_scenario(tmp_path, scenario).rendered_paths[0]))
    assert spec["sshIdentity"] == "/keys/id_ed25519"
    assert spec["sshArgs"] == ["-o", "StrictHostKeyChecking=yes"]


def test_bare_ip_connection_resolves_the_client_host(tmp_path: Path) -> None:
    """`connection: 10.0.0.7` is the whole ask -- no scheme, no tunnel."""
    result = _render_scenario(tmp_path, _remote_scenario(tmp_path, "10.0.0.7"))
    assert not result.has_errors, result.to_dict()
    spec = _spec_of(Path(result.rendered_paths[0]))
    assert spec["clientEndpoint"] == "http://10.0.0.7:8081"


def test_tcp_connection_is_a_render_error(tmp_path: Path) -> None:
    """Fail at render, before any container is launched, and say why."""
    result = _render_scenario(
        tmp_path, _remote_scenario(tmp_path, "tcp://10.0.0.7:2375")
    )
    assert result.has_errors
    errors = result.stacks["nok8s-single"].render_errors
    assert any("tcp://" in e and "ssh://" in e for e in errors), errors


def test_unsupported_connection_scheme_is_a_render_error(tmp_path: Path) -> None:
    result = _render_scenario(tmp_path, _remote_scenario(tmp_path, "http://10.0.0.7"))
    assert result.stacks["nok8s-single"].render_errors


def _remote_stack(
    tmp_path: Path, connection: str = REMOTE, transport: str = ""
) -> Path:
    """A rendered-spec stack dir whose containers live on a remote node."""
    stack = _nok8s_stack(tmp_path)
    spec_file = stack / "34_nok8s-containers.yaml"
    spec = yaml.safe_load(spec_file.read_text(encoding="utf-8"))
    spec["connection"] = connection
    if transport:
        spec["transport"] = transport
    spec["clientEndpoint"] = "http://10.0.0.7:8081"
    spec["workspaceHostDir"] = "~/.llmdbench/nok8s"
    spec["readiness"] = {"vllmPorts": [8000], "envoyPort": 8081}
    # Give the stack the config files the step stages, so the staging path is
    # exercised rather than skipped.
    for prefix in ("31_nok8s-epp-config", "32_nok8s-epp-endpoints", "33_nok8s-envoy"):
        (stack / f"{prefix}.yaml").write_text("k: v\n", encoding="utf-8")
    spec_file.write_text(yaml.safe_dump(spec), encoding="utf-8")
    return stack


def test_remote_deploy_runs_the_runtime_on_the_node(tmp_path: Path) -> None:
    """The default transport needs no container client on this machine.

    Every one of the three launches is a remote operation, so the runtime is
    invoked on the node over ssh rather than through a local client that would
    only relay -- and would have to match the node's daemon family to do it.
    """
    from llmdbenchmark.standup.steps.step_06_nok8s_deploy import NoK8sDeployStep

    stack = _remote_stack(tmp_path)
    cmd = _RecordingCmd(stdout_for={"printenv HOME": "/home/bench\n"})
    ctx = _nok8s_ctx(tmp_path, cmd)

    result = NoK8sDeployStep().execute(ctx, stack)
    assert result.success is True, result.message

    runs = [c for c in cmd.commands if " run -d --name " in c]
    assert len(runs) == 3, cmd.commands
    for run in runs:
        assert run.startswith("ssh ")
        assert "bench@10.0.0.7 'docker run -d --name " in run
        assert " -H ssh://" not in run


def test_native_transport_puts_the_connection_flag_before_the_subcommand(
    tmp_path: Path,
) -> None:
    """`docker run -H ssh://...` is not valid; the flag has to precede `run`."""
    from llmdbenchmark.standup.steps.step_06_nok8s_deploy import NoK8sDeployStep

    stack = _remote_stack(tmp_path, transport="native")
    cmd = _RecordingCmd(stdout_for={"printenv HOME": "/home/bench\n"})
    ctx = _nok8s_ctx(tmp_path, cmd)

    result = NoK8sDeployStep().execute(ctx, stack)
    assert result.success is True, result.message

    runs = [c for c in cmd.commands if " run -d --name " in c]
    assert len(runs) == 3, cmd.commands
    for run in runs:
        assert run.startswith("docker -H ssh://bench@10.0.0.7/var/run/docker.sock run")


def test_the_hf_token_reaches_the_node_without_being_logged(tmp_path: Path) -> None:
    """`-e VAR` with no value is expanded by whoever runs the CLI.

    Natively that is this process, so the token in the operator's shell reaches
    the container. Over ssh the runtime runs on the *node*, where the variable is
    unset -- a gated model would 401 with nothing in the logs to explain it. So
    the value is carried across explicitly, but over stdin: every command string
    is written to the workspace command log, so a `VAR=value` prefix would leave
    the token there in cleartext.
    """
    from llmdbenchmark.standup.steps.step_06_nok8s_deploy import NoK8sDeployStep

    stack = _remote_stack(tmp_path)
    cmd = _RecordingCmd(stdout_for={"printenv HOME": "/home/bench\n"})
    ctx = _nok8s_ctx(tmp_path, cmd)
    import os

    os.environ["HUGGING_FACE_HUB_TOKEN"] = "hf_supersecret"
    try:
        result = NoK8sDeployStep().execute(ctx, stack)
    finally:
        os.environ.pop("HUGGING_FACE_HUB_TOKEN", None)
    assert result.success is True, result.message

    vllm = next(c for c in cmd.commands if "--name vllm-0" in c)
    assert "hf_supersecret" not in vllm
    # It is still requested, and the remote shell has it by then.
    assert "-e HUGGING_FACE_HUB_TOKEN" in vllm
    assert 'eval "$(cat)" &&' in vllm
    token_stdin = next(s for s in cmd.stdins if s)
    assert "hf_supersecret" in token_stdin
    # Nothing anywhere in the log carries the value.
    assert not any("hf_supersecret" in c for c in cmd.commands)


def test_remote_deploy_stages_configs_on_the_node_before_launching(
    tmp_path: Path,
) -> None:
    """Bind-mount sources resolve on the daemon, so they must exist there first.

    docker turns a missing source into an empty directory, so without the push
    the EPP would start with no endpoints file and route nothing.
    """
    from llmdbenchmark.standup.steps.step_06_nok8s_deploy import NoK8sDeployStep

    stack = _remote_stack(tmp_path)
    cmd = _RecordingCmd(stdout_for={"printenv HOME": "/home/bench\n"})
    ctx = _nok8s_ctx(tmp_path, cmd)

    NoK8sDeployStep().execute(ctx, stack)

    scp = next((i for i, c in enumerate(cmd.commands) if "scp " in c), None)
    first_run = next(i for i, c in enumerate(cmd.commands) if " run -d --name " in c)
    assert scp is not None, cmd.commands
    assert scp < first_run, "configs staged after the containers were launched"
    assert "bench@10.0.0.7:" in cmd.commands[scp]


def test_remote_deploy_expands_tilde_against_the_nodes_home(tmp_path: Path) -> None:
    """`~` in workspaceHostDir belongs to the remote user, not the caller."""
    from llmdbenchmark.standup.steps.step_06_nok8s_deploy import NoK8sDeployStep

    stack = _remote_stack(tmp_path)
    cmd = _RecordingCmd(stdout_for={"printenv HOME": "/home/bench\n"})
    ctx = _nok8s_ctx(tmp_path, cmd)

    NoK8sDeployStep().execute(ctx, stack)

    envoy_run = next(c for c in cmd.commands if " run -d --name envoy" in c)
    assert "/home/bench/.llmdbench/nok8s/envoy.yaml" in envoy_run
    assert "~" not in envoy_run


def test_remote_deploy_probes_readiness_on_the_node(tmp_path: Path) -> None:
    """A curl for `localhost` from the client would probe the client."""
    from llmdbenchmark.standup.steps.step_06_nok8s_deploy import NoK8sDeployStep

    stack = _remote_stack(tmp_path)
    cmd = _RecordingCmd(stdout_for={"printenv HOME": "/home/bench\n"})
    ctx = _nok8s_ctx(tmp_path, cmd)

    NoK8sDeployStep().execute(ctx, stack)

    probes = [c for c in cmd.commands if "curl -fsS" in c]
    assert probes, cmd.commands
    for probe in probes:
        assert probe.startswith("ssh ")
        assert "bench@10.0.0.7" in probe


def test_remote_deploy_records_the_in_host_endpoint(tmp_path: Path) -> None:
    """The harness runs on the node, so it benchmarks localhost there."""
    from llmdbenchmark.standup.steps.step_06_nok8s_deploy import NoK8sDeployStep

    stack = _remote_stack(tmp_path)
    cmd = _RecordingCmd(stdout_for={"printenv HOME": "/home/bench\n"})
    ctx = _nok8s_ctx(tmp_path, cmd)

    NoK8sDeployStep().execute(ctx, stack)
    assert ctx.deployed_endpoints["stack"] == "http://localhost:8081"


def test_remote_staging_failure_stops_before_any_container_starts(
    tmp_path: Path,
) -> None:
    """Fatal, not best-effort: an empty mount is a much worse failure to read."""
    from llmdbenchmark.standup.steps.step_06_nok8s_deploy import NoK8sDeployStep

    stack = _remote_stack(tmp_path)
    cmd = _RecordingCmd(
        fail_substrings=("scp ",), stdout_for={"printenv HOME": "/home/bench\n"}
    )
    ctx = _nok8s_ctx(tmp_path, cmd)

    result = NoK8sDeployStep().execute(ctx, stack)

    assert result.success is False
    assert "stage" in result.message.lower()
    assert not any(" run -d --name " in c for c in cmd.commands)


def test_remote_teardown_removes_containers_on_the_node(tmp_path: Path) -> None:
    from llmdbenchmark.teardown.steps.step_06_nok8s_teardown import NoK8sTeardownStep

    stack = _remote_stack(tmp_path)
    cmd = _RecordingCmd()
    ctx = _nok8s_ctx(tmp_path, cmd)

    result = NoK8sTeardownStep().execute(ctx, stack)

    assert result.success is True
    assert "10.0.0.7" in result.message
    assert cmd.removed() == {"vllm-0", "epp", "envoy"}
    for c in cmd.commands:
        assert c.startswith("ssh ")
        assert "bench@10.0.0.7 'docker rm -f " in c


def test_native_transport_teardown_reaches_the_same_daemon(tmp_path: Path) -> None:
    """Teardown reads the transport off the spec standup left behind.

    Defaulting it here instead would leave a native-transport stack running:
    the removals would go out over a different mechanism than the launches, and
    on a host with no local client they would not go out at all.
    """
    from llmdbenchmark.teardown.steps.step_06_nok8s_teardown import NoK8sTeardownStep

    stack = _remote_stack(tmp_path, transport="native")
    cmd = _RecordingCmd()
    ctx = _nok8s_ctx(tmp_path, cmd)

    result = NoK8sTeardownStep().execute(ctx, stack)

    assert result.success is True
    assert cmd.removed() == {"vllm-0", "epp", "envoy"}
    for c in cmd.commands:
        assert c.startswith("docker -H ssh://bench@10.0.0.7/var/run/docker.sock rm")


def test_teardown_refuses_an_unusable_connection_instead_of_going_local(
    tmp_path: Path,
) -> None:
    """Falling back to the local runtime would remove another stack's containers
    and leave the remote node serving."""
    from llmdbenchmark.teardown.steps.step_06_nok8s_teardown import NoK8sTeardownStep

    stack = _remote_stack(tmp_path, connection="tcp://10.0.0.7:2375")
    cmd = _RecordingCmd()
    ctx = _nok8s_ctx(tmp_path, cmd)

    result = NoK8sTeardownStep().execute(ctx, stack)

    assert result.success is False
    assert cmd.commands == []


def test_remote_preflight_tests_the_connection_and_probes_the_node(
    tmp_path: Path,
) -> None:
    """`info` is answered by the daemon, so one call proves the SSH path works;
    the accelerator and the ports have to be checked on the serving host."""
    from llmdbenchmark.standup.steps.step_00_ensure_infra import EnsureInfraStep

    ctx = _nok8s_ctx(
        tmp_path,
        _RecordingCmd(stdout_for={"ss -ltn": "State  Recv-Q\n"}),
        nok8s={"enabled": True, "connection": REMOTE},
    )
    import os

    os.environ["HUGGING_FACE_HUB_TOKEN"] = "hf_test"
    try:
        result = EnsureInfraStep().execute(ctx)
    finally:
        os.environ.pop("HUGGING_FACE_HUB_TOKEN", None)

    assert result.success is True, result.errors
    commands = ctx.cmd.commands
    assert any(c.startswith("ssh ") and c.endswith("'docker info'") for c in commands)
    # No local client is consulted -- there is nothing for one to do.
    assert "command -v docker" not in commands
    assert any(c.startswith("ssh ") and "nvidia-smi -L" in c for c in commands)
    assert any(c.startswith("ssh ") and "ss -ltn" in c for c in commands)


def test_a_dead_connection_and_a_dead_daemon_are_reported_differently(
    tmp_path: Path,
) -> None:
    """Under ssh transport the two failures are distinguishable, so distinguish.

    ssh exits 255 for its own failures and passes the remote command's status
    through otherwise, which separates "never reached the node" from "reached it
    and the runtime would not answer". Collapsing them sent people to debug SSH
    keys when their user simply was not in the `docker` group.
    """
    from llmdbenchmark.standup.steps.step_00_ensure_infra import EnsureInfraStep

    # (a) ssh itself failed.
    ctx = _nok8s_ctx(
        tmp_path,
        _ScriptedCmd(scripted=(("docker info", 255, "Permission denied (publickey)"),)),
        nok8s={"enabled": True, "connection": REMOTE},
    )
    result = EnsureInfraStep().execute(ctx)
    assert result.success is False
    joined = " ".join(result.errors)
    assert "ssh bench@10.0.0.7 true" in joined
    assert "over ssh" in joined
    # No local client is implicated -- there is none.
    assert "nok8s.runtime=" not in joined

    # (b) the node answered; its runtime did not.
    ctx = _nok8s_ctx(
        tmp_path,
        _ScriptedCmd(
            scripted=(("docker info", 1, "permission denied while trying to connect"),)
        ),
        nok8s={"enabled": True, "connection": REMOTE},
    )
    result = EnsureInfraStep().execute(ctx)
    assert result.success is False
    joined = " ".join(result.errors)
    assert "ssh bench@10.0.0.7 docker info" in joined
    assert "'docker' group" in joined, joined
    # It does not blame the SSH setup, which demonstrably worked.
    assert "ssh bench@10.0.0.7 true" not in joined


def test_native_preflight_reports_an_unreachable_daemon_with_the_fix(
    tmp_path: Path,
) -> None:
    from llmdbenchmark.standup.steps.step_00_ensure_infra import EnsureInfraStep

    ctx = _nok8s_ctx(
        tmp_path,
        _RecordingCmd(fail_substrings=("docker -H ",)),
        nok8s={"enabled": True, "connection": REMOTE, "transport": "native"},
    )
    result = EnsureInfraStep().execute(ctx)

    assert result.success is False
    joined = " ".join(result.errors)
    assert "ssh bench@10.0.0.7 true" in joined


def test_preflight_refuses_a_tcp_connection(tmp_path: Path) -> None:
    from llmdbenchmark.standup.steps.step_00_ensure_infra import EnsureInfraStep

    ctx = _nok8s_ctx(
        tmp_path,
        _RecordingCmd(),
        nok8s={"enabled": True, "connection": "tcp://10.0.0.7:2375"},
    )
    result = EnsureInfraStep().execute(ctx)
    assert result.success is False
    assert any("tcp://" in e for e in result.errors)


def test_remote_dry_run_never_touches_the_node(tmp_path: Path) -> None:
    """--dry-run must work with the node unreachable (or nonexistent).

    It proves the plan and the command strings, so reaching out to read $HOME
    would make a dry-run wait on an SSH timeout for a host that may not be up
    yet.
    """
    from llmdbenchmark.standup.steps.step_06_nok8s_deploy import NoK8sDeployStep

    stack = _remote_stack(tmp_path)
    cmd = _RecordingCmd()
    ctx = _nok8s_ctx(tmp_path, cmd)
    ctx.dry_run = True

    result = NoK8sDeployStep().execute(ctx, stack)

    assert result.success is True
    # The launch commands are *built* -- printing them is the point of a dry
    # run -- and the executor prints rather than runs them. What must not
    # happen is a `force=True` call, which runs even under --dry-run because
    # the step needs its output: reading the node's $HOME, staging configs,
    # probing readiness.
    assert cmd.forced == [], cmd.forced
    assert not any("printenv HOME" in c for c in cmd.commands)
    assert not any("scp " in c for c in cmd.commands)
    assert not any("curl" in c for c in cmd.commands)


# ---------------------------------------------------------------------------
# Preflight diagnosis: a broken connection must not read as a broken node
# ---------------------------------------------------------------------------


class _ScriptedCmd(_RecordingCmd):
    """_RecordingCmd with per-command exit codes and stderr.

    _FakeCmd only models success/failure with exit_code 1, but the two bugs
    covered here are specifically about *which* failure happened: ssh's own 255
    versus a remote command's non-zero status, and the runtime client's stderr
    text.
    """

    def __init__(self, scripted=(), stdout_for=None) -> None:
        super().__init__((), stdout_for)
        # (substring, exit_code, stderr), first match wins.
        self.scripted = scripted

    def execute(self, cmd, *args, **kwargs):
        self.commands.append(cmd)
        for needle, code, stderr in self.scripted:
            if needle in cmd:
                result = _FakeResult(code == 0)
                result.exit_code = code
                result.stderr = stderr
                return result
        out = ""
        for key, val in self.stdout_for.items():
            if key in cmd:
                out = val
        return _FakeResult(True, out)


def _remote_ctx(
    tmp_path: Path,
    cmd,
    connection: str,
    runtime: str = "docker",
    transport: str = "",
):
    nok8s = {"enabled": True, "connection": connection, "vllm": {}}
    if transport:
        nok8s["transport"] = transport
    ctx = _nok8s_ctx(tmp_path, cmd, nok8s=nok8s)
    ctx.container_runtime = runtime
    return ctx


PODMAN_AUTH_STDERR = (
    "Error: unable to connect to Podman socket: failed to connect: ssh: "
    "handshake failed: ssh: unable to authenticate, attempted methods [none], "
    "no supported methods remain"
)

# docker's equivalent, which reads nothing like podman's: the CLI has no SSH
# client of its own, so it execs `ssh` and can only report that process's exit
# status. ssh's own "Permission denied" arrives on the same stream.
DOCKER_AUTH_STDERR = (
    "root@10.0.0.7: Permission denied (publickey).\n"
    "error during connect: Get "
    '"http://docker.example.com/v1.47/info": command '
    "[ssh -l root -- 10.0.0.7 docker system dial-stdio] has exited with exit "
    "status 255"
)


def _errors_of(
    tmp_path: Path,
    cmd,
    connection: str,
    runtime: str = "docker",
    transport: str = "",
):
    from llmdbenchmark.standup.steps.step_00_ensure_infra import EnsureInfraStep

    ctx = _remote_ctx(tmp_path, cmd, connection, runtime, transport)
    result = EnsureInfraStep().execute(ctx)
    assert result.success is False
    return result.errors


def _native_errors_of(tmp_path: Path, cmd, connection: str, runtime: str = "docker"):
    """Errors from a `transport: native` preflight.

    Everything below this point is about *which local client* failed and how it
    authenticates -- a question that only exists for the native transport, where
    a client on this machine opens the connection itself. Under the default ssh
    transport there is no local client to misidentify, so those diagnoses are
    deliberately not reached; see the ssh-transport tests further down.
    """
    return _errors_of(tmp_path, cmd, connection, runtime, transport="native")


def test_unreachable_node_is_reported_once_not_as_missing_tools(
    tmp_path: Path,
) -> None:
    """One dead connection used to surface as four unrelated failures.

    `ssh -p 2222` to a host whose sshd is on 22 exits 255, and every probe --
    timeout, curl, nvidia-smi, the port check -- travels that same connection.
    Reading their non-zero status as a verdict about the node told the user to
    install coreutils on a machine the tool had never reached.
    """
    cmd = _ScriptedCmd(
        scripted=(
            (" info", 1, "connect: connection refused"),
            ("ssh ", 255, "ssh: connect to host 10.0.0.7 port 2222: refused"),
        )
    )
    errors = _errors_of(tmp_path, cmd, "ssh://10.0.0.7:2222")

    assert not any("not found on PATH on" in e for e in errors), errors
    assert not any("coreutils" in e for e in errors), errors


def test_a_remote_command_that_really_fails_is_still_reported(tmp_path: Path) -> None:
    """The 255 special-case must not swallow a genuinely missing tool.

    A reachable node that lacks `curl` returns 127 from `command -v`, which is a
    real answer about that node and has to stay fatal.
    """
    cmd = _ScriptedCmd(scripted=(("command -v curl", 127, ""),))
    errors = _errors_of(tmp_path, cmd, "10.0.0.7")

    assert any("'curl' not found on PATH on 10.0.0.7" in e for e in errors), errors


def test_podman_masquerading_as_docker_is_named(tmp_path: Path) -> None:
    """`nok8s.runtime: docker` with a podman binary fails with podman's error.

    podman accepts docker's -H as a synonym for --url, so the mismatch is not
    caught by the flag -- it surfaces as a podman authentication error under a
    /var/run/docker.sock URL, and the advice for docker is then wrong.
    """
    cmd = _ScriptedCmd(
        scripted=((" info", 125, PODMAN_AUTH_STDERR),),
        stdout_for={"docker --version": "podman version 5.8.3"},
    )
    errors = _native_errors_of(tmp_path, cmd, "root@10.0.0.7")

    daemon = next(e for e in errors if "Cannot reach" in e)
    assert "is actually podman" in daemon, daemon
    assert "nok8s.runtime=podman" in daemon, daemon


def test_podmans_error_text_outweighs_a_docker_version_string(
    tmp_path: Path,
) -> None:
    """The failure text names the client, even when --version disagrees.

    Reported from a real node: `nok8s.runtime` defaulted to docker, the local
    `docker` answered "Docker version ...", and yet the connection failed with
    podman's Go-SSH handshake wording. Keying the advice on the configured
    runtime handed the user docker's advice ("put the key in your ssh-agent")
    directly underneath podman's own error -- advice that cannot work, since
    podman never consults the agent's OpenSSH-side defaults the same way.

    Only one of the two clients can produce this text, so it decides.
    """
    cmd = _ScriptedCmd(
        scripted=((" info", 125, PODMAN_AUTH_STDERR),),
        stdout_for={"docker --version": "Docker version 27.1.1, build 6312585"},
    )
    errors = _native_errors_of(tmp_path, cmd, "root@10.0.0.7")

    daemon = next(e for e in errors if "Cannot reach" in e)
    assert "nok8s.sshIdentity=<path-to-private-key>" in daemon, daemon
    # And it must not also carry docker's contradictory advice.
    assert "does not reach the docker client" not in daemon, daemon


def test_podman_symlinked_as_docker_reports_docker_in_its_version(
    tmp_path: Path,
) -> None:
    """The shim case is invisible to --version, which is why it cannot decide.

    podman prints "<argv[0]> version <n>", so the *same binary* reached through
    a symlink named `docker` says "docker version 4.4.1" -- a string with no
    trace of podman in it, and one that also looks like an old Docker to a
    reader (Docker prints "Docker version 27.1.1, build <sha>" and never had a
    4.4.1). Reported from a real client: `--version` corroborated docker, the
    connection failed with podman's handshake wording, and the advice has to
    follow the latter.
    """
    cmd = _ScriptedCmd(
        scripted=((" info", 125, PODMAN_AUTH_STDERR),),
        stdout_for={"docker --version": "docker version 4.4.1"},
    )
    errors = _native_errors_of(tmp_path, cmd, "root@10.0.0.7")

    daemon = next(e for e in errors if "Cannot reach" in e)
    assert "is actually podman" in daemon, daemon
    assert "nok8s.runtime=podman" in daemon, daemon
    assert "nok8s.sshIdentity=<path-to-private-key>" in daemon, daemon
    assert "does not reach the docker client" not in daemon, daemon


def test_an_attributed_mismatch_shows_its_evidence(tmp_path: Path) -> None:
    """ "Your docker is really podman" is a surprising claim; justify it."""
    cmd = _ScriptedCmd(
        scripted=((" info", 125, PODMAN_AUTH_STDERR),),
        stdout_for={"docker --version": "podman version 5.8.3"},
    )
    errors = _native_errors_of(tmp_path, cmd, "root@10.0.0.7")

    daemon = next(e for e in errors if "Cannot reach" in e)
    assert "is actually podman" in daemon, daemon
    assert "podman dials SSH itself" in daemon, daemon


def test_an_unidentifiable_client_gets_both_recipes(tmp_path: Path) -> None:
    """A wrapper script may reveal nothing; guessing one client misdirects.

    `--version` can fail or print something unrecognised (a shim, a wrapper,
    an unusual build). With no evidence either way the message must not assert
    a client -- it covers both, so whichever it is the user has the right step.
    """
    cmd = _ScriptedCmd(
        scripted=(
            (" info", 1, "ssh: unable to authenticate"),
            ("--version", 127, ""),
        ),
    )
    errors = _native_errors_of(tmp_path, cmd, "root@10.0.0.7", runtime="ctr-wrapper")

    daemon = next(e for e in errors if "Cannot reach" in e)
    assert "could not be identified" in daemon, daemon
    assert "nok8s.sshIdentity=<path>" in daemon, daemon
    assert "ssh-add <key>" in daemon, daemon
    assert "is actually" not in daemon, daemon


def test_podman_auth_failure_points_at_sshidentity(tmp_path: Path) -> None:
    """'attempted methods [none]' means podman offered no key at all.

    podman's Go SSH client does not fall back to ~/.ssh/id_rsa the way OpenSSH
    does, so `ssh <dest> true` succeeding proves nothing -- which is exactly
    what the old generic advice told the user to check.
    """
    cmd = _ScriptedCmd(
        scripted=((" info", 125, PODMAN_AUTH_STDERR),),
        stdout_for={"podman --version": "podman version 5.8.3"},
    )
    errors = _native_errors_of(tmp_path, cmd, "root@10.0.0.7", runtime="podman")

    daemon = next(e for e in errors if "Cannot reach" in e)
    assert "nok8s.sshIdentity" in daemon, daemon
    assert "never falls back" in daemon, daemon
    assert "a working 'ssh' proves nothing" in daemon, daemon


def test_podman_refusing_a_supplied_key_blames_the_node(tmp_path: Path) -> None:
    """With sshIdentity already set, re-suggesting it would be a dead end."""
    from llmdbenchmark.standup.steps.step_00_ensure_infra import EnsureInfraStep

    cmd = _ScriptedCmd(
        scripted=((" info", 125, PODMAN_AUTH_STDERR),),
        stdout_for={"podman --version": "podman version 5.8.3"},
    )
    ctx = _nok8s_ctx(
        tmp_path,
        cmd,
        nok8s={
            "enabled": True,
            "connection": "root@10.0.0.7",
            "transport": "native",
            "sshIdentity": "/home/me/.ssh/id_rsa",
            "vllm": {},
        },
    )
    ctx.container_runtime = "podman"
    result = EnsureInfraStep().execute(ctx)

    daemon = next(e for e in result.errors if "Cannot reach" in e)
    assert "authorized_keys" in daemon, daemon
    assert "Pass nok8s.sshIdentity" not in daemon, daemon


def test_docker_auth_failure_does_not_suggest_sshidentity(tmp_path: Path) -> None:
    """sshIdentity never reaches `docker -H`, so suggesting it would misdirect."""
    cmd = _ScriptedCmd(
        scripted=((" info", 1, DOCKER_AUTH_STDERR),),
        stdout_for={"docker --version": "Docker version 27.1.1, build 6312585"},
    )
    errors = _native_errors_of(tmp_path, cmd, "root@10.0.0.7")

    daemon = next(e for e in errors if "Cannot reach" in e)
    assert "ssh-add" in daemon, daemon
    assert "nok8s.sshIdentity does not reach" in daemon, daemon
    # docker really is docker here, so there is no mismatch to report.
    assert "is actually" not in daemon, daemon


def test_daemon_down_keeps_the_generic_runtime_advice(tmp_path: Path) -> None:
    """A reachable node whose daemon is simply not running is neither of the
    special cases, and must not be told about keys it already has."""
    cmd = _ScriptedCmd(
        scripted=((" info", 1, "Cannot connect to the Docker daemon at unix:///..."),),
        stdout_for={"docker --version": "Docker version 27.1.1"},
    )
    errors = _native_errors_of(tmp_path, cmd, "root@10.0.0.7")

    daemon = next(e for e in errors if "Cannot reach" in e)
    assert "is running on the node" in daemon, daemon
    assert "sshIdentity" not in daemon, daemon


def test_unreachable_daemon_skips_the_node_probes_entirely(tmp_path: Path) -> None:
    """No point asking an unreachable node about GPUs or ports."""
    from llmdbenchmark.standup.steps.step_00_ensure_infra import EnsureInfraStep

    cmd = _ScriptedCmd(
        scripted=(
            (" info", 1, "ssh: handshake failed: unable to authenticate"),
            ("ssh ", 255, "Permission denied (publickey)."),
        ),
        stdout_for={"docker --version": "Docker version 27.1.1"},
    )
    ctx = _remote_ctx(tmp_path, cmd, "root@10.0.0.7")
    EnsureInfraStep().execute(ctx)

    assert not any("nvidia-smi" in c for c in cmd.commands), cmd.commands
    assert not any("ss -ltn" in c for c in cmd.commands), cmd.commands


def test_local_preflight_is_unaffected_by_the_ssh_special_case(tmp_path: Path) -> None:
    """A local stack has no ssh in the path at all; 127 stays fatal as before."""
    cmd = _ScriptedCmd(scripted=(("command -v curl", 127, ""),))
    errors = _errors_of(tmp_path, cmd, "localhost")

    assert any("'curl' not found on PATH;" in e for e in errors), errors
