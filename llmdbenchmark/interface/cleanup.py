"""CLI definition and executor for the ``cleanup`` subcommand."""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from llmdbenchmark.config import config
from llmdbenchmark.executor.command import CommandExecutor
from llmdbenchmark.interface.commands import Command
from llmdbenchmark.interface.env import env
from llmdbenchmark.run.steps.step_06_create_profile_configmap import (
    HARNESS_SCRIPTS_CONFIGMAP,
)

# Fallbacks for the scenario keys below, mirroring config/templates/values/
# defaults.yaml. defaults.yaml is always merged into the rendered plan, so
# these only matter for a stack whose config.yaml is missing the key.
DEFAULT_HARNESS_NAME = "inference-perf"
DEFAULT_HARNESS_POD_LABEL = "llmdbench-harness-launcher"
DEFAULT_HARNESS_SERVICE = "llm-d-benchmark-harness"
DEFAULT_WORKLOAD_PVC = "workload-pvc"

# No scenario key exists for the resources below -- they are literal in the
# templates and in the render code, so matching them by name is the only
# option today. Making them configurable is a render-side change.
#   role label: config/templates/jinja/06_pod_access_to_harness_data.yaml.j2
#   ConfigMaps: cli.py (run parameters), teardown/steps/step_02_clean_harness.py
DATA_ACCESS_POD_LABEL = "role=llm-d-benchmark-data-access"
CLEANUP_CONFIGMAPS = [
    "llm-d-benchmark-preprocesses",
    "llm-d-benchmark-run-parameters",
    "llm-d-benchmark-standup-parameters",
    HARNESS_SCRIPTS_CONFIGMAP,
]


@dataclass(frozen=True)
class CleanupTarget:
    """What one rendered stack leaves behind, and where.

    Resources are split across two namespaces: the harness pods and their
    ConfigMaps render into ``harness.namespace | default(namespace.name)``
    (20_harness_pod.yaml.j2), while the workload PVC, the harness service
    and the data-access pod render into ``namespace.name`` (01_*.j2,
    06_*.j2, 07_*.j2). A single namespace cannot reach both when the
    scenario sets ``harness.namespace``.
    """

    namespace: str
    harness_namespace: str
    pod_label: str
    service: str
    pvc_name: str
    profiles_configmap: str

    @property
    def pod_label_selector(self) -> str:
        """``harness.podLabel`` as kubectl sees it -- see delete_pods_by_label."""
        return f"app={self.pod_label}"

    @property
    def namespaces(self) -> list[str]:
        """Both namespaces this target touches, de-duplicated."""
        return _unique([self.harness_namespace, self.namespace])


def add_subcommands(
    parser: argparse._SubParsersAction, parents: list[argparse.ArgumentParser] = []
):
    """Register the ``cleanup`` subcommand and its arguments."""
    cleanup_parser = parser.add_parser(
        Command.CLEANUP.value,
        parents=parents,
        description=(
            "The `cleanup` command removes the resources a benchmark run "
            "leaves behind: the data-access pod, the harness service, the "
            "benchmark ConfigMaps, any leftover harness launcher pods, and "
            "the workload PVC. Resource names come from the scenario the "
            "same way every other subcommand reads them, so --spec is "
            "required. It is idempotent: resources that no longer exist are "
            "skipped, and a namespace with no benchmark resources exits 0. "
            "Pass --keep-pvc to preserve the workload PVC so workload data "
            "survives between runs (which is what `run` itself does)."
        ),
        help="Remove benchmark leftovers (pods, service, ConfigMaps, PVC) "
        "left by a scenario.",
    )
    cleanup_parser.add_argument(
        "-p",
        "--namespace",
        default=env("LLMDBENCH_NAMESPACE"),
        help="Comma-separated namespaces to clean up (model,harness). "
        "Overrides the namespaces from the plan config. If only one "
        "namespace is provided, it is used for both model and harness.",
    )
    cleanup_parser.add_argument(
        "--keep-pvc",
        action="store_true",
        default=False,
        help="Preserve the workload PVC so workload data survives for the "
        "next run. Everything else is still removed.",
    )
    cleanup_parser.add_argument(
        "--pvc-name",
        default=env("LLMDBENCH_WORKLOAD_PVC"),
        help="Name of the workload PVC to delete. Overrides "
        "storage.workloadPvc.name from the scenario "
        f"(default: {DEFAULT_WORKLOAD_PVC}; env: LLMDBENCH_WORKLOAD_PVC).",
    )
    cleanup_parser.add_argument(
        "--kubeconfig",
        "-k",
        default=env("LLMDBENCH_KUBECONFIG") or env("KUBECONFIG"),
        help="Path to kubeconfig file for kubectl commands.",
    )


def execute(args: argparse.Namespace, logger) -> None:
    """Delete benchmark leftovers for every rendered stack. Idempotent."""
    stack_configs = load_stack_configs(config.plan_dir)
    if not stack_configs:
        logger.log_error(
            "No rendered stack config found in the plan directory. "
            "Check that --spec points at a specification that renders at "
            "least one stack."
        )
        sys.exit(1)

    targets = resolve_targets(stack_configs, pvc_name=getattr(args, "pvc_name", None))
    if not targets:
        logger.log_error(
            "No namespace configured. Set 'namespace.name' in your scenario "
            "YAML, defaults.yaml, or pass --namespace on the CLI."
        )
        sys.exit(1)

    cmd = CommandExecutor(
        work_dir=config.workspace,
        dry_run=config.dry_run,
        verbose=config.verbose,
        logger=logger,
        kubeconfig=getattr(args, "kubeconfig", None),
    )

    deleted: list[str] = []
    errors: list[str] = []
    for target in targets:
        stack_deleted, stack_errors = cleanup_target(
            cmd, target, keep_pvc=args.keep_pvc, logger=logger
        )
        deleted.extend(stack_deleted)
        errors.extend(stack_errors)

    namespaces = ", ".join(
        f'"{ns}"' for ns in _unique(n for t in targets for n in t.namespaces)
    )

    if errors:
        for err in errors:
            logger.log_error(f"    {err}")
        logger.log_error(
            f"Cleanup finished with {len(errors)} error(s) in {namespaces}."
        )
        sys.exit(1)

    if deleted:
        logger.log_info(
            f"Cleanup complete: removed {len(deleted)} resource(s) from {namespaces}.",
            emoji="✅",
        )
    else:
        logger.log_info(
            f"No benchmark resources found in {namespaces} -- nothing to clean up.",
            emoji="✅",
        )


def load_stack_configs(plan_dir: Path | None) -> list[dict]:
    """Load ``config.yaml`` from every rendered stack in *plan_dir*."""
    if not plan_dir or not Path(plan_dir).exists():
        return []

    configs: list[dict] = []
    for stack_dir in sorted(Path(plan_dir).iterdir()):
        if not stack_dir.is_dir():
            continue
        config_file = stack_dir / "config.yaml"
        if not config_file.exists():
            continue
        with open(config_file, encoding="utf-8") as f:
            stack_config = yaml.safe_load(f)
        if stack_config:
            configs.append(stack_config)
    return configs


def resolve_targets(
    stack_configs: list[dict], *, pvc_name: str | None = None
) -> list[CleanupTarget]:
    """Build one :class:`CleanupTarget` per stack from its rendered config.

    Values follow the same three-tier precedence the run steps use
    (``executor/step.py``): CLI flag > scenario > default. ``--namespace``
    needs no tier here -- it is fed to RenderPlans as ``cli_namespace``, so
    the rendered config already carries the override, including the
    ``deploy,harness`` split form.
    """
    targets: list[CleanupTarget] = []
    for stack_config in stack_configs:
        namespace = _resolve(stack_config, "namespace.name")
        if not namespace:
            continue

        target = CleanupTarget(
            namespace=namespace,
            harness_namespace=_resolve(stack_config, "harness.namespace") or namespace,
            pod_label=_resolve(
                stack_config, "harness.podLabel", default=DEFAULT_HARNESS_POD_LABEL
            ),
            service=_resolve(
                stack_config, "labels.app", default=DEFAULT_HARNESS_SERVICE
            ),
            pvc_name=_resolve(
                stack_config,
                "storage.workloadPvc.name",
                cli_value=pvc_name,
                default=DEFAULT_WORKLOAD_PVC,
            ),
            profiles_configmap=(
                f"{_resolve(stack_config, 'harness.name', default=DEFAULT_HARNESS_NAME)}"
                "-profiles"
            ),
        )
        if target not in targets:
            targets.append(target)

    return targets


def cleanup_target(
    cmd: CommandExecutor,
    target: CleanupTarget,
    *,
    keep_pvc: bool,
    logger,
) -> tuple[list[str], list[str]]:
    """Delete everything *target* describes; return (deleted, errors)."""
    logger.log_info(
        "Cleaning up benchmark resources in "
        + ", ".join(f'"{ns}"' for ns in target.namespaces)
        + "..."
    )

    deleted: list[str] = []
    errors: list[str] = []

    # Harness namespace first. Pods go before the PVC: the claim's
    # protection finalizer keeps it Terminating while a pod mounts it.
    harness_ns = target.harness_namespace
    _delete_by_label(
        cmd, "pod", target.pod_label_selector, harness_ns, logger, deleted, errors
    )
    for cm_name in [target.profiles_configmap, *CLEANUP_CONFIGMAPS]:
        _delete_by_name(cmd, "configmap", cm_name, harness_ns, logger, deleted, errors)

    # Workload namespace: data-access pod, harness service, workload PVC.
    namespace = target.namespace
    _delete_by_label(
        cmd, "pod", DATA_ACCESS_POD_LABEL, namespace, logger, deleted, errors
    )
    _delete_by_name(cmd, "service", target.service, namespace, logger, deleted, errors)

    if keep_pvc:
        logger.log_info(
            f'Keeping PVC "{target.pvc_name}" (--keep-pvc). Note: it keeps '
            "billing against its storage backend until deleted."
        )
    else:
        _delete_by_name(cmd, "pvc", target.pvc_name, namespace, logger, deleted, errors)

    return deleted, errors


def _delete_by_name(
    cmd: CommandExecutor,
    kind: str,
    name: str,
    namespace: str,
    logger,
    deleted: list,
    errors: list,
) -> None:
    """Delete a single named resource if it exists."""
    check = cmd.kube(
        "get",
        kind,
        name,
        "--namespace",
        namespace,
        "-o",
        "name",
        "--ignore-not-found",
        check=False,
    )
    if not check.dry_run and (not check.success or not check.stdout.strip()):
        return

    logger.log_info(f"  Deleting {kind}/{name}", emoji="🗑️")
    result = cmd.kube(
        "delete",
        kind,
        name,
        "--namespace",
        namespace,
        "--ignore-not-found",
        check=False,
    )
    if result.success:
        deleted.append(f"{kind}/{name}")
    else:
        errors.append(f"Failed to delete {kind}/{name}: {result.stderr}")


def _delete_by_label(
    cmd: CommandExecutor,
    kind: str,
    label: str,
    namespace: str,
    logger,
    deleted: list,
    errors: list,
) -> None:
    """Delete every resource of *kind* matching *label*, if any."""
    list_result = cmd.kube(
        "get",
        kind,
        "-l",
        label,
        "--namespace",
        namespace,
        "-o",
        "name",
        "--ignore-not-found",
        check=False,
    )
    names = []
    if list_result.success and list_result.stdout.strip():
        names = list_result.stdout.strip().splitlines()
    if not list_result.dry_run and not names:
        return

    for name in names:
        logger.log_info(f"  Deleting {name}", emoji="🗑️")
    result = cmd.kube(
        "delete",
        kind,
        "-l",
        label,
        "--namespace",
        namespace,
        "--ignore-not-found",
        check=False,
    )
    if result.success:
        deleted.extend(names)
    else:
        errors.append(f"Failed to delete {kind} with label {label}: {result.stderr}")


def _resolve(
    stack_config: dict | None,
    path: str,
    *,
    cli_value: Any = None,
    default: Any = None,
) -> Any:
    """Three-tier lookup: CLI value, then dotted *path*, then *default*."""
    if cli_value is not None:
        return cli_value

    node: Any = stack_config
    for key in path.split("."):
        if isinstance(node, dict):
            node = node.get(key)
        else:
            node = None
            break
    if node is not None and node != {} and node != []:
        return node

    return default


def _unique(values) -> list[str]:
    """De-duplicate *values*, preserving first-seen order."""
    seen: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return seen
