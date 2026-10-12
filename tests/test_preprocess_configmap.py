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


def test_configmap_skips_python_bytecode(tmp_path, monkeypatch) -> None:
    """__pycache__/*.pyc must not be bundled: they bloat the ConfigMap past
    the 256 KiB last-applied-configuration annotation limit, so
    `kubectl apply` rejects it and the ConfigMap is never created."""
    from llmdbenchmark.utilities.preprocess_configmap import (
        create_preprocess_configmap,
    )

    src = tmp_path / "preprocess"
    (src / "__pycache__").mkdir(parents=True)
    (src / "run.sh").write_text("#!/bin/sh\n")
    (src / "env.py").write_text("print()\n")
    (src / "__pycache__" / "env.cpython-314.pyc").write_bytes(b"\x00")
    (src / "stale.pyc").write_bytes(b"\x00")

    context = ExecutionContext(plan_dir=tmp_path, workspace=tmp_path, logger=_Logger())
    monkeypatch.setattr(ExecutionContext, "preprocess_dir", lambda self: src)
    cmd = _FakeCmd()
    create_preprocess_configmap(cmd, context, ["ns-a"])

    (create_call,) = [c for c in cmd.calls if c[0] == "create"]
    keys = sorted(
        a.removeprefix("--from-file=").split("=", 1)[0]
        for a in create_call
        if str(a).startswith("--from-file=")
    )
    assert keys == ["env.py", "run.sh"]
