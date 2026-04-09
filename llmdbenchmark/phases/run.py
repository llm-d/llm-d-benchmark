"""Run phase -- execute benchmark harness against deployed model endpoints.

Public API:
    ``do_run(args, logger, render_plan_errors, experiment_file_override=None)`` --
        re-entrant core used by the experiment orchestrator.
    ``execute_run(args, logger, render_plan_errors)`` -- top-level CLI entry
        point.  Prints the BENCHMARK RUN SUMMARY banner (via
        ``phases.banners``) and stores run parameters as a ConfigMap in the
        namespace.
    ``store_run_parameters_configmap(...)`` -- append a timestamped run entry
        to the ``llm-d-benchmark-run-parameters`` ConfigMap for auditability.
"""

import getpass
import json
import socket
import sys
from datetime import datetime, timezone

import yaml

from llmdbenchmark.config import config
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.executor.step import Phase
from llmdbenchmark.executor.step_executor import StepExecutor
from llmdbenchmark.run.steps import get_run_steps

from llmdbenchmark.phases.banners import print_run_banner
from llmdbenchmark.phases.common import (
    PhaseError,
    load_all_stacks_info,
    parse_namespaces,
    resolve_deploy_methods,
)


def do_run(args, logger, render_plan_errors, experiment_file_override=None):
    """Core run logic. Returns (context, result). Raises PhaseError on failure."""
    rendered_paths = getattr(render_plan_errors, "rendered_paths", [])
    all_stacks_info = load_all_stacks_info(rendered_paths)
    plan_info = all_stacks_info[0] if all_stacks_info else {}

    deployed_methods = resolve_deploy_methods(args, plan_info, logger, phase="run")

    namespace, harness_ns = parse_namespaces(
        getattr(args, "namespace", None), plan_info,
    )

    endpoint_url = getattr(args, "endpoint_url", None)
    run_config_file = getattr(args, "run_config", None)
    is_run_only = bool(endpoint_url or run_config_file)

    if not namespace and not is_run_only:
        raise PhaseError(
            "No namespace specified. Set 'namespace.name' in your scenario "
            "YAML, defaults.yaml, or pass --namespace on the CLI."
        )

    experiments_file = experiment_file_override or getattr(args, "experiments", None)

    context = ExecutionContext(
        plan_dir=config.plan_dir,
        workspace=config.workspace,
        specification_file=getattr(args, "specification_file", None),
        rendered_stacks=rendered_paths,
        dry_run=config.dry_run,
        verbose=config.verbose,
        non_admin=getattr(args, "non_admin", False),
        current_phase=Phase.RUN,
        kubeconfig=getattr(args, "kubeconfig", None),
        deployed_methods=deployed_methods,
        namespace=namespace,
        harness_namespace=harness_ns,
        model_name=getattr(args, "model", None) or plan_info.get("model_name"),
        logger=logger,
        harness_name=getattr(args, "harness", None),
        harness_profile=getattr(args, "workload", None),
        experiment_treatments_file=experiments_file,
        profile_overrides=getattr(args, "overrides", None),
        harness_output=getattr(args, "output", "local") or "local",
        harness_parallelism=int(getattr(args, "parallelism", 1) or 1),
        harness_wait_timeout=int(getattr(args, "wait_timeout", 3600) or 3600),
        harness_debug=getattr(args, "debug", False),
        harness_skip_run=getattr(args, "skip", False),
        harness_service_account=getattr(args, "serviceaccount", None),
        harness_envvars_to_pod=getattr(args, "envvarspod", None),
        analyze_locally=getattr(args, "analyze", False),
        endpoint_url=endpoint_url,
        run_config_file=run_config_file,
        generate_config_only=getattr(args, "generate_config", False),
        dataset_url=getattr(args, "dataset", None),
    )

    executor = StepExecutor(
        steps=get_run_steps(),
        context=context,
        logger=logger,
        max_parallel_stacks=1,
    )

    step_spec = getattr(args, "step", None)
    result = executor.execute(step_spec=step_spec)

    if result.has_errors:
        raise PhaseError(f"Run failed:\n{result.summary()}")

    return context, result


def execute_run(args, logger, render_plan_errors):
    """Build execution context and run experiment steps."""
    try:
        context, result = do_run(args, logger, render_plan_errors)
    except PhaseError as e:
        logger.log_error(str(e))
        sys.exit(1)

    print_run_banner(context, logger)

    # --- Store run parameters as ConfigMap in namespace ---
    if not context.dry_run:
        harness = context.harness_name or "inference-perf"
        workload = context.harness_profile or "unknown"
        experiment_ids = getattr(context, "experiment_ids", []) or []
        store_run_parameters_configmap(
            context, harness, workload, experiment_ids, logger,
        )


def store_run_parameters_configmap(context, harness, workload, experiment_ids, logger):
    """Store run parameters as a ConfigMap in the namespace for auditability.

    Each run gets its own key in the ConfigMap data (keyed by timestamp),
    so multiple sequential or parallel runs accumulate history in a single
    ConfigMap rather than overwriting each other.
    """
    try:
        cmd = context.require_cmd()
        namespace = context.harness_namespace or context.namespace
        if not namespace:
            return

        cm_name = "llm-d-benchmark-run-parameters"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        # Build PVC results paths from experiment IDs
        parallelism = context.harness_parallelism or 1
        results_dir_prefix = "/requests"
        pvc_paths = []
        for eid in (experiment_ids or []):
            for i in range(1, parallelism + 1):
                pvc_paths.append(f"{results_dir_prefix}/{eid}_{i}")

        run_entry = {
            "harness": harness,
            "workload": workload,
            "model": context.model_name or "",
            "namespace": namespace,
            "endpoint_url": context.endpoint_url or "",
            "user": getpass.getuser(),
            "hostname": socket.gethostname(),
            "experiment_ids": experiment_ids or [],
            "pvc_name": "workload-pvc",
            "pvc_results_paths": pvc_paths,
            "pvc_results_prefix": results_dir_prefix,
            "timestamp": timestamp,
            "analyze": context.analyze_locally,
            "parallelism": parallelism,
            "output": context.harness_output or "local",
        }

        # Try to read existing ConfigMap to append
        existing_data = {}
        get_result = cmd.kube(
            "get", "configmap", cm_name,
            "-o", "jsonpath={.data}",
            namespace=namespace,
            check=False,
        )
        if get_result.success and get_result.stdout.strip():
            try:
                existing_data = json.loads(get_result.stdout)
            except (json.JSONDecodeError, ValueError):
                pass

        # Add this run keyed by timestamp (also update "latest")
        run_key = f"run-{timestamp}"
        existing_data[run_key] = yaml.dump(run_entry, default_flow_style=False)
        existing_data["latest"] = yaml.dump(run_entry, default_flow_style=False)

        # Build configmap YAML
        cm = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": cm_name,
                "namespace": namespace,
            },
            "data": existing_data,
        }

        cm_path = context.run_dir() / "run-parameters-configmap.yaml"
        cm_path.parent.mkdir(parents=True, exist_ok=True)
        cm_path.write_text(yaml.dump(cm, default_flow_style=False), encoding="utf-8")

        cmd.kube(
            "apply", "-f", str(cm_path),
            namespace=namespace,
            check=False,
        )
        logger.log_info(
            f"Run parameters stored in configmap/{cm_name} (key={run_key}) in ns/{namespace}",
        )
    except Exception as exc:
        logger.log_warning(f"Could not store run parameters ConfigMap: {exc}")
