"""Tests for what runs around an ``update``: the chained smoketest, the
deployed-stack check and the sibling-stack warning.

A phase that filters its own steps (``update``, or ``standup -s``) must not
pass its step numbers to the smoketest it chains: those numbers mean nothing
there and would select no smoketest step at all, reporting a pass for checks
that never ran.
"""

from __future__ import annotations

import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from llmdbenchmark.parser.cli_overrides import GLOBAL_SELECTOR
from llmdbenchmark.update import runner


def _patch_smoketest(monkeypatch, tmp_path, captured):
    from llmdbenchmark import cli

    monkeypatch.setattr(cli.config, "plan_dir", tmp_path, raising=False)
    monkeypatch.setattr(cli.config, "workspace", tmp_path, raising=False)
    monkeypatch.setattr(cli, "_load_all_stacks_info", lambda paths: [{}])
    monkeypatch.setattr(cli, "_resolve_deploy_methods", lambda *a, **kw: ["nok8s"])
    monkeypatch.setattr(cli, "_parse_namespaces", lambda *a, **kw: ("llmdbench", None))
    monkeypatch.setattr(cli, "get_smoketest_steps", list)

    class _Result:
        has_errors = False

    class _Executor:
        def __init__(self, **kwargs):
            pass

        def execute(self, step_spec=None):
            captured["step_spec"] = step_spec
            return _Result()

    monkeypatch.setattr(cli, "StepExecutor", _Executor)
    return cli


def _plan():
    return types.SimpleNamespace(rendered_paths=[])


def test_standalone_smoketest_honors_its_own_step_flag(monkeypatch, tmp_path):
    captured = {}
    cli = _patch_smoketest(monkeypatch, tmp_path, captured)

    cli._execute_smoketest(types.SimpleNamespace(step="1"), MagicMock(), _plan())

    assert captured["step_spec"] == "1"


def test_chained_smoketest_ignores_the_callers_step_filter(monkeypatch, tmp_path):
    """update computes a spec like "6,8"; the smoketest has no such steps."""
    captured = {}
    cli = _patch_smoketest(monkeypatch, tmp_path, captured)

    args = types.SimpleNamespace(step="6,8", skip_smoketest=False)
    context = types.SimpleNamespace(deployed_methods=["modelservice"])
    cli._chain_smoketest(args, MagicMock(), _plan(), context)

    assert captured["step_spec"] is None


def test_chained_smoketest_honors_skip(monkeypatch, tmp_path):
    captured = {}
    cli = _patch_smoketest(monkeypatch, tmp_path, captured)

    args = types.SimpleNamespace(step=None, skip_smoketest=True)
    context = types.SimpleNamespace(deployed_methods=["modelservice"])
    cli._chain_smoketest(args, MagicMock(), _plan(), context)

    assert captured == {}


def test_chained_smoketest_is_skipped_for_nok8s(monkeypatch, tmp_path):
    captured = {}
    cli = _patch_smoketest(monkeypatch, tmp_path, captured)

    args = types.SimpleNamespace(step=None, skip_smoketest=False)
    context = types.SimpleNamespace(deployed_methods=["nok8s"])
    cli._chain_smoketest(args, MagicMock(), _plan(), context)

    assert captured == {}


@pytest.mark.parametrize("phase", ["_execute_standup", "_execute_update"])
def test_both_deploy_phases_chain_the_smoketest(monkeypatch, phase):
    from llmdbenchmark import cli

    context = types.SimpleNamespace(deployed_methods=["modelservice"])
    chained = []
    monkeypatch.setattr(cli, "_do_standup", lambda *a: (context, object()))
    monkeypatch.setattr(cli, "_do_update", lambda *a: (context, object()))
    monkeypatch.setattr(cli, "_print_standup_summary", lambda *a: None)
    monkeypatch.setattr(cli, "_chain_smoketest", lambda *a: chained.append(a[-1]))

    args = types.SimpleNamespace(step="6,8")
    if phase == "_execute_update":
        cli._execute_update(args, MagicMock(), _plan(), set())
    else:
        cli._execute_standup(args, MagicMock(), _plan())
    assert chained == [context]


def test_a_no_op_update_chains_nothing(monkeypatch):
    from llmdbenchmark import cli

    monkeypatch.setattr(cli, "_do_update", lambda *a: (object(), None))
    monkeypatch.setattr(
        cli, "_chain_smoketest", lambda *a: pytest.fail("must not chain")
    )
    cli._execute_update(types.SimpleNamespace(), MagicMock(), _plan(), set())


# ---------------------------------------------------------------------------
# Multi-stack safety: siblings share a namespace but own their releases
# ---------------------------------------------------------------------------


class _Cmd:
    """Answers helm list with *releases* and kubectl get with *probe*."""

    def __init__(self, releases=None, probe="", helm_ok=True):
        import json

        self._releases = json.dumps([{"name": name} for name in releases or []])
        self._probe = probe
        self._helm_ok = helm_ok
        self.helm_namespaces = []
        self.kube_calls = []

    def helm(self, *args, **kwargs):
        self.helm_namespaces.append(args[args.index("--namespace") + 1])
        return SimpleNamespace(success=self._helm_ok, stdout=self._releases)

    def kube(self, *args, **kwargs):
        self.kube_calls.append(args)
        return SimpleNamespace(success=True, stdout=self._probe)


def _ctx(cmd, *, stack_filter=None, methods=("modelservice",)):
    return SimpleNamespace(
        dry_run=False,
        container_only=False,
        stack_filter=stack_filter,
        deployed_methods=list(methods),
        require_namespace=lambda: "ns",
        require_cmd=lambda: cmd,
        resolve_cluster=lambda: None,
    )


def _stacks(*labels, namespace="ns", guide=None):
    return [
        {
            "stack_name": label.removeprefix("lab-"),
            "model_id_label": label,
            "namespace": namespace,
            "kustomize_guide": guide,
        }
        for label in labels
    ]


def test_guard_passes_when_every_stack_has_a_release():
    cmd = _Cmd(["lab-a-ms", "lab-b-router"])
    assert (
        runner.missing_stacks(_ctx(cmd), _stacks("lab-a", "lab-b"), MagicMock()) == []
    )


def test_guard_fails_for_a_stack_whose_sibling_is_deployed():
    """ "Some release exists here" must not green-light a missing stack."""
    cmd = _Cmd(["lab-a-ms"])
    missing = runner.missing_stacks(_ctx(cmd), _stacks("lab-a", "lab-b"), MagicMock())
    assert missing == ["b (namespace ns)"]


def test_guard_honors_the_stack_filter():
    cmd = _Cmd(["lab-a-ms"])
    ctx = _ctx(cmd, stack_filter=["a"])
    assert runner.missing_stacks(ctx, _stacks("lab-a", "lab-b"), MagicMock()) == []


def test_guard_skips_when_releases_cannot_be_listed():
    cmd = _Cmd(helm_ok=False)
    logger = MagicMock()
    assert runner.missing_stacks(_ctx(cmd), _stacks("lab-a"), logger) == []
    assert logger.log_warning.called


def test_guard_asks_helm_for_the_live_states():
    """uninstalling must stay in, or an interrupted teardown reads as "never
    stood up"; uninstalled/superseded must stay out, or a kept-history
    uninstall reads as deployed."""
    seen = []
    cmd = _Cmd(["lab-a-ms"])
    original = cmd.helm
    cmd.helm = lambda *a, **k: seen.extend(a) or original(*a, **k)
    runner.missing_stacks(_ctx(cmd), _stacks("lab-a"), MagicMock())
    assert {"--deployed", "--failed", "--pending", "--uninstalling"} <= set(seen)
    assert "--uninstalled" not in seen and "--superseded" not in seen


def test_guard_lists_each_namespace_once():
    cmd = _Cmd(["lab-a-ms", "lab-b-ms", "lab-c-ms"])
    stacks = _stacks("lab-a", "lab-b") + _stacks("lab-c", namespace="other-ns")
    runner.missing_stacks(_ctx(cmd), stacks, MagicMock())
    assert cmd.helm_namespaces == ["ns", "other-ns"]


def test_guard_is_skipped_under_dry_run():
    cmd = _Cmd()
    ctx = _ctx(cmd)
    ctx.dry_run = True
    assert runner.missing_stacks(ctx, _stacks("lab-a"), MagicMock()) == []
    assert cmd.helm_namespaces == []


# ---------------------------------------------------------------------------
# Methods with no -ms/-router release are found by their own workload
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method, expected",
    [
        ("standalone", ("get", "deployment", "vllm-standalone-lab-a")),
        ("fma", ("get", "deployment", "-l", "app=fma-requester-lab-a")),
        ("kustomize", ("get", "pods", "-l", "llm-d.ai/guide=guide-x")),
    ],
)
def test_guard_probes_the_stacks_own_workload(method, expected):
    """Probed per stack: a sibling's workload must not stand in for it."""
    cmd = _Cmd(probe="deployment.apps/x\n")
    stacks = _stacks("lab-a", guide="guides/guide-x")
    assert (
        runner.missing_stacks(_ctx(cmd, methods=(method,)), stacks, MagicMock()) == []
    )
    assert cmd.kube_calls[0][: len(expected)] == expected
    assert cmd.helm_namespaces == [], "no helm release to look for"


@pytest.mark.parametrize("method", ["fma", "standalone", "kustomize"])
def test_guard_fails_when_the_workload_is_missing(method):
    cmd = _Cmd(probe="")
    stacks = _stacks("lab-a", guide="guide-x")
    missing = runner.missing_stacks(_ctx(cmd, methods=(method,)), stacks, MagicMock())
    assert missing == ["a (namespace ns)"]


def test_a_failed_cluster_check_is_a_phase_error(monkeypatch):
    from llmdbenchmark import cli

    context = SimpleNamespace(deployed_methods=["modelservice"])
    monkeypatch.setattr(cli, "_build_standup_context", lambda *a, **k: (context, []))
    monkeypatch.setattr(cli.update_runner, "step_spec", lambda *a: "8")

    def _boom(*a):
        raise RuntimeError("cluster unreachable")

    monkeypatch.setattr(cli.update_runner, "missing_stacks", _boom)
    with pytest.raises(cli.PhaseError, match="cluster unreachable"):
        cli._do_update(SimpleNamespace(), MagicMock(), _plan(), {"vllm"})


# ---------------------------------------------------------------------------
# Sibling warning
# ---------------------------------------------------------------------------


def _sibling_ctx(*names, stack_filter=None):
    from pathlib import Path

    return SimpleNamespace(
        rendered_stacks=[Path(name) for name in names], stack_filter=stack_filter
    )


def _warns(args, ctx) -> bool:
    logger = MagicMock()
    runner.warn_sibling_stacks(args, ctx, logger)
    return logger.log_warning.called


def test_unscoped_multi_stack_update_warns():
    args = SimpleNamespace(
        user_set_overrides_by_stack={GLOBAL_SELECTOR: {"decode": {"replicas": 4}}}
    )
    assert _warns(args, _sibling_ctx("a", "b"))


def test_a_changed_flag_warns():
    args = SimpleNamespace(user_set_overrides_by_stack={}, changed_flags=["affinity"])
    assert _warns(args, _sibling_ctx("a", "b"))


def test_stack_scoped_override_does_not_warn():
    """Synthetic global overrides (--no-pvc, --cluster-config) do not count."""
    args = SimpleNamespace(
        user_set_overrides_by_stack={"a": {"decode": {"replicas": 4}}},
        setup_overrides_by_stack={
            GLOBAL_SELECTOR: {"storage": {"x": 1}},
            "a": {"decode": {"replicas": 4}},
        },
    )
    assert not _warns(args, _sibling_ctx("a", "b"))


def test_single_stack_does_not_warn():
    args = SimpleNamespace(
        user_set_overrides_by_stack={GLOBAL_SELECTOR: {"decode": {"replicas": 4}}}
    )
    assert not _warns(args, _sibling_ctx("a"))
    assert not _warns(args, _sibling_ctx("a", "b", stack_filter=["a"]))
