"""Entry point for the llmdbenchmark CLI.

Parses arguments, sets up the workspace, and dispatches to
plan / standup / teardown / run / experiment subcommands.
"""

import argparse
import logging
import os
import shutil
import sys
import json
import tempfile
import time
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
from llmdbenchmark.executor.step import Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.executor.step_executor import StepExecutor
from llmdbenchmark.standup.steps import get_standup_steps
from llmdbenchmark.smoketests.steps import get_smoketest_steps
from llmdbenchmark.teardown.steps import get_teardown_steps

from llmdbenchmark.run.steps import get_run_steps
from llmdbenchmark.executor.command import CommandExecutor


class PhaseError(Exception):
    """Raised when a lifecycle phase (standup/run/teardown) fails."""

    pass


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
        _execute_experiment(args, logger)
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
            cli_monitoring=getattr(args, "monitoring", None),
            cli_wva=getattr(args, "wva", False),
            cli_stack_filter=_parse_stack_filter(getattr(args, "stack", None)),
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
        # Runs even in dry-run mode - helmfile template is purely local
        # and does not touch the cluster.
        _render_helm_manifests(config.plan_dir, logger)

    if args.command == Command.STANDUP.value:
        _execute_standup(args, logger, render_plan_errors)

    if args.command == Command.SMOKETEST.value:
        _execute_smoketest(args, logger, render_plan_errors)

    if args.command == Command.TEARDOWN.value:
        _execute_teardown(args, logger, render_plan_errors)

    if args.command == Command.RUN.value:
        _execute_run(args, logger, render_plan_errors)


def _render_helm_manifests(plan_dir: Path, logger) -> None:
    """Pre-render modelservice Helm chart manifests into each stack's plan directory.

    For each rendered stack that deploys via ``modelservice``, runs
    ``helmfile template`` against the modelservice release to produce
    the full K8s manifests the chart would create.  Output is saved as
    ``helm-modelservice.yaml`` in the stack directory alongside the
    Jinja2-rendered templates.

    Stacks that deploy via ``standalone`` are skipped entirely - they
    do not use the modelservice Helm chart, so pre-rendering it would
    produce an empty helmfile and fail with "no top-level config keys".

    This runs during the plan phase so that:
    - Users can inspect exactly what Helm will apply
    - Kustomize overlays can patch Helm-produced resources
    - The plan directory contains 100% of K8s manifests
    """
    if not plan_dir or not plan_dir.exists():
        return

    for stack_dir in sorted(plan_dir.iterdir()):
        if not stack_dir.is_dir():
            continue

        helmfile_src = stack_dir / "10_helmfile-main.yaml"
        ms_values = stack_dir / "13_ms-values.yaml"

        if not helmfile_src.exists() or not ms_values.exists():
            continue

        # Read config once per stack - we need it both to decide
        # whether modelservice rendering applies and to extract the
        # model_id_label used by the helmfile selector.
        config_file = stack_dir / "config.yaml"
        cfg: dict = {}
        if config_file.exists():
            with open(config_file, encoding="utf-8") as f:
                cfg = _yaml.safe_load(f) or {}

        # Skip standalone-only stacks: the modelservice Helm chart is
        # not used, and running `helmfile template` against a helmfile
        # with no matching release yields an empty document that
        # subsequently fails to parse.
        modelservice_enabled = bool(
            (cfg.get("modelservice") or {}).get("enabled", False)
        )
        if not modelservice_enabled:
            logger.log_debug(
                f"Skipping Helm pre-render for {stack_dir.name}: "
                f"modelservice.enabled is false (standalone-only stack)"
            )
            continue

        model_id = cfg.get("model_id_label", "")
        if not model_id:
            logger.log_debug(
                f"Skipping Helm pre-render for {stack_dir.name}: "
                f"model_id_label not found in config.yaml"
            )
            continue

        # Output directory for pre-rendered Helm manifests
        helm_dir = stack_dir / "helm"
        helm_dir.mkdir(parents=True, exist_ok=True)

        # helmfile expects values files with specific names relative to
        # the helmfile location.  Use the helm dir as the working
        # directory and copy the values files with expected names.
        shutil.copy2(helmfile_src, helm_dir / "helmfile.yaml")

        # Only the modelservice values file is needed - the selector
        # targets only the -ms release so infra/gaie values are not read.
        shutil.copy2(ms_values, helm_dir / "ms-values.yaml")

        # Use CommandExecutor for consistent logging and error handling
        cmd = CommandExecutor(
            work_dir=plan_dir,
            dry_run=False,
            verbose=False,
            logger=logger,
        )
        result = cmd.helmfile(
            "--selector", f"name={model_id}-ms",
            "template",
            "-f", str(helm_dir / "helmfile.yaml"),
            "--skip-schema-validation",
            use_kubeconfig=False,
        )

        if result.success and result.stdout.strip():
            output_path = helm_dir / "modelservice.yaml"
            output_path.write_text(result.stdout, encoding="utf-8")
            line_count = len(result.stdout.splitlines())
            logger.log_info(
                f"📄 Pre-rendered modelservice Helm manifests "
                f"({line_count} lines) \u2192 {stack_dir.name}/helm/modelservice.yaml"
            )
        elif not result.success:
            logger.log_debug(
                f"Could not pre-render modelservice manifests for "
                f"{stack_dir.name}: {result.stderr[:200]}"
            )


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
                "fma_enabled": (
                    plan_config.get("fma", {}).get("enabled", False)
                ),
                "modelservice_enabled": (
                    plan_config.get("modelservice", {}).get("enabled", False)
                ),
                "harness": plan_config.get("harness", {}),
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
    hf_token, and release -- or an empty dict if no config is found.
    """
    all_info = _load_all_stacks_info(rendered_paths)
    return all_info[0] if all_info else {}


def _parse_namespaces(
    ns_str: str | None, plan_info: dict
) -> tuple[str | None, str | None]:
    """Parse the ``--namespace`` CLI value into (namespace, harness_namespace).

    Supports two formats:
    - ``"ns"`` -- both namespaces use the same value.
    - ``"ns,harness_ns"`` -- first is the infra namespace, second is the
      harness namespace.

    Falls back to ``plan_info`` if *ns_str* is ``None``.

    Returns:
        (namespace, harness_namespace).  Either may be ``None`` if
        no value was provided anywhere.
    """
    cli_namespace = None
    cli_harness_namespace = None
    if ns_str:
        parts = [p.strip() for p in ns_str.split(",")]
        cli_namespace = parts[0]
        cli_harness_namespace = parts[1] if len(parts) > 1 else parts[0]

    namespace = cli_namespace or plan_info.get("namespace")
    harness_ns = (
        cli_harness_namespace or plan_info.get("harness_namespace") or namespace
    )
    return namespace, harness_ns


def _resolve_deploy_methods(args, plan_info, logger, phase="standup"):
    """Determine deployment methods from CLI flag or plan config.

    Priority: CLI --methods > auto-detect from plan config > phase-specific default.
    standalone.enabled defaults to false, so if true the scenario explicitly chose it.
    For teardown, no fallback -- user must specify --methods if config is missing.
    """
    methods_str = getattr(args, "methods", None)
    if methods_str:
        return [m.strip() for m in methods_str.split(",")]

    standalone = plan_info.get("standalone_enabled", False)
    fma = plan_info.get("fma_enabled", False)
    modelservice = plan_info.get("modelservice_enabled", False)

    if phase == "run":
        # Run phase returns all enabled methods for endpoint detection
        methods = []
        if standalone:
            methods.append("standalone")
        if fma:
            methods.append("fma")
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
        if fma:
            logger.log_info("Auto-detected deploy method from plan: fma")
            return ["fma"]
        if modelservice:
            logger.log_info("Auto-detected deploy method from plan: modelservice")
            return ["modelservice"]

    if phase == "teardown":
        raise PhaseError(
            "Cannot determine deployment method: no plan config found and "
            "--methods not specified. Use --methods standalone or "
            "--methods modelservice to specify what to tear down."
        )

    return ["modelservice"]


def _do_standup(args, logger, render_plan_errors):
    """Core standup logic. Returns (context, result). Raises PhaseError on failure."""
    rendered_paths = getattr(render_plan_errors, "rendered_paths", [])
    all_stacks_info = _load_all_stacks_info(rendered_paths)
    plan_info = all_stacks_info[0] if all_stacks_info else {}
    deployed_methods = _resolve_deploy_methods(args, plan_info, logger)

    namespace, harness_ns = _parse_namespaces(
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
        standalone_deploy_timeout=int(getattr(args, "standalone_deploy_timeout", 900) or 900),
        gateway_deploy_timeout=int(getattr(args, "gateway_deploy_timeout", 120) or 120),
        modelservice_deploy_timeout=int(getattr(args, "modelservice_deploy_timeout", 1500) or 1500),
        pvc_bind_timeout=int(getattr(args, "pvc_bind_timeout", 240) or 240),
        stack_filter=_parse_stack_filter(getattr(args, "stack", None)),
    )

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
        raise PhaseError(f"Standup failed:\n{result.summary()}")

    return context, result


def _execute_standup(args, logger, render_plan_errors):
    """Build execution context and run standup steps."""
    try:
        context, result = _do_standup(args, logger, render_plan_errors)
    except PhaseError as e:
        logger.log_error(str(e))
        sys.exit(1)

    _print_standup_summary(context, result, logger)

    # Auto-chain smoketest after standup unless --skip-smoketest
    skip_smoketest = getattr(args, "skip_smoketest", False)
    if not skip_smoketest:
        logger.log_info("")
        logger.log_info(
            "Running smoketests...",
            emoji="🔍",
        )
        try:
            _do_smoketest(args, logger, render_plan_errors)
        except PhaseError as e:
            logger.log_error(str(e))
            sys.exit(1)


def _do_smoketest(args, logger, render_plan_errors):
    """Core smoketest logic. Returns (context, result). Raises PhaseError on failure."""
    rendered_paths = getattr(render_plan_errors, "rendered_paths", [])
    all_stacks_info = _load_all_stacks_info(rendered_paths)
    plan_info = all_stacks_info[0] if all_stacks_info else {}
    deployed_methods = _resolve_deploy_methods(args, plan_info, logger, phase="smoketest")

    namespace, harness_ns = _parse_namespaces(
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
        stack_filter=_parse_stack_filter(getattr(args, "stack", None)),
    )

    # Smoketest runs per-stack checks sequentially (max_parallel_stacks=1):
    # parallel runs would interleave /health + /v1/models probe logs across
    # stacks, hammer the shared gateway with concurrent curls, and make
    # multi-stack failures harder to debug. Standup/run stay at the
    # user-configured default via --parallel; smoketest overrides.
    executor = StepExecutor(
        steps=get_smoketest_steps(),
        context=context,
        logger=logger,
        max_parallel_stacks=1,
    )

    step_spec = getattr(args, "step", None)
    result = executor.execute(step_spec=step_spec)

    if result.has_errors:
        raise PhaseError(f"Smoketest failed:\n{result.summary()}")

    logger.log_info("All smoketest steps complete.", emoji="✅")
    return context, result


def _execute_smoketest(args, logger, render_plan_errors):
    """Build execution context and run smoketest steps."""
    try:
        _do_smoketest(args, logger, render_plan_errors)
    except PhaseError as e:
        logger.log_error(str(e))
        sys.exit(1)


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


def _print_standup_summary(context, result, logger):
    """Print the standup completion banner with namespace, method, and endpoint info."""
    logger.line_break()

    ns = context.namespace or "unknown"
    harness_ns = context.harness_namespace or ns
    username = context.username or "unknown"
    platform = context.platform_type
    methods = (
        ", ".join(context.deployed_methods) if context.deployed_methods else "default"
    )
    stacks = len(context.rendered_stacks)
    mode = "dry-run" if context.dry_run else "live"
    endpoints = context.deployed_endpoints or {}

    # Aggregate per-stack models for the multi-stack case. context.model_name
    # holds only a single value (first stack / CLI override) and would
    # misrepresent the deployment otherwise. Honors --stack filter.
    stack_models = _collect_stack_models(context)

    W = 62
    logger.log_info("=" * W)
    logger.log_info(f"  STANDUP COMPLETE")
    logger.log_info("=" * W)
    logger.log_info(f"  User:       {username}")
    logger.log_info(f"  Platform:   {platform}")
    logger.log_info(f"  Mode:       {mode}")
    if len(stack_models) > 1:
        logger.log_info(f"  Models:     {len(stack_models)} (one per stack)")
        for stack_name, model in stack_models:
            logger.log_info(f"    - {stack_name.ljust(20)} {model}")
    else:
        single_model = (
            stack_models[0][1] if stack_models else (context.model_name or "unknown")
        )
        logger.log_info(f"  Model:      {single_model}")
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
        logger.log_info("-" * W)
        logger.log_info(f"  Deployed Endpoints:")
        for name, url in endpoints.items():
            logger.log_info(f"    {name}: {url}")

    logger.log_info("=" * W)
    logger.line_break()
    logger.log_info(f"Workspace: {context.workspace}")
    logger.log_info("All standup steps complete.", emoji="✅")


def _do_teardown(args, logger, render_plan_errors):
    """Core teardown logic. Returns (context, result). Raises PhaseError on failure."""
    rendered_paths = getattr(render_plan_errors, "rendered_paths", [])
    plan_info = _load_plan_info(rendered_paths)
    deployed_methods = _resolve_deploy_methods(
        args, plan_info, logger, phase="teardown"
    )

    namespace, harness_ns = _parse_namespaces(
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
        fma_teardown_timeout=int(getattr(args, "fma_teardown_timeout", 120) or 120),
        stack_filter=_parse_stack_filter(getattr(args, "stack", None)),
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


def _execute_teardown(args, logger, render_plan_errors):
    """Build execution context and run teardown steps."""
    try:
        context, result = _do_teardown(args, logger, render_plan_errors)
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


def _do_run(args, logger, render_plan_errors, experiment_file_override=None):
    """Core run logic. Returns (context, result). Raises PhaseError on failure."""
    rendered_paths = getattr(render_plan_errors, "rendered_paths", [])
    all_stacks_info = _load_all_stacks_info(rendered_paths)
    plan_info = all_stacks_info[0] if all_stacks_info else {}

    deployed_methods = _resolve_deploy_methods(args, plan_info, logger, phase="run")

    namespace, harness_ns = _parse_namespaces(
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
        harness_wait_timeout=int(
            getattr(args, "wait_timeout", None)
            if getattr(args, "wait_timeout", None) is not None
            else (plan_info.get("harness", {}) or {}).get("waitTimeout")
            or 3600
        ),
        harness_debug=getattr(args, "debug", False),
        harness_skip_run=getattr(args, "skip", False),
        harness_service_account=getattr(args, "serviceaccount", None),
        harness_envvars_to_pod=getattr(args, "envvarspod", None),
        analyze_locally=getattr(args, "analyze", False),
        endpoint_url=endpoint_url,
        run_config_file=run_config_file,
        generate_config_only=getattr(args, "generate_config", False),
        dataset_url=getattr(args, "dataset", None),
        harness_data_access_timeout=int(getattr(args, "data_access_timeout", 120) or 120),
        stack_filter=_parse_stack_filter(getattr(args, "stack", None)),
    )

    # --list-endpoints: detect endpoints (step 03 only), print a copy-paste
    # table with per-stack routing URLs, and exit without deploying any
    # harness pods. Useful for discovering what's live in a multi-stack
    # scenario before picking an endpoint for a targeted `run`.
    if getattr(args, "list_endpoints", False):
        executor = StepExecutor(
            steps=get_run_steps(),
            context=context,
            logger=logger,
            max_parallel_stacks=1,
        )
        result = executor.execute(step_spec="3")
        _print_endpoints_table(context, logger, args)
        return context, result

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


def _collect_stack_models(context) -> list[tuple[str, str]]:
    """Return ``[(stack_name, model_name), ...]`` from rendered configs.

    Honors the ``--stack`` filter so the benchmark summary reflects only
    the stacks that actually ran. Returns an empty list when there are
    no rendered stacks (run-only / endpoint-url mode), in which case the
    summary falls back to ``context.model_name``.
    """
    rendered = getattr(context, "rendered_stacks", []) or []
    if not rendered:
        return []
    stack_filter = getattr(context, "stack_filter", None) or []
    rows: list[tuple[str, str]] = []
    for stack_path in rendered:
        stack_name = stack_path.name
        if stack_filter and stack_name not in stack_filter:
            continue
        cfg_file = stack_path / "config.yaml"
        model_name = "?"
        if cfg_file.exists():
            try:
                with open(cfg_file, encoding="utf-8") as fh:
                    cfg = _yaml.safe_load(fh) or {}
                model_name = (cfg.get("model") or {}).get("name", "?") or "?"
            except (OSError, _yaml.YAMLError):
                pass
        rows.append((stack_name, model_name))
    return rows


def _parse_stack_filter(raw: str | None) -> list[str] | None:
    """Parse --stack / LLMDBENCH_STACK into a list of stack names, or None."""
    if not raw:
        return None
    names = [n.strip() for n in str(raw).split(",") if n.strip()]
    return names or None


def _print_endpoints_table(context, logger, args) -> None:
    """Print a table of per-stack endpoints + copy-paste `run` commands.

    Called by --list-endpoints after step 03 has populated
    context.deployed_endpoints. Output is human-readable AND machine-
    friendly: the copy-paste block can be pasted as-is to benchmark a
    specific pool.
    """
    endpoints = context.deployed_endpoints or {}
    if not endpoints:
        logger.log_warning(
            "No endpoints detected. Have you run standup first? "
            "(`llmdbenchmark standup -p <namespace>` on this spec)"
        )
        return

    rows: list[tuple[str, str, str]] = []
    for stack_path in context.rendered_stacks or []:
        stack_name = stack_path.name
        cfg_file = stack_path / "config.yaml"
        model_name = "?"
        if cfg_file.exists():
            try:
                with open(cfg_file, encoding="utf-8") as fh:
                    cfg = _yaml.safe_load(fh) or {}
                model_name = (cfg.get("model") or {}).get("name", "?") or "?"
            except (OSError, _yaml.YAMLError):
                pass
        url = endpoints.get(stack_name, "<not detected>")
        rows.append((stack_name, model_name, url))

    # Pretty table
    col_stack = max(len("STACK"), max(len(r[0]) for r in rows))
    col_model = max(len("MODEL"), max(len(r[1]) for r in rows))
    col_url = max(len("ENDPOINT URL"), max(len(r[2]) for r in rows))

    logger.line_break()
    logger.log_info("📋 Detected endpoints:")
    logger.log_info(
        f"  {'STACK'.ljust(col_stack)}  "
        f"{'MODEL'.ljust(col_model)}  {'ENDPOINT URL'.ljust(col_url)}"
    )
    logger.log_info(
        f"  {'-' * col_stack}  {'-' * col_model}  {'-' * col_url}"
    )
    for stack_name, model_name, url in rows:
        logger.log_info(
            f"  {stack_name.ljust(col_stack)}  "
            f"{model_name.ljust(col_model)}  {url.ljust(col_url)}"
        )
    logger.line_break()

    # Copy-paste block - one ready-to-run invocation per stack, with the
    # flags the user most likely wants to customize (harness, workload,
    # parallelism) left as placeholders.
    spec_raw = getattr(args, "specification_file", None)
    spec = str(spec_raw) if spec_raw else "<spec>"
    if "/" in spec or spec.endswith(".yaml.j2"):
        # Full path (e.g. /abs/path/config/specification/guides/multi-model-wva.yaml.j2)
        # - trim to the friendly `category/name` form the CLI understands.
        parent = os.path.basename(os.path.dirname(spec)) if "/" in spec else ""
        stem = os.path.basename(spec)
        if stem.endswith(".yaml.j2"):
            stem = stem[:-len(".yaml.j2")]
        spec = f"{parent}/{stem}" if parent else stem
    namespace = context.namespace or "<namespace>"
    logger.log_info("💡 Copy-paste to benchmark one pool:")
    logger.line_break()
    # log_plain writes to every logger handler (terminal + attached log
    # files) without the timestamp / level prefix, so the block both
    # copy-pastes cleanly from the terminal AND lands verbatim in the
    # log file for later auditing.
    for stack_name, model_name, url in rows:
        logger.log_plain(f"  # {stack_name} - {model_name}")
        logger.log_plain(f"  llmdbenchmark --spec {spec} run \\")
        logger.log_plain(f"    --namespace {namespace} \\")
        logger.log_plain(f"    --endpoint-url {url} \\")
        logger.log_plain(f"    --model {model_name} \\")
        logger.log_plain("    -l <harness> -w <workload.yaml> -j <parallel-pods>")
        logger.log_plain("")


def _execute_run(args, logger, render_plan_errors):
    """Build execution context and run experiment steps."""
    try:
        context, result = _do_run(args, logger, render_plan_errors)
    except PhaseError as e:
        logger.log_error(str(e))
        sys.exit(1)

    # --list-endpoints short-circuits the run - no harness pods launched,
    # no results collected, no ConfigMap stored. Skip the benchmark
    # summary banner entirely; the endpoints table was the whole output.
    if getattr(args, "list_endpoints", False):
        return

    endpoint_url = getattr(args, "endpoint_url", None)
    run_config_file = getattr(args, "run_config", None)
    is_run_only = bool(endpoint_url or run_config_file)
    mode = "run-only" if is_run_only else "full"
    if context.generate_config_only:
        mode = "generate-config"
    harness = context.harness_name or "inference-perf"

    # --- Summary banner ---
    results_dir = context.run_results_dir()
    namespace = context.harness_namespace or context.namespace or "unknown"
    workload = context.harness_profile or "unknown"
    experiment_ids = getattr(context, "experiment_ids", [])
    parallelism = context.harness_parallelism or 1

    # Multi-stack scenarios benchmark more than one model simultaneously;
    # context.model_name holds only a single value (from the first stack
    # or a CLI override). Aggregate per-stack model names from the
    # rendered configs so the banner reflects reality.
    stack_models = _collect_stack_models(context)

    logger.line_break()
    logger.log_info("=" * 60)
    logger.log_info("BENCHMARK RUN SUMMARY")
    logger.log_info("=" * 60)
    logger.log_info(f"  Harness:       {harness}")
    logger.log_info(f"  Workload:      {workload}")
    if len(stack_models) > 1:
        logger.log_info(f"  Models:        {len(stack_models)} (one per stack)")
        for stack_name, model in stack_models:
            logger.log_info(f"    - {stack_name.ljust(20)} {model}")
    else:
        single_model = (
            stack_models[0][1] if stack_models else (context.model_name or "unknown")
        )
        logger.log_info(f"  Model:         {single_model}")
    logger.log_info(f"  Namespace:     {namespace}")
    logger.log_info(f"  Mode:          {mode}")
    logger.log_info(f"  Parallelism:   {parallelism}")
    if experiment_ids:
        logger.log_info(f"  Treatments:    {len(experiment_ids)}")
        for eid in experiment_ids:
            logger.log_info(f"    - {eid}")
            # Show per-parallelism result dirs
            for i in range(1, parallelism + 1):
                local_path = results_dir / f"{eid}_{i}"
                if local_path.exists():
                    file_count = sum(1 for f in local_path.rglob("*") if f.is_file())
                    logger.log_info(f"      [{i}/{parallelism}] {local_path.name} ({file_count} files)")
    kube_bin = "oc" if context.is_openshift else "kubectl"
    logger.log_info(f"  Local results: {results_dir}")
    logger.log_info(
        f"  PVC results:   {kube_bin} exec -n {namespace} "
        f"$({kube_bin} get pod -n {namespace} -l role=llm-d-benchmark-data-access "
        f"-o jsonpath='{{.items[0].metadata.name}}') -- ls /requests/"
    )
    logger.log_info("=" * 60)
    logger.log_info(
        f"Run complete (mode={mode}, harness={harness}).",
        emoji="✅",
    )

    # --- Store run parameters as ConfigMap in namespace ---
    if not context.dry_run:
        _store_run_parameters_configmap(context, harness, workload, experiment_ids, logger)


def _store_run_parameters_configmap(context, harness, workload, experiment_ids, logger):
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

        import yaml as _yaml
        from datetime import datetime, timezone

        cm_name = "llm-d-benchmark-run-parameters"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        # Build PVC results paths from experiment IDs
        parallelism = context.harness_parallelism or 1
        results_dir_prefix = "/requests"
        pvc_paths = []
        for eid in (experiment_ids or []):
            for i in range(1, parallelism + 1):
                pvc_paths.append(f"{results_dir_prefix}/{eid}_{i}")

        import getpass
        import socket

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
        existing_data[run_key] = _yaml.dump(run_entry, default_flow_style=False)
        existing_data["latest"] = _yaml.dump(run_entry, default_flow_style=False)

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
        cm_path.write_text(_yaml.dump(cm, default_flow_style=False), encoding="utf-8")

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


def _render_plans_for_experiment(args, logger, setup_overrides=None):
    """Render plans with optional setup overrides. Raises PhaseError on failure."""
    specification_as_dict = RenderSpecification(
        specification_file=args.specification_file,
        base_dir=args.base_dir,
    ).eval()

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
        cli_monitoring=getattr(args, "monitoring", None),
        cli_wva=getattr(args, "wva", False),
        setup_overrides=setup_overrides,
    ).eval()

    if render_plan_errors.has_errors:
        error_dump = json.dumps(render_plan_errors.to_dict(), indent=2)
        raise PhaseError(f"Rendering failed with setup overrides:\n{error_dump}")

    return render_plan_errors


def _execute_experiment(args, logger):
    """Orchestrate a full DoE experiment: setup x run treatment matrix."""
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

    W = 62
    logger.log_info("=" * W)
    logger.log_info("  DoE EXPERIMENT")
    logger.log_info("=" * W)
    logger.log_info(f"  Name:             {experiment_plan.name}")
    logger.log_info(f"  Setup treatments: {total_setup}")
    logger.log_info(f"  Run treatments:   {total_run}")
    logger.log_info(f"  Total matrix:     {experiment_plan.total_matrix}")
    if experiment_plan.harness:
        logger.log_info(f"  Harness:          {experiment_plan.harness}")
    if experiment_plan.profile:
        logger.log_info(f"  Profile:          {experiment_plan.profile}")
    logger.log_info(f"  Continue on error: {not stop_on_error}")
    logger.log_info(f"  Skip teardown:    {skip_teardown}")
    logger.log_info("=" * W)
    logger.line_break()

    base_workspace = config.workspace
    base_plan_dir = config.plan_dir

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
            render_plan_errors = _render_plans_for_experiment(
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
            standup_context, standup_result = _do_standup(
                args, logger, render_plan_errors
            )
            logger.log_info(f"Standup complete for {treatment_name}", emoji="✅")
        except PhaseError as e:
            error_msg = str(e)
            logger.log_error(f"Standup failed for {treatment_name}: {error_msg}")
            # Attempt teardown to clean up any partially deployed resources
            if not skip_teardown:
                try:
                    _do_teardown(args, logger, render_plan_errors)
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
            _do_smoketest(args, logger, render_plan_errors)
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
                    _do_teardown(args, logger, render_plan_errors)
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
            run_context, run_result = _do_run(
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
                _do_teardown(args, logger, render_plan_errors)
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

    config.workspace = base_workspace
    config.plan_dir = base_plan_dir

    summary_path = Path(base_workspace) / "experiment-summary.yaml"
    summary.write(summary_path)
    logger.log_info(f"Experiment summary written to {summary_path}", emoji="📊")
    logger.line_break()
    summary.print_table(logger)


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
        "LLMDBENCH_DATA_ACCESS_TIMEOUT": ("data_access_timeout", "--data-access-timeout"),
        "LLMDBENCH_STANDALONE_DEPLOY_TIMEOUT": ("standalone_deploy_timeout", "--standalone-deploy-timeout"),
        "LLMDBENCH_GATEWAY_DEPLOY_TIMEOUT": ("gateway_deploy_timeout", "--gateway-deploy-timeout"),
        "LLMDBENCH_MODELSERVICE_DEPLOY_TIMEOUT": ("modelservice_deploy_timeout", "--modelservice-deploy-timeout"),
        "LLMDBENCH_PVC_BIND_TIMEOUT": ("pvc_bind_timeout", "--pvc-bind-timeout"),
        "LLMDBENCH_FMA_TEARDOWN_TIMEOUT": ("fma_teardown_timeout", "--fma-teardown-timeout"),
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
    if hasattr(args, "monitoring") and args.monitoring is None:
        args.monitoring = env_bool("LLMDBENCH_MONITORING") or None
    if hasattr(args, "deep") and not args.deep:
        args.deep = env_bool("LLMDBENCH_DEEP_CLEAN")
    if hasattr(args, "skip") and not args.skip:
        args.skip = env_bool("LLMDBENCH_SKIP")
    if hasattr(args, "debug") and not args.debug:
        args.debug = env_bool("LLMDBENCH_DEBUG")
    if hasattr(args, "wva") and not args.wva:
        args.wva = env_bool("LLMDBENCH_WVA")

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
