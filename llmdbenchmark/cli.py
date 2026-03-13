"""Entry point for the llmdbenchmark CLI.

Parses arguments, sets up the workspace, and dispatches to
plan / standup / teardown / run subcommands.
"""

import argparse
import logging
import os
import sys
import json
import tempfile
from pathlib import Path

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
from llmdbenchmark.parser.render_specification import RenderSpecification
from llmdbenchmark.exceptions.exceptions import TemplateError
from llmdbenchmark.parser.render_plans import RenderPlans
from llmdbenchmark.parser.version_resolver import VersionResolver
from llmdbenchmark.parser.cluster_resource_resolver import ClusterResourceResolver
from llmdbenchmark.executor.step import Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.executor.step_executor import StepExecutor
from llmdbenchmark.standup.steps import get_standup_steps
from llmdbenchmark.teardown.steps import get_teardown_steps

from llmdbenchmark.run.steps import get_run_steps


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

    if args.command in (
        Command.PLAN.value,
        Command.STANDUP.value,
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

        # Create version resolver for auto-version resolution during planning
        version_resolver = VersionResolver(logger=logger, dry_run=args.dry_run)

        # Create cluster resource resolver for auto-detecting accelerator,
        # network, and affinity values from the live cluster during planning
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

    if args.command == Command.STANDUP.value:
        _execute_standup(args, logger, render_plan_errors)

    if args.command == Command.TEARDOWN.value:
        _execute_teardown(args, logger, render_plan_errors)

    if args.command == Command.RUN.value:
        _execute_run(args, logger, render_plan_errors)


def _load_stack_info_from_config(config_file, stack_name=""):
    """Parse a single stack's config.yaml into a plan-info dict."""
    import yaml as _yaml

    try:
        with open(config_file, encoding="utf-8") as f:
            plan_config = _yaml.safe_load(f)
        if plan_config:
            return {
                "stack_name": stack_name,
                "namespace": (plan_config.get("namespace", {}).get("name")),
                "harness_namespace": (plan_config.get("harness", {}).get("namespace")),
                "model_name": (
                    plan_config.get("model", {}).get("huggingfaceId")
                    or plan_config.get("model", {}).get("name")
                ),
                "hf_token": (plan_config.get("huggingface", {}).get("token")),
                "release": plan_config.get("release"),
                "standalone_enabled": (
                    plan_config.get("standalone", {}).get("enabled", False)
                ),
                "modelservice_enabled": (
                    plan_config.get("modelservice", {}).get("enabled", False)
                ),
            }
    except (OSError, _yaml.YAMLError):
        pass
    return {}


def _load_all_stacks_info(rendered_paths):
    """Read configuration from every rendered stack's config.yaml.

    Returns a list of per-stack info dicts (one per rendered path that
    has a valid config.yaml).
    """
    stacks_info = []
    for stack_path in rendered_paths or []:
        config_file = stack_path / "config.yaml"
        if config_file.exists():
            info = _load_stack_info_from_config(config_file, stack_name=stack_path.name)
            if info:
                stacks_info.append(info)
    return stacks_info


def _load_plan_info(rendered_paths):
    """Read key configuration from the first rendered plan config.yaml.

    Returns a dict with namespace, harness_namespace, model_name,
    hf_token, and release — or an empty dict if no config is found.
    """
    all_info = _load_all_stacks_info(rendered_paths)
    return all_info[0] if all_info else {}


def _resolve_deploy_methods(args, plan_info, logger, phase="standup"):
    """Determine deployment methods from CLI flag or plan config.

    Priority: CLI --methods > auto-detect from plan config > phase-specific default.
    standalone.enabled defaults to false, so if true the scenario explicitly chose it.
    For teardown, no fallback — user must specify --methods if config is missing.
    """
    methods_str = getattr(args, "methods", None)
    if methods_str:
        return [m.strip() for m in methods_str.split(",")]

    standalone = plan_info.get("standalone_enabled", False)
    modelservice = plan_info.get("modelservice_enabled", False)

    if phase == "run":
        # Run phase returns all enabled methods for endpoint detection
        methods = []
        if standalone:
            methods.append("standalone")
        if modelservice:
            methods.append("modelservice")
        if methods:
            logger.log_info(
                f"Auto-detected deploy method(s) from plan: {', '.join(methods)}"
            )
            return methods
    else:
        # Standup/teardown: treat as mutually exclusive
        if standalone:
            logger.log_info("Auto-detected deploy method from plan: standalone")
            return ["standalone"]
        if modelservice:
            logger.log_info("Auto-detected deploy method from plan: modelservice")
            return ["modelservice"]

    if phase == "teardown":
        logger.log_error(
            "Cannot determine deployment method: no plan config found and "
            "--methods not specified. Use --methods standalone or "
            "--methods modelservice to specify what to tear down."
        )
        sys.exit(1)

    return ["modelservice"]


def _execute_standup(args, logger, render_plan_errors):
    """Build execution context and run standup steps."""
    rendered_paths = getattr(render_plan_errors, "rendered_paths", [])
    all_stacks_info = _load_all_stacks_info(rendered_paths)
    plan_info = all_stacks_info[0] if all_stacks_info else {}
    deployed_methods = _resolve_deploy_methods(args, plan_info, logger)

    # Resolve namespace from plan config (CLI --namespace overrides)
    cli_ns = getattr(args, "namespace", None)
    namespace = cli_ns or plan_info.get("namespace")
    harness_ns = plan_info.get("harness_namespace") or namespace

    if not namespace:
        logger.log_error(
            "No namespace specified. Set 'namespace.name' in your scenario "
            "YAML, defaults.yaml, or pass --namespace on the CLI."
        )
        sys.exit(1)

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

    # Gated model access check — fail fast for ALL stacks' models
    _check_model_access(context, all_stacks_info, logger)

    executor = StepExecutor(
        steps=get_standup_steps(),
        context=context,
        logger=logger,
        max_parallel_stacks=getattr(args, "parallel", 4),
    )

    step_spec = getattr(args, "step", None)
    result = executor.execute(step_spec=step_spec)

    if result.has_errors:
        logger.log_error(f"Standup failed:\n{result.summary()}")
        sys.exit(1)

    _print_standup_summary(context, result, logger)


def _check_model_access(context, all_stacks_info, logger):
    """Verify HuggingFace access for every unique model across stacks.

    Exits immediately if any gated model is inaccessible. Skipped in dry-run.
    """
    if context.dry_run:
        return

    from llmdbenchmark.utilities.huggingface import (
        check_model_access,
        GatedStatus,
    )

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

        result = check_model_access(model_id, hf_token)

        if result.ok:
            if result.gated == GatedStatus.NOT_GATED:
                logger.log_info(
                    f'{prefix}Model "{model_id}" is not gated — '
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
            logger.log_error(f"❌ {prefix}{result.detail}")
            sys.exit(1)


def _print_standup_summary(context, result, logger):
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


def _execute_teardown(args, logger, render_plan_errors):
    """Build execution context and run teardown steps."""
    rendered_paths = getattr(render_plan_errors, "rendered_paths", [])
    plan_info = _load_plan_info(rendered_paths)
    deployed_methods = _resolve_deploy_methods(
        args, plan_info, logger, phase="teardown"
    )

    cli_namespace = None
    cli_harness_namespace = None
    ns_str = getattr(args, "namespace", None)
    if ns_str:
        parts = [p.strip() for p in ns_str.split(",")]
        cli_namespace = parts[0]
        cli_harness_namespace = parts[1] if len(parts) > 1 else parts[0]

    namespace = cli_namespace or plan_info.get("namespace")
    harness_ns = (
        cli_harness_namespace or plan_info.get("harness_namespace") or namespace
    )

    if not namespace:
        logger.log_error(
            "No namespace specified. Set 'namespace.name' in your scenario "
            "YAML, defaults.yaml, or pass --namespace on the CLI."
        )
        sys.exit(1)

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
        logger.log_error(f"Teardown failed:\n{result.summary()}")
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


def _execute_run(args, logger, render_plan_errors):
    """Build execution context and run experiment steps."""
    rendered_paths = getattr(render_plan_errors, "rendered_paths", [])
    all_stacks_info = _load_all_stacks_info(rendered_paths)
    plan_info = all_stacks_info[0] if all_stacks_info else {}

    deployed_methods = _resolve_deploy_methods(args, plan_info, logger, phase="run")

    cli_namespace = None
    cli_harness_namespace = None
    ns_str = getattr(args, "namespace", None)
    if ns_str:
        parts = [p.strip() for p in ns_str.split(",")]
        cli_namespace = parts[0]
        cli_harness_namespace = parts[1] if len(parts) > 1 else parts[0]

    namespace = cli_namespace or plan_info.get("namespace")
    harness_ns = (
        cli_harness_namespace or plan_info.get("harness_namespace") or namespace
    )

    endpoint_url = getattr(args, "endpoint_url", None)
    run_config_file = getattr(args, "run_config", None)
    is_run_only = bool(endpoint_url or run_config_file)

    if not namespace and not is_run_only:
        logger.log_error(
            "No namespace specified. Set 'namespace.name' in your scenario "
            "YAML, defaults.yaml, or pass --namespace on the CLI."
        )
        sys.exit(1)

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
        experiment_treatments_file=getattr(args, "experiments", None),
        profile_overrides=getattr(args, "overrides", None),
        harness_output=getattr(args, "output", "local") or "local",
        harness_parallelism=int(getattr(args, "parallelism", 1) or 1),
        harness_wait_timeout=int(getattr(args, "wait_timeout", 3600) or 3600),
        harness_debug=getattr(args, "debug", False),
        harness_skip_run=getattr(args, "skip", False),
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

    mode = "run-only" if is_run_only else "full"
    if context.generate_config_only:
        mode = "generate-config"
    harness = context.harness_name or "inference-perf"

    if result.has_errors:
        logger.log_error(f"Run failed:\n{result.summary()}")
        sys.exit(1)

    logger.line_break()
    logger.log_info(
        f"Run complete (mode={mode}, harness={harness}).",
        emoji="✅",
    )


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
    }

    active = {
        k: v for k, v in os.environ.items()
        if k in _ENV_TO_CLI
    }
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
    }
    return _ALIASES.get(flag, [flag])


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
    teardown.add_subcommands(subparsers)
    run.add_subcommands(subparsers)

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

    # Ensure the workspace dir name contains "workspace" to avoid
    # accidentally writing into the repo root (workspace*/ is in .gitignore).
    # Each invocation gets its own timestamped sub-directory.
    if args.workspace:
        overall_workspace = Path(args.workspace)
    else:
        overall_workspace = Path(tempfile.mkdtemp(prefix="workspace_llmdbench_"))
    if "workspace" not in overall_workspace.name.lower():
        overall_workspace = overall_workspace.with_name(
            f"workspace_{overall_workspace.name}"
        )
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
