"""Entry point for the llmdbenchmark CLI.

Parses arguments, sets up the workspace, and dispatches to
plan / standup / teardown / run / experiment subcommands.
"""

import argparse
import json
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

import yaml as _yaml

from llmdbenchmark import __version__, __package_name__, __package_home__
from llmdbenchmark.interface.env import env, env_bool
from llmdbenchmark.config import config
from llmdbenchmark.logging.logger import get_logger
from llmdbenchmark.utilities.os.filesystem import (
    create_workspace,
    create_sub_dir_workload,
    get_absolute_path,
    resolve_specification_file,
)
from llmdbenchmark.interface.commands import Command
from llmdbenchmark.interface import plan, standup, teardown, run
from llmdbenchmark.interface import smoketest as smoketest_interface
from llmdbenchmark.interface import experiment as experiment_interface
from llmdbenchmark.parser.render_specification import RenderSpecification
from llmdbenchmark.exceptions.exceptions import TemplateError
from llmdbenchmark.parser.render_plans import RenderPlans
from llmdbenchmark.parser.version_resolver import VersionResolver
from llmdbenchmark.parser.cluster_resource_resolver import ClusterResourceResolver

from llmdbenchmark.phases.standup import execute_standup
from llmdbenchmark.phases.smoketest import execute_smoketest
from llmdbenchmark.phases.teardown import execute_teardown
from llmdbenchmark.phases.run import execute_run
from llmdbenchmark.phases.experiment import execute_experiment


def setup_workspace(
    workspace_path: Path,
    plan_dir: Path,
    log_dir: Path,
    verbose: bool = False,
    dry_run: bool = False,
) -> None:
    """Set workspace paths and runtime flags on the global config singleton."""
    config.workspace = workspace_path
    config.plan_dir = plan_dir
    config.log_dir = log_dir
    config.verbose = verbose
    config.dry_run = dry_run


def dispatch_cli(args: argparse.Namespace, logger: logging.Logger) -> None:
    """Render plans and dispatch to the appropriate phase executor."""

    # Experiment command manages its own rendering per setup treatment
    if args.command == Command.EXPERIMENT.value:
        execute_experiment(args, logger)
        return

    if args.command in (
        Command.PLAN.value,
        Command.STANDUP.value,
        Command.SMOKETEST.value,
        Command.TEARDOWN.value,
        Command.RUN.value,
    ):

        # Resolve templates, scenarios, and values into the workspace
        specification_as_dict = RenderSpecification(
            specification_file=args.specification_file,
            base_dir=args.base_dir,
        ).eval()

        logger.log_info(
            "Specification file rendered and validated successfully.",
            emoji="✅",
        )

        logger.log_debug(
            "Using specification file to fully render templates into complete system stack plans."
        )

        version_resolver = VersionResolver(logger=logger, dry_run=args.dry_run)
        cluster_resource_resolver = ClusterResourceResolver(
            logger=logger,
            dry_run=args.dry_run,
        )

        render_plan_errors = RenderPlans(
            template_dir=specification_as_dict["template_dir"]["path"],
            defaults_file=specification_as_dict["values_file"]["path"],
            scenarios_file=specification_as_dict["scenario_file"]["path"],
            output_dir=config.plan_dir,
            version_resolver=version_resolver,
            cluster_resource_resolver=cluster_resource_resolver,
            cli_namespace=getattr(args, "namespace", None),
            cli_model=getattr(args, "models", None),
            cli_methods=getattr(args, "methods", None),
            cli_monitoring=getattr(args, "monitoring", False),
        ).eval()

        try:
            if render_plan_errors.has_errors:
                error_dump = json.dumps(render_plan_errors.to_dict(), indent=2)
                raise TemplateError(
                    message="Errors occurred while rendering the specification.",
                    context={"\nrender_plan_errors": error_dump},
                )
        except TemplateError as e:
            logger.log_error(f"Rendering failed: {e}")
            sys.exit(1)

        # Pre-render Helm chart manifests so the plan directory contains
        # all K8s resources (both Jinja2-rendered and Helm-rendered).
        # This enables kustomize overlays and full manifest inspection.
        # Runs even in dry-run mode — helmfile template is purely local
        # and does not touch the cluster.
        _render_helm_manifests(config.plan_dir, logger)

    if args.command == Command.STANDUP.value:
        execute_standup(args, logger, render_plan_errors)

    if args.command == Command.SMOKETEST.value:
        execute_smoketest(args, logger, render_plan_errors)

    if args.command == Command.TEARDOWN.value:
        execute_teardown(args, logger, render_plan_errors)

    if args.command == Command.RUN.value:
        execute_run(args, logger, render_plan_errors)


def _log_env_overrides(logger, args):
    """Log which supported LLMDBENCH_* env vars are set, noting CLI overrides."""
    # Only the vars we actually wire to argparse flags.
    # Anything else in the environment (e.g. internal vars) is not our concern.
    _ENV_TO_CLI = {
        "LLMDBENCH_WORKSPACE": ("workspace", "--workspace"),
        "LLMDBENCH_BASE_DIR": ("base_dir", "--base-dir"),
        "LLMDBENCH_SPEC": ("specification_file", "--spec"),
        "LLMDBENCH_DRY_RUN": ("dry_run", "--dry-run"),
        "LLMDBENCH_VERBOSE": ("verbose", "--verbose"),
        "LLMDBENCH_NON_ADMIN": ("non_admin", "--non-admin"),
        "LLMDBENCH_NAMESPACE": ("namespace", "--namespace"),
        "LLMDBENCH_MODELS": ("models", "--models"),
        "LLMDBENCH_METHODS": ("methods", "--methods"),
        "LLMDBENCH_RELEASE": ("release", "--release"),
        "LLMDBENCH_KUBECONFIG": ("kubeconfig", "--kubeconfig"),
        "LLMDBENCH_PARALLEL": ("parallel", "--parallel"),
        "LLMDBENCH_MONITORING": ("monitoring", "--monitoring"),
        "LLMDBENCH_SCENARIO": ("scenario", "--scenario"),
        "LLMDBENCH_DEEP_CLEAN": ("deep", "--deep"),
        "LLMDBENCH_MODEL": ("model", "--model"),
        "LLMDBENCH_HARNESS": ("harness", "--harness"),
        "LLMDBENCH_WORKLOAD": ("workload", "--workload"),
        "LLMDBENCH_EXPERIMENTS": ("experiments", "--experiments"),
        "LLMDBENCH_OVERRIDES": ("overrides", "--overrides"),
        "LLMDBENCH_OUTPUT": ("output", "--output"),
        "LLMDBENCH_PARALLELISM": ("parallelism", "--parallelism"),
        "LLMDBENCH_WAIT_TIMEOUT": ("wait_timeout", "--wait-timeout"),
        "LLMDBENCH_DATASET": ("dataset", "--dataset"),
        "LLMDBENCH_ENDPOINT_URL": ("endpoint_url", "--endpoint-url"),
        "LLMDBENCH_SKIP": ("skip", "--skip"),
        "LLMDBENCH_DEBUG": ("debug", "--debug"),
        "LLMDBENCH_AFFINITY": ("affinity", "--affinity"),
        "LLMDBENCH_ANNOTATIONS": ("annotations", "--annotations"),
        "LLMDBENCH_WVA": ("wva", "--wva"),
        "LLMDBENCH_SERVICE_ACCOUNT": ("serviceaccount", "--serviceaccount"),
        "LLMDBENCH_HARNESS_ENVVARS_TO_YAML": ("envvarspod", "--envvarspod"),
    }

    active = {k: v for k, v in os.environ.items() if k in _ENV_TO_CLI}
    if not active:
        return

    # Detect which CLI flags were explicitly passed on the command line
    cli_argv = sys.argv[1:]
    cli_flags_used = set()
    for token in cli_argv:
        if token.startswith("-"):
            cli_flags_used.add(token.split("=")[0])

    logger.log_info(f"Active LLMDBENCH_* environment overrides: {len(active)}")
    for k, v in sorted(active.items()):
        display = v if len(v) < 60 else v[:57] + "..."
        dest, flag = _ENV_TO_CLI[k]
        # Check all flag variants (long and short forms)
        overridden = any(f in cli_flags_used for f in _all_flag_forms(flag))
        if overridden:
            cli_val = getattr(args, dest, None)
            cli_display = str(cli_val) if cli_val is not None else ""
            if len(cli_display) > 50:
                cli_display = cli_display[:47] + "..."
            logger.log_info(
                f"  {k}={display} (overridden by CLI: {flag} {cli_display})"
            )
        else:
            logger.log_info(f"  {k}={display}")


def _all_flag_forms(flag: str) -> list[str]:
    """Return all CLI flag forms to check against sys.argv.

    For '--workspace', also checks '--ws'.
    For '--methods', also checks '-t', etc.
    """
    # Build reverse lookup from the argparse definitions
    _ALIASES = {
        "--workspace": ["--workspace", "--ws"],
        "--base-dir": ["--base-dir", "--bd"],
        "--spec": ["--specification_file", "--spec"],
        "--dry-run": ["--dry-run", "-n"],
        "--verbose": ["--verbose", "-v"],
        "--non-admin": ["--non-admin", "-i"],
        "--namespace": ["--namespace", "-p"],
        "--models": ["--models", "-m"],
        "--methods": ["--methods", "-t"],
        "--release": ["--release", "-r"],
        "--kubeconfig": ["--kubeconfig", "-k"],
        "--parallel": ["--parallel"],
        "--monitoring": ["--monitoring"],
        "--scenario": ["--scenario", "-c"],
        "--deep": ["--deep", "-d"],
        "--model": ["--model", "-m"],
        "--harness": ["--harness", "-l"],
        "--workload": ["--workload", "-w"],
        "--experiments": ["--experiments", "-e"],
        "--overrides": ["--overrides", "-o"],
        "--output": ["--output", "-r"],
        "--parallelism": ["--parallelism", "-j"],
        "--wait-timeout": ["--wait-timeout"],
        "--dataset": ["--dataset", "-x"],
        "--endpoint-url": ["--endpoint-url", "-U"],
        "--skip": ["--skip", "-z"],
        "--debug": ["--debug", "-d"],
        "--affinity": ["--affinity"],
        "--annotations": ["--annotations"],
        "--wva": ["--wva"],
        "--serviceaccount": ["--serviceaccount", "-q"],
        "--envvarspod": ["--envvarspod", "-g"],
    }
    return _ALIASES.get(flag, [flag])


def _extract_workspace_from_scenario(
    specification_file: Path,
    base_dir: Path,
) -> str | None:
    """Quick-parse the scenario YAML to extract workDir if present.

    This runs *before* the full rendering pipeline so we can use the
    scenario-specified workspace (equivalent to LLMDBENCH_CONTROL_WORK_DIR)
    as a fallback when --workspace is not given on the CLI.
    """
    import yaml as _yaml
    from jinja2 import Environment as _Env

    try:
        env = _Env(autoescape=False, trim_blocks=True, lstrip_blocks=True)
        rendered = env.from_string(specification_file.read_text()).render(
            base_dir=str(base_dir)
        )
        spec = _yaml.safe_load(rendered)
        scenario_path = spec.get("scenario_file", {}).get("path")
        if not scenario_path:
            return None

        scenario_path = Path(scenario_path)
        if not scenario_path.exists():
            return None

        with open(scenario_path, encoding="utf-8") as f:
            scenario_data = _yaml.safe_load(f)

        scenarios = scenario_data.get("scenario", [])
        if scenarios and isinstance(scenarios, list):
            return scenarios[0].get("workDir")
    except Exception:  # noqa: BLE001 -- best-effort; fall through to temp dir
        pass
    return None


def cli() -> None:
    """Parse arguments, set up workspace and logging, and dispatch the subcommand."""

    parser = argparse.ArgumentParser(
        prog="llmdbenchmark",
        description="Provision and drive experiments for LLM workloads focused on analyzing "
        "the performance of llm-d and vllm inference platform stacks. "
        f"Visit {__package_home__} for more information.",
        epilog=(
            "A command must be supplied. Commands correspond to high-level actions "
            "such as generating plans, provisioning infrastructure, or running experiments "
            "and workloads."
        ),
    )

    parser.add_argument(
        "--workspace",
        "--ws",
        default=env("LLMDBENCH_WORKSPACE"),
        help="Supply a workspace directory for placing "
        "generated items and logs, otherwise the default action is to create a "
        "temporary directory on your system.",
    )

    parser.add_argument(
        "--base-dir",
        "--bd",
        default=env("LLMDBENCH_BASE_DIR", "."),
        help="Base directory containing templates and scenarios. "
        'The default base directory is the cwd "." - we highly suggest enforcing a '
        'base_dir explicitly. For example: "BASE_DIR/templates", "BASE_DIR/scenarios".',
    )

    parser.add_argument(
        "--specification_file",
        "--spec",
        default=env("LLMDBENCH_SPEC"),
        required=not env("LLMDBENCH_SPEC"),
        help="Specification file for the experiment. Accepts a bare name (e.g. 'gpu'), "
        "a category/name (e.g. 'guides/inference-scheduling'), or a full path. "
        "Bare names are searched in config/specification/**/<name>.yaml.j2.",
    )

    parser.add_argument(
        "--non-admin",
        "-i",
        action="store_true",
        help="Run as non-cluster-level admin user.",
    )

    parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="Log all commands without executing against compute cluster, while still "
        "generating YAML and Helm documents.",
    )

    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable debug logging to console."
    )

    parser.add_argument(
        "--version",
        "--ver",
        action="version",
        version=f"{__package_name__}:{__version__}",
        help="Show program's version number and exit.",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        title="Commands",
        description="Available commands:",
    )

    plan.add_subcommands(subparsers)
    standup.add_subcommands(subparsers)
    smoketest_interface.add_subcommands(subparsers)
    teardown.add_subcommands(subparsers)
    run.add_subcommands(subparsers)
    experiment_interface.add_subcommands(subparsers)

    args = parser.parse_args()

    # Merge env vars for boolean flags (store_true can't use default=)
    if not args.dry_run:
        args.dry_run = env_bool("LLMDBENCH_DRY_RUN")
    if not args.verbose:
        args.verbose = env_bool("LLMDBENCH_VERBOSE")
    if not args.non_admin:
        args.non_admin = env_bool("LLMDBENCH_NON_ADMIN")
    if hasattr(args, "monitoring") and not args.monitoring:
        args.monitoring = env_bool("LLMDBENCH_MONITORING")
    if hasattr(args, "deep") and not args.deep:
        args.deep = env_bool("LLMDBENCH_DEEP_CLEAN")
    if hasattr(args, "skip") and not args.skip:
        args.skip = env_bool("LLMDBENCH_SKIP")
    if hasattr(args, "debug") and not args.debug:
        args.debug = env_bool("LLMDBENCH_DEBUG")

    # Convert relative/~ paths to absolute
    args.base_dir = get_absolute_path(args.base_dir)

    # Resolve --spec (bare name / category/name / full path)
    raw_spec = args.specification_file
    try:
        args.specification_file = resolve_specification_file(
            raw_spec,
            base_dir=args.base_dir,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    # Each invocation gets its own timestamped sub-directory inside the workspace.
    # Priority: --workspace CLI / LLMDBENCH_WORKSPACE env > scenario workDir
    #           > auto-generated temp dir.
    # workDir is the YAML equivalent of LLMDBENCH_CONTROL_WORK_DIR from the
    # old bash scenarios.
    if args.workspace:
        overall_workspace = Path(args.workspace)
    else:
        scenario_work_dir = _extract_workspace_from_scenario(
            args.specification_file, args.base_dir
        )
        if scenario_work_dir:
            overall_workspace = Path(scenario_work_dir).expanduser()
        else:
            overall_workspace = Path(tempfile.mkdtemp(prefix="workspace_llmdbench_"))
    overall_workspace = create_workspace(overall_workspace)
    absolute_overall_workspace_path = get_absolute_path(overall_workspace)

    current_workspace = create_sub_dir_workload(absolute_overall_workspace_path)
    absolute_workspace_path = get_absolute_path(current_workspace)

    absolute_workspace_log_dir = create_sub_dir_workload(
        absolute_workspace_path, "logs"
    )

    absolute_workspace_plan_dir = create_sub_dir_workload(
        absolute_workspace_path, "plan"
    )

    setup_workspace(
        workspace_path=absolute_workspace_path,
        plan_dir=absolute_workspace_plan_dir,
        log_dir=absolute_workspace_log_dir,
        verbose=args.verbose,
        dry_run=args.dry_run,
    )

    logger = get_logger(config.log_dir, config.verbose, __name__)

    if str(args.specification_file) != str(raw_spec):
        logger.log_info(
            f"Specification resolved: {raw_spec} to {args.specification_file}"
        )

    logger.log_info(
        f'Using Package: "{__package_name__}:{__version__}" found at {__package_home__}'
    )

    _log_env_overrides(logger, args)

    logger.log_info(
        f'Created Workspace: "{absolute_overall_workspace_path}"',
        emoji="✅",
    )

    logger.log_info(
        f'Created {__package_name__} instance in workspace: "{absolute_workspace_path}"',
        emoji="✅",
    )

    logger.line_break()

    dispatch_cli(args, logger)


if __name__ == "__main__":
    cli()
