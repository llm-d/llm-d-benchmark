"""Standup phase -- deploy infrastructure, gateway, and model pods.

Public API:
    ``do_standup(args, logger, render_plan_errors)`` -- re-entrant core used
        by the experiment orchestrator.
    ``execute_standup(args, logger, render_plan_errors)`` -- top-level CLI
        entry point.  Prints the standup summary and auto-chains to
        ``do_smoketest`` unless ``--skip-smoketest`` is set.
    ``check_model_access(...)`` -- verify HuggingFace access for each unique
        model before running any cluster operations.
    ``print_standup_summary(...)`` -- print the "STANDUP COMPLETE" banner.

``execute_standup`` imports ``do_smoketest`` from ``phases.smoketest`` to
implement the auto-chain.  The import direction is one-way
(``standup -> smoketest``); ``phases.smoketest`` must not import from this
module.
"""

import sys

from llmdbenchmark.config import config
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.executor.step import Phase
from llmdbenchmark.executor.step_executor import StepExecutor
from llmdbenchmark.standup.steps import get_standup_steps
from llmdbenchmark.utilities.huggingface import check_model_access as _check_access, GatedStatus

from llmdbenchmark.phases.common import (
    PhaseError,
    load_all_stacks_info,
    parse_namespaces,
    resolve_deploy_methods,
)
from llmdbenchmark.phases.smoketest import do_smoketest


def do_standup(args, logger, render_plan_errors):
    """Core standup logic. Returns (context, result). Raises PhaseError on failure."""
    rendered_paths = getattr(render_plan_errors, "rendered_paths", [])
    all_stacks_info = load_all_stacks_info(rendered_paths)
    plan_info = all_stacks_info[0] if all_stacks_info else {}
    deployed_methods = resolve_deploy_methods(args, plan_info, logger)

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
        current_phase=Phase.STANDUP,
        kubeconfig=getattr(args, "kubeconfig", None),
        deployed_methods=deployed_methods,
        namespace=namespace,
        harness_namespace=harness_ns,
        model_name=plan_info.get("model_name"),
        logger=logger,
    )

    check_model_access(context, all_stacks_info, logger)

    executor = StepExecutor(
        steps=get_standup_steps(),
        context=context,
        logger=logger,
        max_parallel_stacks=getattr(args, "parallel", 4),
    )

    step_spec = getattr(args, "step", None)
    result = executor.execute(step_spec=step_spec)

    if result.has_errors:
        raise PhaseError(f"Standup failed:\n{result.summary()}")

    return context, result


def execute_standup(args, logger, render_plan_errors):
    """Build execution context and run standup steps."""
    try:
        context, result = do_standup(args, logger, render_plan_errors)
    except PhaseError as e:
        logger.log_error(str(e))
        sys.exit(1)

    print_standup_summary(context, result, logger)

    # Auto-chain smoketest after standup unless --skip-smoketest
    skip_smoketest = getattr(args, "skip_smoketest", False)
    if not skip_smoketest:
        logger.log_info("")
        logger.log_info(
            "Running smoketests...",
            emoji="🔍",
        )
        try:
            do_smoketest(args, logger, render_plan_errors)
        except PhaseError as e:
            logger.log_error(str(e))
            sys.exit(1)


def check_model_access(context, all_stacks_info, logger):
    """Verify HuggingFace access for every unique model across stacks.

    Exits immediately if any gated model is inaccessible. Skipped in dry-run.
    """
    if context.dry_run:
        return

    checked: set[str] = set()
    for stack_info in all_stacks_info:
        model_id = stack_info.get("model_name")
        if not model_id or model_id in checked:
            continue
        checked.add(model_id)

        hf_token = stack_info.get("hf_token")
        stack_name = stack_info.get("stack_name", "")
        prefix = f"[{stack_name}] " if stack_name and len(all_stacks_info) > 1 else ""

        logger.log_info(
            f'{prefix}Checking HuggingFace access for "{model_id}"...',
            emoji="🔑",
        )

        result = _check_access(model_id, hf_token)

        if result.ok:
            if result.gated == GatedStatus.NOT_GATED:
                logger.log_info(
                    f'{prefix}Model "{model_id}" is not gated -- '
                    f"access is authorized by default",
                    emoji="✅",
                )
            elif result.gated == GatedStatus.GATED:
                logger.log_info(
                    f'{prefix}Verified access to gated model "{model_id}" '
                    f"is authorized",
                    emoji="✅",
                )
            else:
                logger.log_warning(f"{prefix}{result.detail}")
        else:
            raise PhaseError(f"{prefix}{result.detail}")


def print_standup_summary(context, result, logger):
    """Print the standup completion banner with namespace, method, and endpoint info."""
    logger.line_break()

    ns = context.namespace or "unknown"
    harness_ns = context.harness_namespace or ns
    username = context.username or "unknown"
    platform = context.platform_type
    model = context.model_name or "unknown"
    methods = (
        ", ".join(context.deployed_methods) if context.deployed_methods else "default"
    )
    stacks = len(context.rendered_stacks)
    mode = "dry-run" if context.dry_run else "live"
    endpoints = context.deployed_endpoints or {}

    W = 62
    logger.log_info("═" * W)
    logger.log_info(f"  STANDUP COMPLETE")
    logger.log_info("═" * W)
    logger.log_info(f"  User:       {username}")
    logger.log_info(f"  Platform:   {platform}")
    logger.log_info(f"  Mode:       {mode}")
    logger.log_info(f"  Model:      {model}")
    logger.log_info(f"  Namespace:  {ns}")
    if harness_ns != ns:
        logger.log_info(f"  Harness NS: {harness_ns}")
    logger.log_info(f"  Methods:    {methods}")
    logger.log_info(f"  Stacks:     {stacks}")

    total_steps = len(result.global_results)
    for sr in result.stack_results:
        total_steps += len(sr.step_results)
    passed = sum(1 for r in result.global_results if r.success)
    for sr in result.stack_results:
        passed += sum(1 for r in sr.step_results if r.success)
    skipped = sum(1 for r in result.global_results if r.message == "Skipped")
    for sr in result.stack_results:
        skipped += sum(1 for r in sr.step_results if r.message == "Skipped")

    steps_summary = f"{passed}/{total_steps} passed"
    if skipped:
        steps_summary += f", {skipped} skipped"
    logger.log_info(f"  Steps:      {steps_summary}")

    if endpoints:
        logger.log_info("─" * W)
        logger.log_info(f"  Deployed Endpoints:")
        for name, url in endpoints.items():
            logger.log_info(f"    {name}: {url}")

    logger.log_info("═" * W)
    logger.line_break()
    logger.log_info(f"Workspace: {context.workspace}")
    logger.log_info("All standup steps complete.", emoji="✅")
