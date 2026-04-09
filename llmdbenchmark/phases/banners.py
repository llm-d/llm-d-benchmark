"""Decorative CLI banners and summary blocks for the lifecycle phases.

All "horizontal rule + labeled fields" output the CLI prints (start-of-phase
banners, end-of-phase summaries) lives here so the phase modules stay
focused on orchestration logic.

Public functions:
    ``print_standup_banner(context, result, logger)``
        Standup completion banner -- "STANDUP COMPLETE".

    ``print_run_banner(context, logger)``
        Run completion banner -- "BENCHMARK RUN SUMMARY".

    ``print_teardown_banner(context, logger)``
        Teardown completion line.

    ``print_experiment_start_banner(experiment_plan, stop_on_error,
                                    skip_teardown, logger)``
        DoE experiment kickoff banner -- "DoE EXPERIMENT".

The phase-start banner that runs at the beginning of standup/teardown
(``print_phase_banner``) lives in ``llmdbenchmark.utilities.cluster``
because it needs cluster info that is resolved during ``ensure_infra``.
It is re-exported here for a unified import surface.
"""

from llmdbenchmark.utilities.cluster import print_phase_banner  # noqa: F401

# Banner widths -- kept as constants so all banners stay visually consistent.
_BANNER_WIDTH_NARROW = 60   # Run summary
_BANNER_WIDTH_WIDE = 62     # Standup, experiment, etc.


def print_standup_banner(context, result, logger):
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

    W = _BANNER_WIDTH_WIDE
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


def print_run_banner(context, logger):
    """Print the BENCHMARK RUN SUMMARY banner.

    Reads run-phase fields from ``context`` (mode, harness, workload, model,
    namespace, parallelism, experiment_ids, results_dir).  Includes a
    reproducible kubectl/oc command for inspecting PVC results.
    """
    endpoint_url = context.endpoint_url
    run_config_file = context.run_config_file
    is_run_only = bool(endpoint_url or run_config_file)
    mode = "run-only" if is_run_only else "full"
    if context.generate_config_only:
        mode = "generate-config"
    harness = context.harness_name or "inference-perf"

    results_dir = context.run_results_dir()
    namespace = context.harness_namespace or context.namespace or "unknown"
    model_name = context.model_name or "unknown"
    workload = context.harness_profile or "unknown"
    experiment_ids = getattr(context, "experiment_ids", []) or []
    parallelism = context.harness_parallelism or 1

    W = _BANNER_WIDTH_NARROW
    logger.line_break()
    logger.log_info("=" * W)
    logger.log_info("BENCHMARK RUN SUMMARY")
    logger.log_info("=" * W)
    logger.log_info(f"  Harness:       {harness}")
    logger.log_info(f"  Workload:      {workload}")
    logger.log_info(f"  Model:         {model_name}")
    logger.log_info(f"  Namespace:     {namespace}")
    logger.log_info(f"  Mode:          {mode}")
    logger.log_info(f"  Parallelism:   {parallelism}")
    if experiment_ids:
        logger.log_info(f"  Treatments:    {len(experiment_ids)}")
        for eid in experiment_ids:
            logger.log_info(f"    - {eid}")
            for i in range(1, parallelism + 1):
                local_path = results_dir / f"{eid}_{i}"
                if local_path.exists():
                    file_count = sum(1 for f in local_path.rglob("*") if f.is_file())
                    logger.log_info(
                        f"      [{i}/{parallelism}] {local_path.name} "
                        f"({file_count} files)"
                    )

    kube_bin = "oc" if context.is_openshift else "kubectl"
    logger.log_info(f"  Local results: {results_dir}")
    logger.log_info(
        f"  PVC results:   {kube_bin} exec -n {namespace} "
        f"$({kube_bin} get pod -n {namespace} -l role=llm-d-benchmark-data-access "
        f"-o jsonpath='{{.items[0].metadata.name}}') -- ls /requests/"
    )
    logger.log_info("=" * W)
    logger.log_info(
        f"Run complete (mode={mode}, harness={harness}).",
        emoji="✅",
    )


def print_teardown_banner(context, logger):
    """Print the teardown completion line with namespaces, methods, and release."""
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


def print_experiment_start_banner(
    experiment_plan, stop_on_error: bool, skip_teardown: bool, logger
):
    """Print the kickoff banner for a DoE experiment matrix.

    Shown once at the start of ``execute_experiment`` before any treatment
    runs.  The end-of-experiment summary is rendered by
    ``ExperimentSummary.print_table()`` from ``llmdbenchmark.experiment.summary``.
    """
    W = _BANNER_WIDTH_WIDE
    logger.log_info("=" * W)
    logger.log_info("  DoE EXPERIMENT")
    logger.log_info("=" * W)
    logger.log_info(f"  Name:             {experiment_plan.name}")
    logger.log_info(f"  Setup treatments: {len(experiment_plan.setup_treatments)}")
    logger.log_info(f"  Run treatments:   {experiment_plan.run_treatments_count}")
    logger.log_info(f"  Total matrix:     {experiment_plan.total_matrix}")
    if experiment_plan.harness:
        logger.log_info(f"  Harness:          {experiment_plan.harness}")
    if experiment_plan.profile:
        logger.log_info(f"  Profile:          {experiment_plan.profile}")
    logger.log_info(f"  Continue on error: {not stop_on_error}")
    logger.log_info(f"  Skip teardown:    {skip_teardown}")
    logger.log_info("=" * W)
    logger.line_break()
