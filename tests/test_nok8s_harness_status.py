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
    """A failed or unparseable inspect is reported, not silently ignored."""
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
