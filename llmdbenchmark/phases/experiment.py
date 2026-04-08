"""Experiment phase -- orchestrate a full DoE setup × run treatment matrix.

Public API:
    ``execute_experiment(args, logger)`` -- top-level CLI entry point.
        Parses the experiment YAML, synthesizes a default treatment if
        ``setup.treatments`` is missing, then for each setup treatment runs
        render → standup → smoketest → run → teardown, capturing results in
        an ``ExperimentSummary``.

This is the only ``phases/*`` module that imports from sibling phase
modules.  It composes ``do_standup``, ``do_smoketest``, ``do_run``, and
``do_teardown`` to implement the experiment matrix.

Note on global state: the experiment temporarily mutates
``config.workspace`` and ``config.plan_dir`` per treatment so that
per-treatment plans and results go into an isolated subdirectory.  The
mutation is wrapped in ``try/finally`` so an uncaught exception mid-matrix
cannot leak a stale workspace into subsequent code.
"""

import time
from pathlib import Path

from llmdbenchmark.config import config

from llmdbenchmark.phases.banners import print_experiment_start_banner
from llmdbenchmark.phases.common import (
    PhaseError,
    render_plans_for_experiment,
)
from llmdbenchmark.phases.standup import do_standup
from llmdbenchmark.phases.smoketest import do_smoketest
from llmdbenchmark.phases.run import do_run
from llmdbenchmark.phases.teardown import do_teardown


def execute_experiment(args, logger):
    """Orchestrate a full DoE experiment: setup × run treatment matrix."""
    from llmdbenchmark.experiment.parser import parse_experiment, SetupTreatment
    from llmdbenchmark.experiment.summary import ExperimentSummary

    experiment_file = Path(args.experiments)
    experiment_plan = parse_experiment(experiment_file)

    # When no setup.treatments are defined, synthesize a single "default"
    # treatment with no overrides so the spec's defaults flow through.
    if not experiment_plan.has_setup_phase:
        experiment_plan.setup_treatments = [SetupTreatment(name="default")]
        experiment_plan.has_setup_phase = True
        logger.log_info(
            f"No setup.treatments in {experiment_file.name} -- "
            f"running a single cycle with spec defaults."
        )

    # Wire experiment-level harness/profile as fallbacks for CLI args
    if experiment_plan.harness and not getattr(args, "harness", None):
        args.harness = experiment_plan.harness
    if experiment_plan.profile and not getattr(args, "workload", None):
        args.workload = experiment_plan.profile

    total_setup = len(experiment_plan.setup_treatments)
    total_run = experiment_plan.run_treatments_count
    stop_on_error = getattr(args, "stop_on_error", False)
    skip_teardown = getattr(args, "skip_teardown", False)

    summary = ExperimentSummary(
        experiment_name=experiment_plan.name,
        total_setup_treatments=total_setup,
        total_run_treatments=total_run,
    )

    print_experiment_start_banner(
        experiment_plan, stop_on_error, skip_teardown, logger,
    )

    base_workspace = config.workspace
    base_plan_dir = config.plan_dir

    try:
        for i, setup_treatment in enumerate(experiment_plan.setup_treatments, 1):
            treatment_start = time.time()
            treatment_name = setup_treatment.name
            logger.line_break()
            logger.log_info(
                f"[{i}/{total_setup}] Setup treatment: {treatment_name}",
                emoji="🔧",
            )

            treatment_dir = Path(base_workspace) / f"setup-treatment-{treatment_name}"
            treatment_dir.mkdir(parents=True, exist_ok=True)
            treatment_plan_dir = treatment_dir / "plan"
            treatment_plan_dir.mkdir(parents=True, exist_ok=True)

            config.workspace = treatment_dir
            config.plan_dir = treatment_plan_dir

            try:
                render_plan_errors = render_plans_for_experiment(
                    args, logger, setup_overrides=setup_treatment.overrides
                )
                override_note = " with setup overrides" if setup_treatment.overrides else ""
                logger.log_info(
                    f"Plans rendered{override_note} for {treatment_name}",
                    emoji="✅",
                )
            except (PhaseError, Exception) as e:
                duration = time.time() - treatment_start
                error_msg = str(e)
                logger.log_error(f"Rendering failed for {treatment_name}: {error_msg}")
                summary.record_failure(
                    treatment_name,
                    "render",
                    error_msg,
                    run_total=total_run,
                    workspace_dir=str(treatment_dir),
                    duration=duration,
                )
                if stop_on_error:
                    break
                continue

            try:
                standup_context, standup_result = do_standup(
                    args, logger, render_plan_errors
                )
                logger.log_info(f"Standup complete for {treatment_name}", emoji="✅")
            except PhaseError as e:
                error_msg = str(e)
                logger.log_error(f"Standup failed for {treatment_name}: {error_msg}")
                # Attempt teardown to clean up any partially deployed resources
                if not skip_teardown:
                    try:
                        do_teardown(args, logger, render_plan_errors)
                        logger.log_info(
                            f"Cleanup teardown complete for {treatment_name}",
                            emoji="🧹",
                        )
                    except PhaseError:
                        logger.log_warning(
                            f"Cleanup teardown also failed for {treatment_name} "
                            f"(resources may need manual cleanup)"
                        )
                duration = time.time() - treatment_start
                summary.record_failure(
                    treatment_name,
                    "standup",
                    error_msg,
                    run_total=total_run,
                    workspace_dir=str(treatment_dir),
                    duration=duration,
                )
                if stop_on_error:
                    break
                continue

            # --- Phase 2b: Smoketest ---
            try:
                do_smoketest(args, logger, render_plan_errors)
                logger.log_info(
                    f"Smoketest complete for {treatment_name}", emoji="✅"
                )
            except PhaseError as e:
                error_msg = str(e)
                logger.log_error(
                    f"Smoketest failed for {treatment_name}: {error_msg}"
                )
                if not skip_teardown:
                    try:
                        do_teardown(args, logger, render_plan_errors)
                    except PhaseError:
                        pass
                duration = time.time() - treatment_start
                summary.record_failure(
                    treatment_name,
                    "smoketest",
                    error_msg,
                    run_total=total_run,
                    workspace_dir=str(treatment_dir),
                    duration=duration,
                )
                if stop_on_error:
                    break
                continue

            run_succeeded = False
            run_error_msg = None
            try:
                run_context, run_result = do_run(
                    args,
                    logger,
                    render_plan_errors,
                    experiment_file_override=str(experiment_plan.experiment_file),
                )
                run_succeeded = True
                logger.log_info(f"Run complete for {treatment_name}", emoji="✅")
            except PhaseError as e:
                run_error_msg = str(e)
                logger.log_error(f"Run failed for {treatment_name}: {run_error_msg}")

            # --- Phase 4: Teardown (always attempted unless --skip-teardown) ---
            teardown_error = None
            if not skip_teardown:
                try:
                    do_teardown(args, logger, render_plan_errors)
                    logger.log_info(f"Teardown complete for {treatment_name}", emoji="✅")
                except PhaseError as e:
                    teardown_error = str(e)
                    logger.log_warning(
                        f"Teardown failed for {treatment_name}: {teardown_error}"
                    )
            else:
                logger.log_info(
                    f"Teardown skipped for {treatment_name} (--skip-teardown)",
                    emoji="⏭️",
                )

            # --- Record result ---
            duration = time.time() - treatment_start
            if run_succeeded and not teardown_error:
                summary.record_success(
                    treatment_name,
                    run_completed=total_run,
                    run_total=total_run,
                    workspace_dir=str(treatment_dir),
                    duration=duration,
                )
            elif run_succeeded and teardown_error:
                summary.record_failure(
                    treatment_name,
                    "teardown",
                    teardown_error,
                    run_completed=total_run,
                    run_total=total_run,
                    workspace_dir=str(treatment_dir),
                    duration=duration,
                )
            else:
                summary.record_failure(
                    treatment_name,
                    "run",
                    run_error_msg,
                    run_completed=0,
                    run_total=total_run,
                    workspace_dir=str(treatment_dir),
                    duration=duration,
                )

            if not run_succeeded and stop_on_error:
                break
    finally:
        # Restore the workspace/plan_dir regardless of how the loop exited.
        # This protects against stale config leaking into post-loop code or
        # later CLI operations if an unexpected exception escapes the loop.
        config.workspace = base_workspace
        config.plan_dir = base_plan_dir

    summary_path = Path(base_workspace) / "experiment-summary.yaml"
    summary.write(summary_path)
    logger.log_info(f"Experiment summary written to {summary_path}", emoji="📊")
    logger.line_break()
    summary.print_table(logger)
