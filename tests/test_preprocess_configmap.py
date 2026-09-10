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
