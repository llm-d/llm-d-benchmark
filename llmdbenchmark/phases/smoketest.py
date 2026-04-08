"""Smoketest phase -- health checks, inference tests, and config validation.

Public API:
    ``do_smoketest(args, logger, render_plan_errors)`` -- re-entrant core used
        by the standup auto-chain and the experiment orchestrator.
    ``execute_smoketest(args, logger, render_plan_errors)`` -- top-level CLI
        entry point invoked by ``cli.dispatch_cli``.

This module must NOT import from ``phases.standup`` or ``phases.experiment``
to avoid circular imports.  ``phases.standup`` imports ``do_smoketest``
from here to implement the auto-chain.
"""

import sys

from llmdbenchmark.config import config
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.executor.step import Phase
from llmdbenchmark.executor.step_executor import StepExecutor
from llmdbenchmark.smoketests.steps import get_smoketest_steps

from llmdbenchmark.phases.common import (
    PhaseError,
    load_all_stacks_info,
    parse_namespaces,
    resolve_deploy_methods,
)


def do_smoketest(args, logger, render_plan_errors):
    """Core smoketest logic. Returns (context, result). Raises PhaseError on failure."""
    rendered_paths = getattr(render_plan_errors, "rendered_paths", [])
    all_stacks_info = load_all_stacks_info(rendered_paths)
    plan_info = all_stacks_info[0] if all_stacks_info else {}
    deployed_methods = resolve_deploy_methods(args, plan_info, logger, phase="smoketest")

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
        current_phase=Phase.SMOKETEST,
        kubeconfig=getattr(args, "kubeconfig", None),
        deployed_methods=deployed_methods,
        namespace=namespace,
        harness_namespace=harness_ns,
        model_name=plan_info.get("model_name"),
        logger=logger,
    )

    executor = StepExecutor(
        steps=get_smoketest_steps(),
        context=context,
        logger=logger,
        max_parallel_stacks=getattr(args, "parallel", 4),
    )

    step_spec = getattr(args, "step", None)
    result = executor.execute(step_spec=step_spec)

    if result.has_errors:
        raise PhaseError(f"Smoketest failed:\n{result.summary()}")

    logger.log_info("All smoketest steps complete.", emoji="✅")
    return context, result


def execute_smoketest(args, logger, render_plan_errors):
    """Build execution context and run smoketest steps."""
    try:
        do_smoketest(args, logger, render_plan_errors)
    except PhaseError as e:
        logger.log_error(str(e))
        sys.exit(1)
