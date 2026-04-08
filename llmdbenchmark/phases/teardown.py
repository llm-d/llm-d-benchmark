"""Teardown phase -- remove deployed resources and clean up the cluster.

Public API:
    ``do_teardown(args, logger, render_plan_errors)`` -- re-entrant core used
        by the experiment orchestrator (for per-treatment cleanup).
    ``execute_teardown(args, logger, render_plan_errors)`` -- top-level CLI
        entry point.
"""

import sys

from llmdbenchmark.config import config
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.executor.step import Phase
from llmdbenchmark.executor.step_executor import StepExecutor
from llmdbenchmark.teardown.steps import get_teardown_steps

from llmdbenchmark.phases.common import (
    PhaseError,
    load_plan_info,
    parse_namespaces,
    resolve_deploy_methods,
)


def do_teardown(args, logger, render_plan_errors):
    """Core teardown logic. Returns (context, result). Raises PhaseError on failure."""
    rendered_paths = getattr(render_plan_errors, "rendered_paths", [])
    plan_info = load_plan_info(rendered_paths)
    deployed_methods = resolve_deploy_methods(
        args, plan_info, logger, phase="teardown"
    )

    namespace, harness_ns = parse_namespaces(
        getattr(args, "namespace", None), plan_info,
    )

    if not namespace:
        raise PhaseError(
            "No namespace specified. Set 'namespace.name' in your scenario "
            "YAML, defaults.yaml, or pass --namespace on the CLI."
        )

    context = ExecutionContext(
        plan_dir=config.plan_dir,
        workspace=config.workspace,
        specification_file=getattr(args, "specification_file", None),
        rendered_stacks=rendered_paths,
        dry_run=config.dry_run,
        verbose=config.verbose,
        non_admin=getattr(args, "non_admin", False),
        current_phase=Phase.TEARDOWN,
        kubeconfig=getattr(args, "kubeconfig", None),
        deployed_methods=deployed_methods,
        deep_clean=getattr(args, "deep", False),
        release=getattr(args, "release", "llmdbench"),
        namespace=namespace,
        harness_namespace=harness_ns,
        model_name=plan_info.get("model_name"),
        logger=logger,
    )

    executor = StepExecutor(
        steps=get_teardown_steps(),
        context=context,
        logger=logger,
    )

    step_spec = getattr(args, "step", None)
    result = executor.execute(step_spec=step_spec)

    if result.has_errors:
        raise PhaseError(f"Teardown failed:\n{result.summary()}")

    return context, result


def execute_teardown(args, logger, render_plan_errors):
    """Build execution context and run teardown steps."""
    try:
        context, result = do_teardown(args, logger, render_plan_errors)
    except PhaseError as e:
        logger.log_error(str(e))
        sys.exit(1)

    ns = context.namespace or "unknown"
    harness_ns = context.harness_namespace or ns
    mode = "deep clean" if context.deep_clean else "normal"
    logger.line_break()
    logger.log_info(
        f"Teardown complete ({mode}). "
        f'Namespaces: "{ns}", "{harness_ns}". '
        f"Methods: {', '.join(context.deployed_methods)}. "
        f"Release: {context.release}.",
        emoji="✅",
    )
