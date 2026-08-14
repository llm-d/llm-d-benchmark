"""Tests for the ``cleanup`` subcommand.

``cleanup`` is the CLI's answer to the leftovers a benchmark run parks in the
namespace (data-access pod, harness service, ConfigMaps, workload PVC). Two
properties matter: the resource names come from the scenario rather than from
hardcoded constants, and the command is idempotent -- a namespace with nothing
in it is a success, not an error.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from llmdbenchmark.interface.cleanup import (
    CLEANUP_CONFIGMAPS,
    DATA_ACCESS_POD_LABEL,
    DEFAULT_HARNESS_POD_LABEL,
    DEFAULT_HARNESS_SERVICE,
    DEFAULT_WORKLOAD_PVC,
    cleanup_target,
    load_stack_configs,
    resolve_targets,
)

import pytest
import yaml


@dataclass
class _Result:
    success: bool
    stdout: str = ""
    stderr: str = ""
    dry_run: bool = False


class _FakeLogger:
    def __init__(self):
        self.messages = []

    def log_info(self, msg, **_kwargs):
        self.messages.append(("info", msg))

    def log_warning(self, msg, **_kwargs):
        self.messages.append(("warning", msg))

    def log_error(self, msg, **_kwargs):
        self.messages.append(("error", msg))


class _FakeCluster:
    """Answers kubectl get/delete against an in-memory resource inventory.

    ``resources`` maps namespace -> {"kind/name": set of "key=value" labels}.
    """

    def __init__(self, resources: dict[str, dict[str, set[str]]]):
        self.resources = {ns: dict(items) for ns, items in resources.items()}
        self.deleted: list[tuple[str, str]] = []
        self.commands: list[tuple[str, ...]] = []

    def kube(self, *args, namespace=None, check=True, force=False):
        self.commands.append(args)
        namespace = args[args.index("--namespace") + 1]
        verb, kind = args[0], args[1]
        inventory = self.resources.setdefault(namespace, {})
        if verb == "get":
            return _Result(True, "\n".join(self._match(inventory, kind, args)))
        if verb == "delete":
            for key in self._match(inventory, kind, args):
                del inventory[key]
                self.deleted.append((namespace, key))
            return _Result(True)
        return _Result(False, "", f"unexpected verb {verb}")

    @staticmethod
    def _match(inventory: dict[str, set[str]], kind: str, args) -> list[str]:
        if "-l" in args:
            label = args[args.index("-l") + 1]
            return [
                key
                for key, labels in inventory.items()
                if key.startswith(f"{kind}/") and label in labels
            ]
        name = args[2] if len(args) > 2 and not args[2].startswith("-") else None
        if name:
            key = f"{kind}/{name}"
            return [key] if key in inventory else []
        return [k for k in inventory if k.startswith(f"{kind}/")]


def _stack_config(**overrides) -> dict:
    """A rendered config.yaml with everything cleanup reads at its default."""
    config = {
        "namespace": {"name": "bench"},
        "labels": {"app": DEFAULT_HARNESS_SERVICE},
        "storage": {"workloadPvc": {"name": DEFAULT_WORKLOAD_PVC}},
        "harness": {"name": "inference-perf", "podLabel": DEFAULT_HARNESS_POD_LABEL},
    }
    for path, value in overrides.items():
        node = config
        keys = path.split("__")
        for key in keys[:-1]:
            node = node.setdefault(key, {})
        node[keys[-1]] = value
    return config


def _inventory(target) -> dict[str, dict[str, set[str]]]:
    """Everything a run of *target* leaves behind, in both namespaces."""
    resources: dict[str, dict[str, set[str]]] = {ns: {} for ns in target.namespaces}
    harness = resources[target.harness_namespace]
    harness["pod/harness-launcher-1"] = {target.pod_label_selector}
    harness[f"configmap/{target.profiles_configmap}"] = set()
    for cm_name in CLEANUP_CONFIGMAPS:
        harness[f"configmap/{cm_name}"] = set()

    # Same dict as ``harness`` when the scenario leaves harness.namespace unset.
    workload = resources[target.namespace]
    workload["pod/access-to-harness-data-workload-pvc"] = {DATA_ACCESS_POD_LABEL}
    workload[f"service/{target.service}"] = set()
    workload[f"pvc/{target.pvc_name}"] = set()
    return resources


def _clean(target, *, keep_pvc: bool = False):
    cluster = _FakeCluster(_inventory(target))
    deleted, errors = cleanup_target(
        cluster, target, keep_pvc=keep_pvc, logger=_FakeLogger()
    )
    return cluster, deleted, errors


def _only_target(stack_config: dict, **kwargs):
    targets = resolve_targets([stack_config], **kwargs)
    assert len(targets) == 1
    return targets[0]


def test_deletes_every_benchmark_resource() -> None:
    target = _only_target(_stack_config())
    cluster, deleted, errors = _clean(target)

    assert not errors
    # Acceptance: nothing benchmark-owned survives in either namespace.
    assert not any(cluster.resources.values())
    assert f"pvc/{DEFAULT_WORKLOAD_PVC}" in deleted
    assert f"service/{DEFAULT_HARNESS_SERVICE}" in deleted
    assert "configmap/inference-perf-profiles" in deleted


def test_keep_pvc_preserves_only_the_pvc() -> None:
    target = _only_target(_stack_config())
    cluster, deleted, errors = _clean(target, keep_pvc=True)

    assert not errors
    assert list(cluster.resources[target.namespace]) == [f"pvc/{DEFAULT_WORKLOAD_PVC}"]
    assert f"pvc/{DEFAULT_WORKLOAD_PVC}" not in deleted


def test_empty_namespace_is_a_no_op_success() -> None:
    target = _only_target(_stack_config())
    cluster = _FakeCluster({})

    deleted, errors = cleanup_target(
        cluster, target, keep_pvc=False, logger=_FakeLogger()
    )

    assert not errors
    assert not deleted
    assert not cluster.deleted


def test_pods_deleted_before_pvc() -> None:
    """The PVC protection finalizer blocks claim deletion while a pod mounts
    it, so the data-access pod must be deleted before the claim."""
    target = _only_target(_stack_config())
    cluster, _, _ = _clean(target)

    order = [key for _ns, key in cluster.deleted]
    assert order.index("pod/access-to-harness-data-workload-pvc") < order.index(
        f"pvc/{DEFAULT_WORKLOAD_PVC}"
    )


# --- resolution: scenario values, and the CLI > scenario > default order ---


def test_scenario_overrides_are_honored() -> None:
    """A scenario that renames every configurable resource is still cleaned."""
    target = _only_target(
        _stack_config(
            harness__podLabel="my-launcher",
            harness__name="guidellm",
            labels__app="my-harness",
            storage__workloadPvc__name="my-workload",
        )
    )

    assert target.pod_label_selector == "app=my-launcher"
    assert target.service == "my-harness"
    assert target.pvc_name == "my-workload"
    assert target.profiles_configmap == "guidellm-profiles"

    cluster, deleted, errors = _clean(target)
    assert not errors
    assert not any(cluster.resources.values())
    assert "configmap/guidellm-profiles" in deleted
    assert "pvc/my-workload" in deleted


def test_missing_keys_fall_back_to_defaults() -> None:
    target = _only_target({"namespace": {"name": "bench"}})

    assert target.pod_label_selector == f"app={DEFAULT_HARNESS_POD_LABEL}"
    assert target.service == DEFAULT_HARNESS_SERVICE
    assert target.pvc_name == DEFAULT_WORKLOAD_PVC
    assert target.profiles_configmap == "inference-perf-profiles"


def test_cli_pvc_name_beats_the_scenario() -> None:
    target = _only_target(
        _stack_config(storage__workloadPvc__name="scenario-pvc"),
        pvc_name="cli-pvc",
    )

    assert target.pvc_name == "cli-pvc"


def test_harness_namespace_is_cleaned_alongside_the_workload_namespace() -> None:
    """Launcher pods and their ConfigMaps render into ``harness.namespace``
    while the PVC, service and data-access pod render into ``namespace.name``,
    so one namespace cannot reach both."""
    target = _only_target(_stack_config(harness__namespace="bench-harness"))

    assert target.namespaces == ["bench-harness", "bench"]

    cluster, _, errors = _clean(target)
    assert not errors
    assert not any(cluster.resources.values())

    touched = {ns for ns, _key in cluster.deleted}
    assert touched == {"bench", "bench-harness"}
    assert ("bench-harness", "pod/harness-launcher-1") in cluster.deleted
    assert ("bench", f"pvc/{DEFAULT_WORKLOAD_PVC}") in cluster.deleted


def test_every_stack_of_a_multi_stack_scenario_is_cleaned() -> None:
    targets = resolve_targets(
        [
            _stack_config(namespace__name="stack-a"),
            _stack_config(namespace__name="stack-b", harness__name="guidellm"),
        ]
    )

    assert [t.namespace for t in targets] == ["stack-a", "stack-b"]
    assert [t.profiles_configmap for t in targets] == [
        "inference-perf-profiles",
        "guidellm-profiles",
    ]


def test_identical_stacks_are_cleaned_once() -> None:
    """Multi-stack scenarios routinely share a namespace; cleaning it twice
    would double every log line for no benefit."""
    assert len(resolve_targets([_stack_config(), _stack_config()])) == 1


def test_stacks_without_a_namespace_are_skipped() -> None:
    assert resolve_targets([{"harness": {"name": "inference-perf"}}]) == []


def test_load_stack_configs_reads_every_rendered_stack(tmp_path) -> None:
    for name in ("stack-b", "stack-a"):
        stack_dir = tmp_path / name
        stack_dir.mkdir()
        (stack_dir / "config.yaml").write_text(
            yaml.safe_dump(_stack_config(namespace__name=name))
        )
    # A directory without a rendered config, and a stray file, are ignored.
    (tmp_path / "logs").mkdir()
    (tmp_path / "notes.txt").write_text("ignored")

    configs = load_stack_configs(tmp_path)

    assert [c["namespace"]["name"] for c in configs] == ["stack-a", "stack-b"]


def test_load_stack_configs_tolerates_a_missing_plan_dir(tmp_path) -> None:
    assert load_stack_configs(None) == []
    assert load_stack_configs(tmp_path / "nope") == []


def test_execute_errors_when_no_namespace_resolves(monkeypatch, tmp_path) -> None:
    """A rendered plan with no namespace is a configuration error, not a
    silent no-op that reports success."""
    from llmdbenchmark.interface import cleanup as cleanup_module

    stack_dir = tmp_path / "stack-a"
    stack_dir.mkdir()
    (stack_dir / "config.yaml").write_text(yaml.safe_dump({"harness": {}}))
    monkeypatch.setattr(cleanup_module.config, "plan_dir", tmp_path)

    logger = _FakeLogger()
    with pytest.raises(SystemExit) as excinfo:
        cleanup_module.execute(argparse.Namespace(keep_pvc=False), logger)

    assert excinfo.value.code == 1
    assert any(level == "error" for level, _msg in logger.messages)
