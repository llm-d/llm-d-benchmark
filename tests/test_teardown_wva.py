"""Tests for WVA controller teardown policy in ``UninstallHelmStep._teardown_wva``.

Behavior under test:
- Full-scenario teardown (no ``--stack`` filter): controller is uninstalled.
- Partial-stack teardown (``--stack X`` filter set): controller is preserved.
- ``--deep``: controller is uninstalled regardless of filter.
- Per-stack VariantAutoscaling + HPA are always deleted, regardless of mode.
- Non-OpenShift platforms: WVA teardown is skipped entirely.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml

from llmdbenchmark.teardown.steps.step_01_uninstall_helm import UninstallHelmStep


# ---------------------------------------------------------------------------
# Stubs / fixtures
# ---------------------------------------------------------------------------


class _StubLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def log_info(self, msg: str, **_: Any) -> None:
        self.messages.append(msg)

    def log_warning(self, msg: str, **_: Any) -> None:
        self.messages.append(f"WARN: {msg}")

    def log_error(self, msg: str, **_: Any) -> None:
        self.messages.append(f"ERR: {msg}")


@dataclass
class _StubResult:
    success: bool = True
    stdout: str = ""
    stderr: str = ""


@dataclass
class _StubCmd:
    """Records every kube/helm invocation."""

    kube_calls: list[tuple] = field(default_factory=list)
    helm_calls: list[tuple] = field(default_factory=list)

    def kube(self, *args: str, **_: Any) -> _StubResult:
        self.kube_calls.append(args)
        return _StubResult(success=True)

    def helm(self, *args: str, **_: Any) -> _StubResult:
        self.helm_calls.append(args)
        return _StubResult(success=True)


@dataclass
class _StubContext:
    """Minimal stand-in for ExecutionContext sufficient for _teardown_wva."""

    rendered_stacks: list[Path] = field(default_factory=list)
    stack_filter: list[str] | None = None
    deep_clean: bool = False
    is_openshift: bool = True
    platform_type: str = "openshift"
    logger: _StubLogger = field(default_factory=_StubLogger)


def _write_stack(tmp_path: Path, name: str, *, wva_ns: str, model_id: str) -> Path:
    """Create a rendered-stack directory with a wva-enabled config.yaml."""
    stack_dir = tmp_path / name
    stack_dir.mkdir(parents=True)
    cfg = {
        "wva": {"enabled": True, "namespace": wva_ns},
        "namespace": {"name": wva_ns},
        "model_id_label": model_id,
    }
    (stack_dir / "config.yaml").write_text(yaml.safe_dump(cfg))
    return stack_dir


# ---------------------------------------------------------------------------
# Helpers for assertions
# ---------------------------------------------------------------------------


def _controller_was_uninstalled(cmd: _StubCmd) -> bool:
    return any(
        "uninstall" in args and "workload-variant-autoscaler" in args
        for args in cmd.helm_calls
    )


def _va_hpa_deleted_for(cmd: _StubCmd, model_id: str) -> bool:
    expected = f"{model_id}-decode"
    return any(
        "delete" in args and expected in args
        for args in cmd.kube_calls
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWvaTeardownPolicy:
    def test_full_scenario_uninstalls_controller(self, tmp_path: Path) -> None:
        """No --stack filter => controller is uninstalled."""
        step = UninstallHelmStep()
        ctx = _StubContext(
            rendered_stacks=[
                _write_stack(tmp_path, "stack-a", wva_ns="ns1", model_id="modelA"),
                _write_stack(tmp_path, "stack-b", wva_ns="ns1", model_id="modelB"),
            ],
            stack_filter=None,
            deep_clean=False,
        )
        cmd = _StubCmd()

        step._teardown_wva(cmd, ctx, errors=[])

        assert _controller_was_uninstalled(cmd), (
            f"Expected controller uninstall on full-scenario teardown; "
            f"helm calls={cmd.helm_calls}"
        )
        assert _va_hpa_deleted_for(cmd, "modelA")
        assert _va_hpa_deleted_for(cmd, "modelB")

    def test_partial_stack_preserves_controller(self, tmp_path: Path) -> None:
        """--stack filter present => controller is preserved."""
        step = UninstallHelmStep()
        ctx = _StubContext(
            rendered_stacks=[
                _write_stack(tmp_path, "stack-a", wva_ns="ns1", model_id="modelA"),
                _write_stack(tmp_path, "stack-b", wva_ns="ns1", model_id="modelB"),
            ],
            stack_filter=["stack-a"],
            deep_clean=False,
        )
        cmd = _StubCmd()

        step._teardown_wva(cmd, ctx, errors=[])

        assert not _controller_was_uninstalled(cmd), (
            f"Expected controller preservation under --stack filter; "
            f"helm calls={cmd.helm_calls}"
        )
        assert any(
            "Preserving WVA controller" in m for m in ctx.logger.messages
        ), f"Expected preservation log message; got: {ctx.logger.messages}"

    def test_deep_clean_uninstalls_controller_even_with_stack_filter(
        self, tmp_path: Path
    ) -> None:
        """--deep + --stack => controller is uninstalled (deep wins)."""
        step = UninstallHelmStep()
        ctx = _StubContext(
            rendered_stacks=[
                _write_stack(tmp_path, "stack-a", wva_ns="ns1", model_id="modelA"),
            ],
            stack_filter=["stack-a"],
            deep_clean=True,
        )
        cmd = _StubCmd()

        step._teardown_wva(cmd, ctx, errors=[])

        assert _controller_was_uninstalled(cmd), (
            f"Expected --deep to force controller uninstall; "
            f"helm calls={cmd.helm_calls}"
        )

    def test_non_openshift_skips_entirely(self, tmp_path: Path) -> None:
        """WVA teardown is a no-op on non-OpenShift platforms."""
        step = UninstallHelmStep()
        ctx = _StubContext(
            rendered_stacks=[
                _write_stack(tmp_path, "stack-a", wva_ns="ns1", model_id="modelA"),
            ],
            is_openshift=False,
            platform_type="kind",
        )
        cmd = _StubCmd()

        step._teardown_wva(cmd, ctx, errors=[])

        assert cmd.helm_calls == []
        assert cmd.kube_calls == []

    def test_full_scenario_multiple_namespaces(self, tmp_path: Path) -> None:
        """Full teardown uninstalls the controller in every wva-enabled namespace."""
        step = UninstallHelmStep()
        ctx = _StubContext(
            rendered_stacks=[
                _write_stack(tmp_path, "stack-a", wva_ns="ns1", model_id="modelA"),
                _write_stack(tmp_path, "stack-b", wva_ns="ns2", model_id="modelB"),
            ],
            stack_filter=None,
        )
        cmd = _StubCmd()

        step._teardown_wva(cmd, ctx, errors=[])

        # Controller uninstalled once per unique namespace
        ns_uninstalls = [
            args for args in cmd.helm_calls
            if "uninstall" in args and "workload-variant-autoscaler" in args
        ]
        namespaces = {args[args.index("--namespace") + 1] for args in ns_uninstalls}
        assert namespaces == {"ns1", "ns2"}, (
            f"Expected controller uninstall in both ns1 and ns2; "
            f"got {namespaces}"
        )

    @pytest.mark.parametrize(
        "stack_filter,deep,expected_uninstall",
        [
            (None, False, True),       # full scenario: uninstall
            (None, True, True),        # full scenario + deep: uninstall
            (["stack-a"], False, False),  # partial: preserve
            (["stack-a"], True, True),    # partial + deep: uninstall
        ],
    )
    def test_policy_matrix(
        self,
        tmp_path: Path,
        stack_filter: list[str] | None,
        deep: bool,
        expected_uninstall: bool,
    ) -> None:
        step = UninstallHelmStep()
        ctx = _StubContext(
            rendered_stacks=[
                _write_stack(tmp_path, "stack-a", wva_ns="ns1", model_id="modelA"),
            ],
            stack_filter=stack_filter,
            deep_clean=deep,
        )
        cmd = _StubCmd()

        step._teardown_wva(cmd, ctx, errors=[])

        assert _controller_was_uninstalled(cmd) == expected_uninstall, (
            f"stack_filter={stack_filter}, deep={deep}: "
            f"expected uninstall={expected_uninstall}, "
            f"helm calls={cmd.helm_calls}"
        )
