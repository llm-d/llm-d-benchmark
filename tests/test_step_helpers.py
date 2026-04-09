"""Unit tests for the Step base-class helper methods.

Covers ``Step.success_result``, ``Step.failure_result``, and
``Step.start`` -- the prologue/epilogue helpers that all step
implementations rely on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from llmdbenchmark.executor.step import (
    Phase,
    Step,
    StepPrologue,
    StepResult,
)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


class _DummyStep(Step):
    """Concrete Step subclass for testing the base helpers."""

    def __init__(self, *, per_stack: bool = False, number: int = 5, name: str = "dummy"):
        super().__init__(
            number=number,
            name=name,
            description="dummy step for testing",
            phase=Phase.STANDUP,
            per_stack=per_stack,
        )

    def execute(self, context, stack_path=None):  # pragma: no cover - not used by tests
        raise NotImplementedError


class _RecordingLogger:
    """Captures log calls so tests can assert on them."""

    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.infos: list[str] = []

    def log_error(self, msg, **kwargs):
        self.errors.append(msg)

    def log_warning(self, msg, **kwargs):
        self.warnings.append(msg)

    def log_info(self, msg, **kwargs):
        self.infos.append(msg)


class _StubCommandExecutor:
    """Stand-in for CommandExecutor."""


class _StubContext:
    """Minimal ExecutionContext stand-in for testing Step.start()."""

    def __init__(
        self,
        rendered_stacks: list[Path] | None = None,
        cmd_obj: Any = None,
    ):
        self.rendered_stacks = rendered_stacks or []
        self._cmd = cmd_obj
        self.logger = _RecordingLogger()

    def require_cmd(self):
        if self._cmd is None:
            raise RuntimeError("CommandExecutor not initialised")
        return self._cmd


@pytest.fixture
def stack_dir(tmp_path: Path) -> Path:
    """Create a stack directory with a valid config.yaml."""
    stack = tmp_path / "stack-a"
    stack.mkdir()
    config = {
        "namespace": {"name": "test-ns"},
        "harness": {"namespace": "test-harness"},
        "model": {"name": "facebook/opt-125m"},
    }
    (stack / "config.yaml").write_text(yaml.safe_dump(config))
    return stack


@pytest.fixture
def empty_stack_dir(tmp_path: Path) -> Path:
    """Create a stack directory with NO config.yaml."""
    stack = tmp_path / "stack-empty"
    stack.mkdir()
    return stack


# ---------------------------------------------------------------------------
# Step.success_result()
# ---------------------------------------------------------------------------


class TestSuccessResult:
    """Tests for ``Step.success_result()``."""

    def test_basic_success(self):
        step = _DummyStep(number=3, name="health_check")
        result = step.success_result("All checks passed")

        assert isinstance(result, StepResult)
        assert result.success is True
        assert result.step_number == 3
        assert result.step_name == "health_check"
        assert result.message == "All checks passed"
        assert result.errors == []
        assert result.stack_name is None
        assert result.context == {}

    def test_success_with_stack_name(self):
        step = _DummyStep(per_stack=True)
        result = step.success_result("ok", stack_name="gpu-prod")

        assert result.success is True
        assert result.stack_name == "gpu-prod"

    def test_success_with_context_dict(self):
        step = _DummyStep()
        result = step.success_result("ok", context={"endpoint": "http://x:8000"})

        assert result.context == {"endpoint": "http://x:8000"}

    def test_success_default_message(self):
        step = _DummyStep()
        result = step.success_result()

        assert result.success is True
        assert result.message == ""

    def test_success_has_errors_property(self):
        step = _DummyStep()
        result = step.success_result("ok")

        assert result.has_errors is False


# ---------------------------------------------------------------------------
# Step.failure_result()
# ---------------------------------------------------------------------------


class TestFailureResult:
    """Tests for ``Step.failure_result()``."""

    def test_basic_failure(self):
        step = _DummyStep(number=7, name="deploy")
        result = step.failure_result("deploy failed", ["pod crashed", "timeout"])

        assert isinstance(result, StepResult)
        assert result.success is False
        assert result.step_number == 7
        assert result.step_name == "deploy"
        assert result.message == "deploy failed"
        assert result.errors == ["pod crashed", "timeout"]
        assert result.stack_name is None

    def test_failure_with_stack_name(self):
        step = _DummyStep(per_stack=True)
        result = step.failure_result("oops", ["err"], stack_name="my-stack")

        assert result.stack_name == "my-stack"

    def test_failure_logs_errors_when_logger_provided(self):
        step = _DummyStep()
        logger = _RecordingLogger()

        step.failure_result("oops", ["err1", "err2"], logger=logger)

        assert logger.errors == ["    err1", "    err2"]

    def test_failure_does_not_log_without_logger(self):
        step = _DummyStep()
        # No logger -- must not raise.
        result = step.failure_result("oops", ["err1"])

        assert result.errors == ["err1"]

    def test_failure_log_errors_disabled(self):
        step = _DummyStep()
        logger = _RecordingLogger()

        step.failure_result("oops", ["err"], logger=logger, log_errors=False)

        assert logger.errors == []

    def test_failure_with_empty_error_list_does_not_log(self):
        step = _DummyStep()
        logger = _RecordingLogger()

        step.failure_result("oops", [], logger=logger)

        assert logger.errors == []

    def test_failure_errors_list_is_copied(self):
        """Mutating the input list after the call must not affect the result."""
        step = _DummyStep()
        original_errors = ["a", "b"]
        result = step.failure_result("oops", original_errors)

        original_errors.append("c")

        assert result.errors == ["a", "b"]

    def test_failure_has_errors_property(self):
        step = _DummyStep()
        result = step.failure_result("oops", ["e"])

        assert result.has_errors is True


# ---------------------------------------------------------------------------
# Step.start() - per-stack validation
# ---------------------------------------------------------------------------


class TestStartPerStackValidation:
    """``Step.start()`` per-stack validation behavior."""

    def test_per_stack_step_without_stack_path_returns_failure(self):
        step = _DummyStep(per_stack=True)
        ctx = _StubContext()

        result = step.start(ctx, stack_path=None)

        assert isinstance(result, StepResult)
        assert result.success is False
        assert result.message == "No stack path provided for per-stack step"
        assert result.errors == ["stack_path is required"]

    def test_global_step_without_stack_path_does_not_fail_validation(self, stack_dir):
        """Global steps must accept stack_path=None and proceed to config load."""
        step = _DummyStep(per_stack=False)
        ctx = _StubContext(rendered_stacks=[stack_dir], cmd_obj=_StubCommandExecutor())

        result = step.start(ctx, stack_path=None)

        # Should be a successful prologue, not a failure result
        assert isinstance(result, StepPrologue)
        assert result.stack_path is None
        assert result.stack_name is None
        assert result.plan_config is not None
        assert result.plan_config["namespace"]["name"] == "test-ns"

    def test_per_stack_step_with_stack_path_proceeds(self, stack_dir):
        step = _DummyStep(per_stack=True)
        ctx = _StubContext(cmd_obj=_StubCommandExecutor())

        result = step.start(ctx, stack_path=stack_dir)

        assert isinstance(result, StepPrologue)
        assert result.stack_path == stack_dir
        assert result.stack_name == stack_dir.name


# ---------------------------------------------------------------------------
# Step.start() - config loading
# ---------------------------------------------------------------------------


class TestStartConfigLoading:
    """``Step.start()`` config-loading behavior."""

    def test_global_loads_plan_config_from_first_stack(self, stack_dir):
        step = _DummyStep(per_stack=False)
        ctx = _StubContext(rendered_stacks=[stack_dir], cmd_obj=_StubCommandExecutor())

        prologue = step.start(ctx)

        assert isinstance(prologue, StepPrologue)
        assert prologue.plan_config is not None
        assert prologue.plan_config["model"]["name"] == "facebook/opt-125m"

    def test_per_stack_loads_config_from_stack_path(self, stack_dir):
        step = _DummyStep(per_stack=True)
        ctx = _StubContext(cmd_obj=_StubCommandExecutor())

        prologue = step.start(ctx, stack_path=stack_dir)

        assert isinstance(prologue, StepPrologue)
        assert prologue.plan_config is not None
        assert prologue.plan_config["namespace"]["name"] == "test-ns"

    def test_global_with_no_rendered_stacks_returns_failure(self):
        step = _DummyStep(per_stack=False)
        ctx = _StubContext(rendered_stacks=[], cmd_obj=_StubCommandExecutor())

        result = step.start(ctx)

        assert isinstance(result, StepResult)
        assert result.success is False
        assert result.message == "Could not load plan configuration"
        assert "No rendered stack configuration found" in result.errors

    def test_per_stack_with_missing_config_yaml_returns_failure(self, empty_stack_dir):
        step = _DummyStep(per_stack=True)
        ctx = _StubContext(cmd_obj=_StubCommandExecutor())

        result = step.start(ctx, stack_path=empty_stack_dir)

        assert isinstance(result, StepResult)
        assert result.success is False
        assert result.message == "Could not load plan configuration"
        assert result.stack_name == empty_stack_dir.name

    def test_load_config_disabled_skips_load(self):
        """When load_config=False, missing config does not fail."""
        step = _DummyStep(per_stack=False)
        ctx = _StubContext(rendered_stacks=[], cmd_obj=_StubCommandExecutor())

        prologue = step.start(ctx, load_config=False)

        assert isinstance(prologue, StepPrologue)
        assert prologue.plan_config is None


# ---------------------------------------------------------------------------
# Step.start() - command executor
# ---------------------------------------------------------------------------


class TestStartCommandExecutor:
    """``Step.start()`` CommandExecutor handling."""

    def test_require_cmd_returns_command_executor(self, stack_dir):
        step = _DummyStep()
        cmd_obj = _StubCommandExecutor()
        ctx = _StubContext(rendered_stacks=[stack_dir], cmd_obj=cmd_obj)

        prologue = step.start(ctx)

        assert isinstance(prologue, StepPrologue)
        assert prologue.cmd is cmd_obj

    def test_require_cmd_disabled_returns_none(self, stack_dir):
        step = _DummyStep()
        ctx = _StubContext(rendered_stacks=[stack_dir], cmd_obj=None)

        prologue = step.start(ctx, require_cmd=False)

        assert isinstance(prologue, StepPrologue)
        assert prologue.cmd is None

    def test_require_cmd_propagates_runtime_error(self, stack_dir):
        """If context.require_cmd() raises, the exception bubbles up."""
        step = _DummyStep()
        ctx = _StubContext(rendered_stacks=[stack_dir], cmd_obj=None)

        with pytest.raises(RuntimeError, match="CommandExecutor not initialised"):
            step.start(ctx)


# ---------------------------------------------------------------------------
# Step.start() - prologue contents
# ---------------------------------------------------------------------------


class TestStartPrologueContents:
    """The shape of the StepPrologue returned on success."""

    def test_errors_list_is_fresh_empty_list(self, stack_dir):
        step = _DummyStep()
        ctx = _StubContext(rendered_stacks=[stack_dir], cmd_obj=_StubCommandExecutor())

        prologue = step.start(ctx)

        assert isinstance(prologue, StepPrologue)
        assert prologue.errors == []

        # Mutating one prologue's errors should not affect a fresh prologue.
        prologue.errors.append("oops")
        prologue2 = step.start(ctx)
        assert isinstance(prologue2, StepPrologue)
        assert prologue2.errors == []

    def test_stack_name_extracted_from_stack_path(self, stack_dir):
        step = _DummyStep(per_stack=True)
        ctx = _StubContext(cmd_obj=_StubCommandExecutor())

        prologue = step.start(ctx, stack_path=stack_dir)

        assert isinstance(prologue, StepPrologue)
        assert prologue.stack_name == stack_dir.name
