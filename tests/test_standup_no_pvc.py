"""standup --no-pvc: flag plumbing, overrides, and step gates."""

from __future__ import annotations

import argparse

from llmdbenchmark.interface import standup as standup_interface


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    standup_interface.add_subcommands(subparsers, parents=[])
    return parser.parse_args(argv)


def test_standup_no_pvc_defaults_false() -> None:
    assert _parse(["standup"]).no_pvc is False


def test_standup_no_pvc_parses_true() -> None:
    assert _parse(["standup", "--no-pvc"]).no_pvc is True


def test_standup_no_pvc_env_var(monkeypatch) -> None:
    monkeypatch.setenv("LLMDBENCH_NO_PVC", "1")
    assert _parse(["standup"]).no_pvc is True


def test_no_pvc_overrides_for_standup() -> None:
    from llmdbenchmark.cli import _no_pvc_standup_overrides

    args = argparse.Namespace(command="standup", no_pvc=True)
    assert _no_pvc_standup_overrides(args) == {
        "modelservice": {"uriProtocol": "hf"},
        "standalone": {"mountModelVolume": False},
    }


def test_no_pvc_overrides_empty_without_flag() -> None:
    from llmdbenchmark.cli import _no_pvc_standup_overrides

    assert (
        _no_pvc_standup_overrides(argparse.Namespace(command="standup", no_pvc=False))
        == {}
    )


def test_no_pvc_overrides_empty_for_run() -> None:
    """run --no-pvc must NOT redirect model storage -- run deploys nothing."""
    from llmdbenchmark.cli import _no_pvc_standup_overrides

    assert (
        _no_pvc_standup_overrides(argparse.Namespace(command="run", no_pvc=True)) == {}
    )


def test_step04_rejects_hostpath_with_no_pvc(tmp_path) -> None:
    import yaml as _yaml

    from llmdbenchmark.standup.steps.step_04_model_namespace import (
        ModelNamespaceStep,
    )
    from llmdbenchmark.executor.context import ExecutionContext

    class _Logger:
        def log_info(self, *a, **k): ...
        def log_warning(self, *a, **k): ...
        def log_error(self, *a, **k): ...

    stack = tmp_path / "plan" / "stack01"
    stack.mkdir(parents=True)
    (stack / "config.yaml").write_text(
        _yaml.dump({"storage": {"hostPath": {"enabled": True}}}),
        encoding="utf-8",
    )
    context = ExecutionContext(
        plan_dir=tmp_path / "plan",
        workspace=tmp_path,
        logger=_Logger(),
        namespace="model-ns",
        no_pvc=True,
        rendered_stacks=[stack],
        dry_run=True,
    )
    result = ModelNamespaceStep()._check_no_pvc_hostpath_conflict(context)
    assert result is not None
    assert "hostPath" in result and "--no-pvc" in result


def test_step05_no_pvc_skips_pvc_and_data_access(tmp_path) -> None:
    from llmdbenchmark.executor.command import CommandResult
    from llmdbenchmark.executor.context import ExecutionContext
    from llmdbenchmark.standup.steps.step_05_harness_namespace import (
        HarnessNamespaceStep,
    )

    class _Logger:
        def log_info(self, *a, **k): ...
        def log_warning(self, *a, **k): ...
        def log_error(self, *a, **k): ...

    class _Cmd:
        def __init__(self):
            self.kube_calls: list[tuple] = []
            self.pvc_waits = 0
            self.pod_waits = 0

        def kube(self, *args, **kwargs) -> CommandResult:
            self.kube_calls.append(args)
            if args[:2] == ("get", "namespace"):
                return CommandResult(command="get ns", exit_code=1)
            return CommandResult(command=" ".join(str(a) for a in args), exit_code=0)

        def wait_for_pvc(self, **k) -> CommandResult:
            self.pvc_waits += 1
            return CommandResult(command="wait pvc", exit_code=0)

        def wait_for_pods(self, **k) -> CommandResult:
            self.pod_waits += 1
            return CommandResult(command="wait pods", exit_code=0)

    context = ExecutionContext(
        plan_dir=tmp_path,
        workspace=tmp_path,
        logger=_Logger(),
        namespace="bench",
        harness_namespace="bench",
        no_pvc=True,
    )
    cmd = _Cmd()
    context.cmd = cmd

    result = HarnessNamespaceStep().execute(context)
    assert result.success
    assert "--no-pvc" in result.message or "PVC" in result.message
    # Namespace apply happened; no PVC bind wait, no data-access pod wait.
    assert any(c[0] == "apply" for c in cmd.kube_calls)
    assert cmd.pvc_waits == 0
    assert cmd.pod_waits == 0


def test_requires_pvc_download_standalone_respects_mount_flag() -> None:
    """--no-pvc sets standalone.mountModelVolume=false but not
    standalone.enabled -- _requires_pvc_download must honor the mount
    flag so standalone stacks don't still get a model PVC + download job
    while the deployment template omits the model-cache volume mount."""
    from llmdbenchmark.standup.steps.step_04_model_namespace import (
        ModelNamespaceStep,
    )

    step = ModelNamespaceStep()

    no_pvc_cfg = {
        "standalone": {"enabled": True, "mountModelVolume": False},
        "modelservice": {"uriProtocol": "hf"},
    }
    assert step._requires_pvc_download(no_pvc_cfg) is False

    default_cfg = {
        "standalone": {"enabled": True},
        "modelservice": {"uriProtocol": "hf"},
    }
    assert step._requires_pvc_download(default_cfg) is True


def test_step04_no_pvc_skips_storage_class_validation(tmp_path, monkeypatch) -> None:
    """--no-pvc: no standup PVCs exist, so validating a StorageClass
    against the cluster (which PVC-restricted users often cannot list)
    must not happen."""
    import yaml as _yaml

    from llmdbenchmark.executor.command import CommandResult
    from llmdbenchmark.executor.context import ExecutionContext
    from llmdbenchmark.standup.steps.step_04_model_namespace import (
        ModelNamespaceStep,
    )

    class _Logger:
        def log_info(self, *a, **k): ...
        def log_warning(self, *a, **k): ...
        def log_error(self, *a, **k): ...

    class _Cmd:
        def kube(self, *args, **kwargs) -> CommandResult:
            return CommandResult(command=" ".join(str(a) for a in args), exit_code=0)

    stack = tmp_path / "plan" / "stack01"
    stack.mkdir(parents=True)
    (stack / "config.yaml").write_text(
        _yaml.dump(
            {
                "modelservice": {"uriProtocol": "hf"},
                "control": {"contextSecretName": "ctx-secret"},
            }
        ),
        encoding="utf-8",
    )

    context = ExecutionContext(
        plan_dir=tmp_path / "plan",
        workspace=tmp_path,
        logger=_Logger(),
        namespace="model-ns",
        no_pvc=True,
        rendered_stacks=[stack],
        dry_run=False,
    )
    context.cmd = _Cmd()

    def _must_not_be_called(self, cmd, context):
        raise AssertionError(
            "_validate_storage_class must not be called under --no-pvc"
        )

    monkeypatch.setattr(
        ModelNamespaceStep, "_validate_storage_class", _must_not_be_called
    )

    result = ModelNamespaceStep().execute(context)
    assert result.success
