"""Tests for nok8s local harness exit-status reporting (issue #1700)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import yaml

from llmdbenchmark.executor.context import ExecutionContext

_STEP_PATH = (
    Path(__file__).resolve().parent.parent
    / "llmdbenchmark"
    / "run"
    / "steps"
    / "step_07_deploy_harness_local.py"
)
_spec = importlib.util.spec_from_file_location(
    "step_07_deploy_harness_local_status", _STEP_PATH
)
harness_local = importlib.util.module_from_spec(_spec)
sys.modules["step_07_deploy_harness_local_status"] = harness_local
_spec.loader.exec_module(harness_local)
DeployHarnessLocalStep = harness_local.DeployHarnessLocalStep


class _Logger:
    def __init__(self) -> None:
        self.infos: list[str] = []
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def log_info(self, message: str, *_: Any, **__: Any) -> None:
        self.infos.append(message)

    def log_warning(self, message: str, *_: Any, **__: Any) -> None:
        self.warnings.append(message)

    def log_error(self, message: str, *_: Any, **__: Any) -> None:
        self.errors.append(message)

    def log_debug(self, *_: Any, **__: Any) -> None:
        pass

    def line_break(self) -> None:
        pass


class _Result:
    def __init__(self, exit_code: int = 0, stdout: str = "") -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = ""
        self.dry_run = False

    @property
    def success(self) -> bool:
        return self.exit_code == 0


class _Command:
    """Records every command and replays canned stdout for matched substrings."""

    def __init__(self, replies: dict[str, _Result] | None = None) -> None:
        self.commands: list[str] = []
        self.replies = replies or {}

    def execute(self, command: str, *_: Any, **__: Any) -> _Result:
        self.commands.append(command)
        for key, reply in self.replies.items():
            if key in command:
                return reply
        return _Result()


def _plan_config() -> dict[str, Any]:
    return {
        "model": {"name": "test-model"},
        "images": {"benchmark": {"repository": "example.com/bench", "tag": "latest"}},
        "harness": {
            "name": "inference-perf",
            "experimentProfile": "sanity_random.yaml",
        },
        "nok8s": {"hfTokenEnv": "HUGGING_FACE_HUB_TOKEN"},
    }


def _context(tmp_path: Path, cmd: _Command, logger: _Logger):
    stack_path = tmp_path / "plan" / "stack"
    stack_path.mkdir(parents=True)
    (stack_path / "config.yaml").write_text(
        yaml.safe_dump(_plan_config()), encoding="utf-8"
    )
    context = ExecutionContext(
        plan_dir=tmp_path / "plan",
        workspace=tmp_path,
        base_dir=Path(__file__).resolve().parents[1],
        deployed_methods=["nok8s"],
        container_runtime="docker",
        logger=logger,
        cmd=cmd,
    )
    context.deployed_endpoints["stack"] = "http://localhost:8081"
    context.run_results_dir().mkdir(parents=True, exist_ok=True)
    return context, stack_path


def _inspect_commands(cmd: _Command) -> list[str]:
    return [c for c in cmd.commands if " inspect " in c]


def test_clean_exit_reports_success(tmp_path: Path) -> None:
    logger = _Logger()
    cmd = _Command({" inspect ": _Result(stdout="exited 0\n")})
    context, stack_path = _context(tmp_path, cmd, logger)

    result = DeployHarnessLocalStep().execute(context, stack_path)

    assert result.success
    assert result.errors == []


def test_nonzero_harness_exit_fails_the_step(tmp_path: Path) -> None:
    """A container that starts and then exits non-zero must not report success."""
    logger = _Logger()
    cmd = _Command({" inspect ": _Result(stdout="exited 137\n")})
    context, stack_path = _context(tmp_path, cmd, logger)

    result = DeployHarnessLocalStep().execute(context, stack_path)

    assert not result.success
    assert any("exited 137" in error for error in result.errors)
    # The operator is pointed at the surviving evidence.
    assert any(".log" in error for error in result.errors)
    assert any("partial" in warning for warning in logger.warnings)


def test_timeout_expired_container_still_running_fails(tmp_path: Path) -> None:
    """`timeout ... docker wait` giving up leaves the container running."""
    logger = _Logger()
    cmd = _Command(
        {
            "timeout ": _Result(exit_code=124),
            " inspect ": _Result(stdout="running 0\n"),
        }
    )
    context, stack_path = _context(tmp_path, cmd, logger)
    context.harness_wait_timeout = 30

    result = DeployHarnessLocalStep().execute(context, stack_path)

    assert not result.success
    assert any("did not finish within 30s" in error for error in result.errors)


def test_unreadable_exit_status_fails(tmp_path: Path) -> None:
    """A failed or unparsable inspect is reported, not silently ignored."""
    logger = _Logger()
    cmd = _Command({" inspect ": _Result(exit_code=1)})
    context, stack_path = _context(tmp_path, cmd, logger)

    result = DeployHarnessLocalStep().execute(context, stack_path)

    assert not result.success
    assert any("Could not read exit status" in error for error in result.errors)


def test_status_read_happens_before_container_removal(tmp_path: Path) -> None:
    """`docker rm -f` must not run before the exit status is read."""
    logger = _Logger()
    cmd = _Command({" inspect ": _Result(stdout="exited 0\n")})
    context, stack_path = _context(tmp_path, cmd, logger)

    DeployHarnessLocalStep().execute(context, stack_path)

    inspect_at = next(i for i, c in enumerate(cmd.commands) if " inspect " in c)
    # The pre-launch 'rm -f' is expected; the teardown one must come later.
    removals = [i for i, c in enumerate(cmd.commands) if " rm -f " in c]
    assert removals[-1] > inspect_at


def test_dry_run_issues_no_wait_and_no_inspect(tmp_path: Path) -> None:
    logger = _Logger()
    cmd = _Command()
    context, stack_path = _context(tmp_path, cmd, logger)
    context.dry_run = True

    result = DeployHarnessLocalStep().execute(context, stack_path)

    assert result.success
    assert not any(c.startswith("timeout ") for c in cmd.commands)
    assert _inspect_commands(cmd) == []


def test_preflight_is_fatal_when_wait_tools_are_missing(tmp_path: Path) -> None:
    """nok8s skips the k8s toolchain check, so it must check its own tools."""
    from llmdbenchmark.standup.steps.step_00_ensure_infra import EnsureInfraStep

    for tool in ("timeout", "curl"):
        logger = _Logger()
        cmd = _Command({f"command -v {tool}": _Result(exit_code=1)})
        context = ExecutionContext(
            plan_dir=tmp_path,
            workspace=tmp_path,
            deployed_methods=["nok8s"],
            container_runtime="docker",
            logger=logger,
            cmd=cmd,
        )

        result = EnsureInfraStep().execute(context)

        assert not result.success
        assert any(f"'{tool}' not found on PATH" in error for error in result.errors)


def test_debug_mode_does_not_check_exit_status(tmp_path: Path) -> None:
    """Debug containers run 'sleep infinity', so the wait always times out."""
    logger = _Logger()
    cmd = _Command({"timeout ": _Result(exit_code=124)})
    context, stack_path = _context(tmp_path, cmd, logger)
    context.harness_debug = True

    result = DeployHarnessLocalStep().execute(context, stack_path)

    assert result.success
    assert _inspect_commands(cmd) == []


# ---------------------------------------------------------------------------
# Remote nok8s (nok8s.connection)
# ---------------------------------------------------------------------------

REMOTE = "ssh://bench@10.0.0.7"


def _remote_context(
    tmp_path: Path, cmd: _Command, logger: _Logger, transport: str = ""
):
    """A context whose stack config points the runtime at a remote node."""
    context, stack_path = _context(tmp_path, cmd, logger)
    config = _plan_config()
    config["nok8s"]["connection"] = REMOTE
    if transport:
        config["nok8s"]["transport"] = transport
    (stack_path / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    return context, stack_path


def _remote_cmd() -> _Command:
    return _Command(
        {
            "printenv HOME": _Result(stdout="/home/bench\n"),
            " inspect ": _Result(stdout="exited 0\n"),
        }
    )


def test_remote_harness_runs_on_the_node(tmp_path: Path) -> None:
    """The load generator belongs on the serving host.

    Driving it from the client would add the SSH round-trip to every request
    and report that as the stack's latency, so the container is launched
    through the remote runtime, not locally.
    """
    cmd = _remote_cmd()
    context, stack_path = _remote_context(tmp_path, cmd, _Logger())

    result = DeployHarnessLocalStep().execute(context, stack_path)

    assert result.success, result.errors
    run = next(c for c in cmd.commands if " run -d --name " in c)
    # Default transport is ssh: the runtime is invoked on the node, so no
    # client binary is needed here and no -H/--url flag appears.
    assert run.startswith("ssh ")
    assert "bench@10.0.0.7 'docker run -d --name " in run
    assert " -H ssh://" not in run
    # It measures the in-host endpoint, which the deploy step recorded.
    assert "LLMDBENCH_HARNESS_STACK_ENDPOINT_URL=http://localhost:8081" in run


def test_native_transport_still_uses_the_client_connection_flag(
    tmp_path: Path,
) -> None:
    """``transport: native`` keeps the runtimes' own SSH transport.

    Anyone who already has a matching client and prefers ``docker -H`` should
    get exactly the command they got before the ssh transport existed.
    """
    cmd = _remote_cmd()
    context, stack_path = _remote_context(tmp_path, cmd, _Logger(), transport="native")

    result = DeployHarnessLocalStep().execute(context, stack_path)

    assert result.success, result.errors
    run = next(c for c in cmd.commands if " run -d --name " in c)
    assert run.startswith("docker -H ssh://bench@10.0.0.7/var/run/docker.sock run")
    assert not run.startswith("ssh ")


def test_remote_harness_mounts_paths_staged_on_the_node(tmp_path: Path) -> None:
    """Bind-mount sources resolve on the daemon, so the -v paths are remote."""
    cmd = _remote_cmd()
    context, stack_path = _remote_context(tmp_path, cmd, _Logger())

    DeployHarnessLocalStep().execute(context, stack_path)

    run = next(c for c in cmd.commands if " run -d --name " in c)
    remote_root = f"/home/bench/.llmdbench/nok8s-runs/stack/{tmp_path.name}"
    for mount in ("results:/requests", "profiles:/workspace/profiles"):
        assert f"{remote_root}/{mount}" in run, run
    assert f"{remote_root}/harnesses:/workspace/harnesses:ro" in run
    # Nothing client-side leaks into a mount source.
    assert str(context.run_results_dir()) not in run


def test_remote_harness_pushes_inputs_before_launching(tmp_path: Path) -> None:
    """Profiles and scripts have to be on the node before the container starts."""
    cmd = _remote_cmd()
    context, stack_path = _remote_context(tmp_path, cmd, _Logger())

    DeployHarnessLocalStep().execute(context, stack_path)

    first_scp = next(i for i, c in enumerate(cmd.commands) if "scp " in c)
    first_run = next(i for i, c in enumerate(cmd.commands) if " run -d --name " in c)
    assert first_scp < first_run


def test_remote_harness_pulls_results_back(tmp_path: Path) -> None:
    """The caller must end up with the same results tree the local path makes."""
    cmd = _remote_cmd()
    context, stack_path = _remote_context(tmp_path, cmd, _Logger())

    DeployHarnessLocalStep().execute(context, stack_path)

    pulls = [c for c in cmd.commands if "scp " in c and "bench@10.0.0.7:" in c]
    assert pulls, cmd.commands
    assert any(str(context.run_results_dir()) in c for c in pulls)


def test_remote_input_push_failure_is_fatal(tmp_path: Path) -> None:
    """docker mounts a missing source as an empty dir, so never launch blind."""
    cmd = _Command(
        {
            "printenv HOME": _Result(stdout="/home/bench\n"),
            "scp ": _Result(exit_code=1),
        }
    )
    context, stack_path = _remote_context(tmp_path, cmd, _Logger())

    result = DeployHarnessLocalStep().execute(context, stack_path)

    assert not result.success
    assert any("stage" in error.lower() for error in result.errors)
    assert not any(" run -d --name " in c for c in cmd.commands)


def test_remote_scratch_dir_is_unique_per_invocation(tmp_path: Path) -> None:
    """Two runs must not share a remote results dir, or the second reports the
    first's numbers."""
    seen = set()
    for name in ("user-20260818-000001-000", "user-20260818-000002-000"):
        workspace = tmp_path / name
        workspace.mkdir()
        cmd = _remote_cmd()
        context, stack_path = _remote_context(workspace, cmd, _Logger())
        DeployHarnessLocalStep().execute(context, stack_path)
        run = next(c for c in cmd.commands if " run -d --name " in c)
        seen.add(run.split(":/requests")[0].rsplit(" ", 1)[-1])
    assert len(seen) == 2, seen


def _rendered_spec(stack_path: Path) -> None:
    """The launch spec standup leaves behind, carrying both endpoints."""
    (stack_path / "34_nok8s-containers.yaml").write_text(
        yaml.safe_dump(
            {
                "endpoint": "http://localhost:8081",
                "clientEndpoint": "http://10.0.0.7:8081",
                "model": "test-model",
            }
        ),
        encoding="utf-8",
    )


def test_standalone_run_measures_the_in_host_endpoint(tmp_path: Path) -> None:
    """A fresh ``run`` reads the endpoint off the rendered spec, not the CLI.

    ``deployed_endpoints`` is only populated when standup ran in the same
    process. Without it the step used to fall back to ``context.endpoint_url``,
    which is the client-side URL -- so a container running *on the node* would
    have sent every request out to the node's external address and back.
    """
    cmd = _remote_cmd()
    context, stack_path = _remote_context(tmp_path, cmd, _Logger())
    context.deployed_endpoints.clear()
    context.endpoint_url = "http://10.0.0.7:8081"
    _rendered_spec(stack_path)

    result = DeployHarnessLocalStep().execute(context, stack_path)

    assert result.success, result.errors
    run = next(c for c in cmd.commands if " run -d --name " in c)
    assert "LLMDBENCH_HARNESS_STACK_ENDPOINT_URL=http://localhost:8081" in run
    assert "10.0.0.7:8081" not in run


def test_standalone_run_without_a_spec_still_falls_back(tmp_path: Path) -> None:
    """No rendered spec (e.g. a hand-rolled plan) must not break the run."""
    cmd = _remote_cmd()
    context, stack_path = _remote_context(tmp_path, cmd, _Logger())
    context.deployed_endpoints.clear()
    context.endpoint_url = "http://10.0.0.7:8081"

    result = DeployHarnessLocalStep().execute(context, stack_path)

    assert result.success, result.errors
    run = next(c for c in cmd.commands if " run -d --name " in c)
    assert "LLMDBENCH_HARNESS_STACK_ENDPOINT_URL=http://10.0.0.7:8081" in run


def test_local_run_ignores_the_spec_endpoint(tmp_path: Path) -> None:
    """A local stack keeps its previous fallback exactly.

    Both endpoints are identical for a local stack, so reading the spec would
    change nothing -- and an explicit --endpoint-url must still win over a
    stale spec left over from an earlier standup.
    """
    cmd = _Command({" inspect ": _Result(stdout="exited 0\n")})
    context, stack_path = _context(tmp_path, cmd, _Logger())
    context.deployed_endpoints.clear()
    context.endpoint_url = "http://127.0.0.1:9999"
    _rendered_spec(stack_path)

    result = DeployHarnessLocalStep().execute(context, stack_path)

    assert result.success, result.errors
    run = next(c for c in cmd.commands if " run -d --name " in c)
    assert "LLMDBENCH_HARNESS_STACK_ENDPOINT_URL=http://127.0.0.1:9999" in run


def test_client_endpoint_from_step_03_is_swapped_for_the_in_host_one(
    tmp_path: Path,
) -> None:
    """Step 03 copies the CLI's client-side default into deployed_endpoints.

    So the swap cannot only cover an *empty* deployed_endpoints -- by the time
    this step runs in a standalone ``run``, the client URL is already recorded
    under the stack's name.
    """
    cmd = _remote_cmd()
    context, stack_path = _remote_context(tmp_path, cmd, _Logger())
    context.deployed_endpoints["stack"] = "http://10.0.0.7:8081"
    context.endpoint_url = "http://10.0.0.7:8081"
    _rendered_spec(stack_path)

    result = DeployHarnessLocalStep().execute(context, stack_path)

    assert result.success, result.errors
    run = next(c for c in cmd.commands if " run -d --name " in c)
    assert "LLMDBENCH_HARNESS_STACK_ENDPOINT_URL=http://localhost:8081" in run


def test_an_explicit_endpoint_url_is_not_overridden(tmp_path: Path) -> None:
    """--endpoint-url names a target the caller chose; the spec must not win.

    Only the CLI's derived default (which equals clientEndpoint) is replaced.
    Anything else -- a proxy, a second front door, another node -- is a
    deliberate choice and is passed through untouched.
    """
    cmd = _remote_cmd()
    context, stack_path = _remote_context(tmp_path, cmd, _Logger())
    context.deployed_endpoints["stack"] = "http://proxy.internal:9000"
    context.endpoint_url = "http://proxy.internal:9000"
    _rendered_spec(stack_path)

    result = DeployHarnessLocalStep().execute(context, stack_path)

    assert result.success, result.errors
    run = next(c for c in cmd.commands if " run -d --name " in c)
    assert "LLMDBENCH_HARNESS_STACK_ENDPOINT_URL=http://proxy.internal:9000" in run


def test_a_trailing_slash_still_counts_as_the_client_default(tmp_path: Path) -> None:
    """The comparison is on the URL, not on its punctuation."""
    cmd = _remote_cmd()
    context, stack_path = _remote_context(tmp_path, cmd, _Logger())
    context.deployed_endpoints["stack"] = "http://10.0.0.7:8081/"
    _rendered_spec(stack_path)

    result = DeployHarnessLocalStep().execute(context, stack_path)

    assert result.success, result.errors
    run = next(c for c in cmd.commands if " run -d --name " in c)
    assert "LLMDBENCH_HARNESS_STACK_ENDPOINT_URL=http://localhost:8081" in run
