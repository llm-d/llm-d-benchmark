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


def _write_stack(
    tmp_path: Path,
    name: str,
    *,
    wva_ns: str,
    model_id: str,
    fma_enabled: bool = False,
) -> Path:
    """Create a rendered-stack directory with a wva-enabled config.yaml."""
    stack_dir = tmp_path / name
    stack_dir.mkdir(parents=True)
    cfg = {
        "wva": {"enabled": True, "namespace": wva_ns},
        "namespace": {"name": wva_ns},
        "model_id_label": model_id,
        "fma": {"enabled": fma_enabled},
    }
    (stack_dir / "config.yaml").write_text(yaml.safe_dump(cfg))
    kustomization = (
        "apiVersion: kustomize.config.k8s.io/v1beta1\n"
        "kind: Kustomization\n"
        f"namespace: {wva_ns}\n"
        "resources:\n"
        "- github.com/llm-d/llm-d-workload-variant-autoscaler/"
        "config/overlays/namespace-scoped/openshift?ref=main\n"
    )
    (stack_dir / "19_wva-kustomize.yaml").write_text(kustomization)
    return stack_dir


# ---------------------------------------------------------------------------
# Helpers for assertions
# ---------------------------------------------------------------------------


def _kustomize_delete_calls(cmd: _StubCmd) -> list[tuple]:
    """All ``kubectl delete -k <dir> ...`` invocations recorded on the stub."""
    return [
        args
        for args in cmd.kube_calls
        if len(args) >= 2 and args[0] == "delete" and "-k" in args
    ]


def _controller_was_uninstalled(cmd: _StubCmd) -> bool:
    """Detect a ``kubectl delete -k`` (kustomize-based controller uninstall)."""
    return len(_kustomize_delete_calls(cmd)) > 0


def _kustomize_delete_namespaces(cmd: _StubCmd) -> set[str]:
    """Read the staged kustomization.yaml at each delete-k call's tempdir
    and pull out the ``namespace:`` field. Used to verify per-namespace
    coverage when multiple WVA namespaces are torn down in one pass."""
    namespaces: set[str] = set()
    for args in _kustomize_delete_calls(cmd):
        # args looks like ("delete", "-k", "<tempdir>", "--ignore-not-found")
        idx = args.index("-k") + 1
        kustomize_dir = Path(args[idx])
        kfile = kustomize_dir / "kustomization.yaml"
        if not kfile.exists():
            continue
        body = yaml.safe_load(kfile.read_text()) or {}
        ns = body.get("namespace")
        if ns:
            namespaces.add(ns)
    return namespaces


def _va_hpa_deleted_for(cmd: _StubCmd, model_id: str, *, fma: bool = False) -> bool:
    suffix = "fma" if fma else "decode"
    expected = f"{model_id}-{suffix}"
    return any("delete" in args and expected in args for args in cmd.kube_calls)


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
            f"kube calls={cmd.kube_calls}"
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
            f"kube calls={cmd.kube_calls}"
        )
        assert any("Preserving WVA controller" in m for m in ctx.logger.messages), (
            f"Expected preservation log message; got: {ctx.logger.messages}"
        )

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
            f"kube calls={cmd.kube_calls}"
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

        # Controller uninstalled once per unique WVA namespace -- verified by
        # reading the staged kustomization.yaml from each delete-k tempdir.
        assert _kustomize_delete_namespaces(cmd) == {"ns1", "ns2"}, (
            f"Expected controller uninstall in both ns1 and ns2; "
            f"got namespaces={_kustomize_delete_namespaces(cmd)}; "
            f"kube calls={cmd.kube_calls}"
        )

    def test_fma_enabled_stack_uses_fma_suffix(self, tmp_path: Path) -> None:
        """Under fma.enabled the per-stack VA + HPA names use ``-fma`` suffix."""
        step = UninstallHelmStep()
        ctx = _StubContext(
            rendered_stacks=[
                _write_stack(
                    tmp_path,
                    "stack-fma",
                    wva_ns="ns1",
                    model_id="modelA",
                    fma_enabled=True,
                ),
            ],
            stack_filter=None,
        )
        cmd = _StubCmd()

        step._teardown_wva(cmd, ctx, errors=[])

        assert _va_hpa_deleted_for(cmd, "modelA", fma=True), (
            f"Expected VA/HPA delete with -fma suffix under fma.enabled; "
            f"kube calls={cmd.kube_calls}"
        )
        assert not _va_hpa_deleted_for(cmd, "modelA", fma=False), (
            f"Did not expect -decode suffix when fma.enabled is true; "
            f"kube calls={cmd.kube_calls}"
        )

    @pytest.mark.parametrize(
        "stack_filter,deep,expected_uninstall",
        [
            (None, False, True),  # full scenario: uninstall
            (None, True, True),  # full scenario + deep: uninstall
            (["stack-a"], False, False),  # partial: preserve
            (["stack-a"], True, True),  # partial + deep: uninstall
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
            f"kube calls={cmd.kube_calls}"
        )
