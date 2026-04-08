"""Shared helpers and the PhaseError exception used across all phase modules.

This module holds:
    - ``PhaseError``: raised by any phase on lifecycle failure.
    - Stack info loading helpers: ``load_stack_info_from_config``,
      ``load_all_stacks_info``, ``load_plan_info``.
    - CLI parsing helpers: ``parse_namespaces``, ``resolve_deploy_methods``.
    - ``render_plans_for_experiment``: render plans with setup overrides for
      the DoE experiment orchestrator.

Phase modules must import from this module rather than duplicating logic.
``phases/common.py`` must NOT import from any other ``phases/*`` module or
from ``llmdbenchmark.cli`` (both would create import cycles).
"""

import json

import yaml as _yaml

from llmdbenchmark.config import config
from llmdbenchmark.parser.render_specification import RenderSpecification
from llmdbenchmark.parser.render_plans import RenderPlans
from llmdbenchmark.parser.version_resolver import VersionResolver
from llmdbenchmark.parser.cluster_resource_resolver import ClusterResourceResolver


class PhaseError(Exception):
    """Raised when a lifecycle phase (standup/run/teardown) fails."""

    pass


def load_stack_info_from_config(config_file, stack_name=""):
    """Parse a single stack's config.yaml into a plan-info dict."""
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


def load_all_stacks_info(rendered_paths):
    """Read configuration from every rendered stack's config.yaml.

    Returns a list of per-stack info dicts (one per rendered path that
    has a valid config.yaml).
    """
    stacks_info = []
    for stack_path in rendered_paths or []:
        config_file = stack_path / "config.yaml"
        if config_file.exists():
            info = load_stack_info_from_config(config_file, stack_name=stack_path.name)
            if info:
                stacks_info.append(info)
    return stacks_info


def load_plan_info(rendered_paths):
    """Read key configuration from the first rendered plan config.yaml.

    Returns a dict with namespace, harness_namespace, model_name,
    hf_token, and release -- or an empty dict if no config is found.
    """
    all_info = load_all_stacks_info(rendered_paths)
    return all_info[0] if all_info else {}


def parse_namespaces(
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


def resolve_deploy_methods(args, plan_info, logger, phase="standup"):
    """Determine deployment methods from CLI flag or plan config.

    Priority: CLI --methods > auto-detect from plan config > phase-specific default.
    standalone.enabled defaults to false, so if true the scenario explicitly chose it.
    For teardown, no fallback -- user must specify --methods if config is missing.
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
        raise PhaseError(
            "Cannot determine deployment method: no plan config found and "
            "--methods not specified. Use --methods standalone or "
            "--methods modelservice to specify what to tear down."
        )

    return ["modelservice"]


def render_plans_for_experiment(args, logger, setup_overrides=None):
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
        cli_monitoring=getattr(args, "monitoring", False),
        setup_overrides=setup_overrides,
    ).eval()

    if render_plan_errors.has_errors:
        error_dump = json.dumps(render_plan_errors.to_dict(), indent=2)
        raise PhaseError(f"Rendering failed with setup overrides:\n{error_dump}")

    return render_plan_errors
