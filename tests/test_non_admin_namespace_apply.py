"""A namespace-level user cannot patch the Namespace object itself, and
plain kubernetes has no openshift scc cluster roles to bind, so a
non-admin standup applies the namespace resources without them. The scc
binding is retried separately and best-effort: the service account runs
restricted-v2 by default, and a hardened cluster can refuse the binding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from llmdbenchmark.standup.steps.step_04_model_namespace import ModelNamespaceStep


class _StubLogger:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def log_info(self, *_: Any, **__: Any) -> None:
        pass

    def log_warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def log_error(self, *_: Any, **__: Any) -> None:
        pass


@dataclass
class _StubResult:
    success: bool = True
    stderr: str = ""


@dataclass
class _StubCmd:
    fail_scc_apply: bool = False
    applied: list[str] = field(default_factory=list)

    def kube(self, *args: str, **_: Any) -> _StubResult:
        if args[0] == "apply":
            path = Path(args[args.index("-f") + 1])
            self.applied.append(path.read_text(encoding="utf-8"))
            if self.fail_scc_apply and path.name.endswith(".scc.yaml"):
                return _StubResult(success=False, stderr="forbidden")
        return _StubResult()


@dataclass
class _StubContext:
    rendered_stacks: list[Path]
    non_admin: bool
    workspace: Path
    is_openshift: bool = True
    namespace: str | None = None
    logger: _StubLogger = field(default_factory=_StubLogger)

    def setup_yamls_dir(self) -> Path:
        yamls = self.workspace / "setup" / "yamls"
        yamls.mkdir(parents=True, exist_ok=True)
        return yamls


_DOCS = [
    {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "nikita-dev"}},
    {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": {"name": "inference-perf-runner", "namespace": "nikita-dev"},
    },
    {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "RoleBinding",
        "metadata": {"name": "inference-perf-restricted-v2-scc"},
        "roleRef": {
            "kind": "ClusterRole",
            "name": "system:openshift:scc:restricted-v2",
        },
    },
]


def _stack(tmp_path: Path, content: str | None = None) -> Path:
    stack = tmp_path / "s1"
    stack.mkdir()
    (stack / "05_namespace_sa_rbac_secret.yaml").write_text(
        content if content is not None else yaml.safe_dump_all(_DOCS),
        encoding="utf-8",
    )
    return stack


def _apply(
    tmp_path: Path,
    non_admin: bool,
    content: str | None = None,
    is_openshift: bool = True,
    fail_scc_apply: bool = False,
):
    cmd = _StubCmd(fail_scc_apply=fail_scc_apply)
    context = _StubContext(
        rendered_stacks=[_stack(tmp_path, content)],
        non_admin=non_admin,
        workspace=tmp_path,
        is_openshift=is_openshift,
    )
    errors: list[str] = []

    ModelNamespaceStep()._apply_namespace_resources(cmd, context, errors)

    return cmd.applied, context.namespace, errors, context.logger


def _parsed(raw_texts: list[str]) -> list[list]:
    return [list(yaml.safe_load_all(raw)) for raw in raw_texts]


def _kinds(docs: list) -> set[str]:
    return {doc["kind"] for doc in docs if doc}


def test_non_admin_applies_without_the_namespace_object(tmp_path):
    raw, namespace, errors, _logger = _apply(tmp_path, non_admin=True)
    applied = _parsed(raw)

    assert errors == []
    assert len(applied) == 2
    assert _kinds(applied[0]) == {"ServiceAccount"}
    assert _kinds(applied[1]) == {"RoleBinding"}
    # the namespace is still resolved from the dropped document
    assert namespace == "nikita-dev"
    # the filtered manifests are kept in the workspace for review
    assert (tmp_path / "setup" / "yamls" / "05_namespace_sa_rbac.nsa.yaml").exists()
    assert (tmp_path / "setup" / "yamls" / "05_namespace_sa_rbac.scc.yaml").exists()


def test_admin_applies_the_original_file(tmp_path):
    raw, namespace, errors, _logger = _apply(tmp_path, non_admin=False)
    applied = _parsed(raw)

    assert errors == []
    assert len(applied) == 1
    assert _kinds(applied[0]) == {"Namespace", "ServiceAccount", "RoleBinding"}
    assert namespace == "nikita-dev"


def test_unparsable_manifest_applies_the_original_file(tmp_path):
    raw, namespace, errors, _logger = _apply(
        tmp_path, non_admin=True, content=": not yaml ["
    )

    # the original file is applied as-is; the failure is left to kubectl
    assert errors == []
    assert raw == [": not yaml ["]
    assert namespace is None


def test_plain_kubernetes_drops_the_scc_binding(tmp_path):
    raw, _namespace, errors, _logger = _apply(
        tmp_path, non_admin=True, is_openshift=False
    )
    applied = _parsed(raw)

    assert errors == []
    assert len(applied) == 1
    assert _kinds(applied[0]) == {"ServiceAccount"}


def test_refused_scc_binding_is_a_warning_not_an_error(tmp_path):
    raw, _namespace, errors, logger = _apply(
        tmp_path, non_admin=True, fail_scc_apply=True
    )

    assert errors == []
    assert len(raw) == 2
    assert any("scc binding" in w for w in logger.warnings)
