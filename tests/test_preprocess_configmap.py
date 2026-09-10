"""The shared preprocess-ConfigMap helper applies one ConfigMap per namespace."""

from __future__ import annotations

from typing import Any

from llmdbenchmark.executor.command import CommandResult
from llmdbenchmark.executor.context import ExecutionContext


class _Logger:
    def log_info(self, *a: Any, **k: Any) -> None: ...
    def log_warning(self, *a: Any, **k: Any) -> None: ...
    def log_error(self, *a: Any, **k: Any) -> None: ...


class _FakeCmd:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def kube(self, *args: Any, **kwargs: Any) -> CommandResult:
        self.calls.append(args)
        return CommandResult(
            command=" ".join(str(a) for a in args),
            exit_code=0,
            stdout="apiVersion: v1\nkind: ConfigMap\n",
        )


def test_creates_configmap_in_each_namespace(tmp_path) -> None:
    from llmdbenchmark.utilities.preprocess_configmap import (
        create_preprocess_configmap,
    )

    context = ExecutionContext(plan_dir=tmp_path, workspace=tmp_path, logger=_Logger())
    cmd = _FakeCmd()
    create_preprocess_configmap(cmd, context, ["ns-a", "ns-b"])

    create_calls = [c for c in cmd.calls if c[0] == "create"]
    apply_calls = [c for c in cmd.calls if c[0] == "apply"]
    assert len(create_calls) == 2
    assert len(apply_calls) == 2
    namespaces = {c[c.index("--namespace") + 1] for c in create_calls}
    assert namespaces == {"ns-a", "ns-b"}
    # The rendered manifest lands under workspace/setup/yamls, one per ns.
    yamls = list((tmp_path / "setup" / "yamls").glob("preprocesses-configmap*"))
    assert len(yamls) == 2


def test_standup_step_04_creates_model_ns_configmap(tmp_path, monkeypatch) -> None:
    """Step 04 owns the model-ns copy so serving pods have it even though
    harness prep no longer runs in standup."""
    from llmdbenchmark.standup.steps import step_04_model_namespace as s4

    captured: list[list[str]] = []

    def _fake_create(cmd, context, namespaces):
        captured.append(list(namespaces))

    monkeypatch.setattr(s4, "create_preprocess_configmap", _fake_create)
    context = ExecutionContext(
        plan_dir=tmp_path,
        workspace=tmp_path,
        logger=_Logger(),
        namespace="model-ns",
    )
    s4.ModelNamespaceStep()._create_model_preprocess_configmap(_FakeCmd(), context)
    assert captured == [["model-ns"]]


def test_step_04_configmap_tolerates_missing_namespace(tmp_path, monkeypatch) -> None:
    from llmdbenchmark.standup.steps import step_04_model_namespace as s4

    captured: list[list[str]] = []
    monkeypatch.setattr(
        s4,
        "create_preprocess_configmap",
        lambda cmd, context, namespaces: captured.append(list(namespaces)),
    )
    context = ExecutionContext(plan_dir=tmp_path, workspace=tmp_path, logger=_Logger())
    s4.ModelNamespaceStep()._create_model_preprocess_configmap(_FakeCmd(), context)
    assert captured == []  # warned and returned, no write
