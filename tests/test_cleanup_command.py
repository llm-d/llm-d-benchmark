"""Tests for the ``cleanup`` subcommand's step.

``cleanup`` removes the leftovers a benchmark run parks in the namespace
(harness pods, ConfigMaps, data-access pod, harness service, workload PVC).
Two properties matter: every resource name comes from the rendered
``config.yaml`` rather than from hardcoded fallbacks, and the step is
idempotent -- a namespace with nothing in it is a success, not an error.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from llmdbenchmark.cleanup.steps import get_cleanup_steps
from llmdbenchmark.cleanup.steps.step_00_cleanup_resources import (
    BENCHMARK_CONFIGMAPS,
    CleanupResourcesStep,
)
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.utilities.kube_helpers import DATA_ACCESS_LABEL


@dataclass
class _Result:
    success: bool
    stdout: str = ""
    stderr: str = ""


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
    Mirrors kubectl closely enough for the step: ``get -o jsonpath`` returns
    the first match, ``delete`` prints one "<kind>/<name> deleted" line per
    removed resource and nothing for ``--ignore-not-found`` misses.
    """

    def __init__(self, resources: dict[str, dict[str, set[str]]]):
        self.resources = {ns: dict(items) for ns, items in resources.items()}
        self.deleted: list[tuple[str, str]] = []

    def kube(self, *args, check=True, **_kwargs):
        namespace = args[args.index("--namespace") + 1]
        verb, kind = args[0], args[1]
        inventory = self.resources.setdefault(namespace, {})
        matches = self._match(inventory, kind, args)
        if verb == "get":
            return _Result(True, matches[0].split("/", 1)[1] if matches else "")
        if verb == "delete":
            for key in matches:
                del inventory[key]
                self.deleted.append((namespace, key))
            return _Result(True, "\n".join(f"{key} deleted" for key in matches))
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
        key = f"{kind}/{name}"
        return [key] if key in inventory else []


def _stack_config(**overrides) -> dict:
    """A rendered config.yaml carrying everything the step reads."""
    config = {
        "namespace": {"name": "bench"},
        "labels": {"app": "llm-d-benchmark-harness"},
        "storage": {"workloadPvc": {"name": "workload-pvc"}},
        "harness": {"name": "inference-perf", "podLabel": "llmdbench-harness-launcher"},
    }
    for path, value in overrides.items():
        node = config
        keys = path.split("__")
        for key in keys[:-1]:
            node = node.setdefault(key, {})
        node[keys[-1]] = value
    return config


def _inventory(config: dict) -> dict[str, dict[str, set[str]]]:
    """Everything a run of *config* leaves behind, in both namespaces."""
    namespace = config["namespace"]["name"]
    harness_ns = config["harness"].get("namespace") or namespace
    resources: dict[str, dict[str, set[str]]] = {namespace: {}, harness_ns: {}}

    harness = resources[harness_ns]
    harness["pod/harness-launcher-1"] = {f"app={config['harness']['podLabel']}"}
    harness[f"configmap/{config['harness']['name']}-profiles"] = set()
    for cm_name in BENCHMARK_CONFIGMAPS:
        harness[f"configmap/{cm_name}"] = set()

    # Same dict as ``harness`` when the scenario leaves harness.namespace unset.
    workload = resources[namespace]
    workload["pod/access-to-harness-data-workload-pvc"] = {DATA_ACCESS_LABEL}
    workload[f"service/{config['labels']['app']}"] = set()
    workload[f"pvc/{config['storage']['workloadPvc']['name']}"] = set()
    return resources


def _render(tmp_path: Path, *configs: dict) -> list[Path]:
    """Write one rendered stack directory per config, like RenderPlans does."""
    paths = []
    for index, config in enumerate(configs):
        stack_dir = tmp_path / f"stack-{index}"
        stack_dir.mkdir()
        (stack_dir / "config.yaml").write_text(yaml.safe_dump(config))
        paths.append(stack_dir)
    return paths


def _context(tmp_path: Path, cluster: _FakeCluster, *configs: dict, **kwargs):
    return ExecutionContext(
        plan_dir=tmp_path,
        workspace=tmp_path,
        rendered_stacks=_render(tmp_path, *configs),
        logger=_FakeLogger(),
        cmd=cluster,
        **kwargs,
    )


def _clean(tmp_path: Path, *configs: dict, **kwargs):
    """Run the step against a cluster holding every leftover of *configs*."""
    resources: dict[str, dict[str, set[str]]] = {}
    for config in configs:
        for ns, items in _inventory(config).items():
            resources.setdefault(ns, {}).update(items)
    cluster = _FakeCluster(resources)
    result = CleanupResourcesStep().execute(
        _context(tmp_path, cluster, *configs, **kwargs)
    )
    return cluster, result


def test_registered_as_the_only_cleanup_step() -> None:
    assert [type(s) for s in get_cleanup_steps()] == [CleanupResourcesStep]


def test_deletes_every_benchmark_resource(tmp_path) -> None:
    cluster, result = _clean(tmp_path, _stack_config())

    assert result.success, result.errors
    # Acceptance: nothing benchmark-owned survives in either namespace.
    assert not any(cluster.resources.values())
    assert "Removed" in result.message


def test_keep_pvc_preserves_only_the_pvc(tmp_path) -> None:
    cluster, result = _clean(tmp_path, _stack_config(), keep_pvc=True)

    assert result.success, result.errors
    assert list(cluster.resources["bench"]) == ["pvc/workload-pvc"]


def test_empty_namespace_is_a_no_op_success(tmp_path) -> None:
    cluster = _FakeCluster({})

    result = CleanupResourcesStep().execute(
        _context(tmp_path, cluster, _stack_config())
    )

    assert result.success, result.errors
    assert not cluster.deleted
    assert "No benchmark resources found" in result.message


def test_data_access_pod_deleted_before_pvc(tmp_path) -> None:
    """The PVC protection finalizer blocks claim deletion while a pod mounts
    it, so the data-access pod must be deleted before the claim."""
    cluster, _ = _clean(tmp_path, _stack_config())

    order = [key for _ns, key in cluster.deleted]
    assert order.index("pod/access-to-harness-data-workload-pvc") < order.index(
        "pvc/workload-pvc"
    )


def test_resource_names_come_from_the_rendered_config(tmp_path) -> None:
    """A scenario that renames every configurable resource is still cleaned."""
    config = _stack_config(
        harness__podLabel="my-launcher",
        harness__name="guidellm",
        labels__app="my-harness",
        storage__workloadPvc__name="my-workload",
    )

    cluster, result = _clean(tmp_path, config)

    assert result.success, result.errors
    assert not any(cluster.resources.values())
    deleted = {key for _ns, key in cluster.deleted}
    assert {
        "pod/harness-launcher-1",
        "configmap/guidellm-profiles",
        "service/my-harness",
        "pvc/my-workload",
    } <= deleted


@pytest.mark.parametrize(
    "missing",
    ["harness__podLabel", "harness__name", "labels__app", "storage__workloadPvc__name"],
)
def test_missing_config_key_is_an_error_not_a_guess(tmp_path, missing) -> None:
    """No hardcoded fallbacks: a rendered config without a key the step needs
    fails loudly instead of deleting a guessed name."""
    config = _stack_config()
    node = config
    keys = missing.split("__")
    for key in keys[:-1]:
        node = node[key]
    del node[keys[-1]]

    with pytest.raises(KeyError, match=missing.replace("__", ".")):
        CleanupResourcesStep().execute(_context(tmp_path, _FakeCluster({}), config))


def test_harness_namespace_is_cleaned_alongside_the_workload_namespace(
    tmp_path,
) -> None:
    """Launcher pods and their ConfigMaps render into ``harness.namespace``
    while the PVC, service and data-access pod render into ``namespace.name``,
    so one namespace cannot reach both."""
    cluster, result = _clean(
        tmp_path, _stack_config(harness__namespace="bench-harness")
    )

    assert result.success, result.errors
    assert not any(cluster.resources.values())
    assert ("bench-harness", "pod/harness-launcher-1") in cluster.deleted
    assert ("bench", "pvc/workload-pvc") in cluster.deleted


def test_every_stack_of_a_multi_stack_scenario_is_cleaned(tmp_path) -> None:
    cluster, result = _clean(
        tmp_path,
        _stack_config(namespace__name="stack-a"),
        _stack_config(namespace__name="stack-b", harness__name="guidellm"),
    )

    assert result.success, result.errors
    assert not any(cluster.resources.values())
    assert ("stack-a", "configmap/inference-perf-profiles") in cluster.deleted
    assert ("stack-b", "configmap/guidellm-profiles") in cluster.deleted


def test_identical_stacks_are_cleaned_once(tmp_path) -> None:
    """Multi-stack scenarios routinely share a namespace; cleaning it twice
    would double every log line for no benefit."""
    cluster, result = _clean(tmp_path, _stack_config(), _stack_config())

    assert result.success, result.errors
    assert len(cluster.deleted) == len(set(cluster.deleted))
    assert sum(1 for _ns, key in cluster.deleted if key == "pvc/workload-pvc") == 1


def test_no_rendered_config_is_an_error(tmp_path) -> None:
    context = ExecutionContext(
        plan_dir=tmp_path,
        workspace=tmp_path,
        rendered_stacks=[tmp_path / "stack-without-config"],
        logger=_FakeLogger(),
        cmd=_FakeCluster({}),
    )

    result = CleanupResourcesStep().execute(context)

    assert not result.success
    assert result.errors


def test_failed_delete_is_reported(tmp_path) -> None:
    class _FailingCluster(_FakeCluster):
        def kube(self, *args, **kwargs):
            if args[0] == "delete" and args[1] == "pvc":
                return _Result(False, "", "forbidden")
            return super().kube(*args, **kwargs)

    cluster = _FailingCluster(_inventory(_stack_config()))
    result = CleanupResourcesStep().execute(
        _context(tmp_path, cluster, _stack_config())
    )

    assert not result.success
    assert any(
        "pvc/workload-pvc" in err and "forbidden" in err for err in result.errors
    )


def test_nok8s_scenario_is_skipped(tmp_path) -> None:
    context = _context(tmp_path, _FakeCluster({}), deployed_methods=["nok8s"])
    assert CleanupResourcesStep().should_skip(context)
