"""Deciding what an ``update`` runs, and checking the stack is up first."""

import sys

from llmdbenchmark.executor.step_executor import StepExecutor
from llmdbenchmark.parser.cli_overrides import GLOBAL_SELECTOR
from llmdbenchmark.update import (
    COMPONENT_STEPS,
    DANGEROUS_COMPONENTS,
    KEY_COMPONENTS,
    RELEASELESS_METHODS,
    KeyClass,
    classify_overrides,
    components_to_steps,
    dangerous_steps,
    flag_entry,
    flag_name,
    parse_components,
    prune_components,
)
from llmdbenchmark.utilities.kube_helpers import list_helm_releases
from llmdbenchmark.utilities.standup_parameters import stack_names

#: Unlike teardown, uninstalled and superseded are left out: a release kept
#: in history is not up. uninstalling stays in, so a broken teardown shows as
#: a stuck release and not as a stack never stood up.
_LIVE_RELEASE_STATUSES = ["deployed", "failed", "pending", "uninstalling"]


def _exit(logger, *lines: str) -> None:
    for line in lines:
        logger.log_error(line)
    sys.exit(1)


def resolve_scope(args, logger) -> set[str]:
    """Decide which components -- and so which steps -- this update touches.

    Exits non-zero when there is nothing to change, on an unknown top-level
    key, on a stack-scoped ``--set`` for a stack ``--stack`` leaves out, and
    on a change that cannot be applied in place without ``--force``.
    """
    by_selector = getattr(args, "user_set_overrides_by_stack", None) or {}
    flags = list(getattr(args, "changed_flags", None) or [])
    raw_component = getattr(args, "component", None)

    if not (by_selector or flags or raw_component or getattr(args, "step", None)):
        _exit(
            logger,
            "update needs something to change: pass --set 'dotted.key=value', "
            "a flag that differs from the original standup, --component or "
            "-s. To re-apply an unchanged scenario in full, use `standup` -- "
            "it is idempotent.",
        )

    stacks = stack_names(getattr(args, "stack", None))
    outside = sorted(
        selector
        for selector in by_selector
        if stacks and selector != GLOBAL_SELECTOR and selector not in stacks
    )
    if outside:
        _exit(
            logger,
            f"--set targets stack(s) {', '.join(outside)}, which --stack leaves "
            "out, so it would not be applied. Add them to --stack, or drop it.",
        )

    components, infra, dangerous, noop, unknown = classify_overrides(by_selector)
    if unknown:
        _exit(
            logger,
            "update cannot tell which component owns these override "
            f"key(s): {', '.join(sorted(unknown))}. Overrides target the "
            "TOP-LEVEL config path (e.g. router.epp.replicas, not "
            "modelservice.router.epp.replicas). Known top-level keys: "
            f"{', '.join(sorted(KEY_COMPONENTS))}.",
        )

    noop = [(key, KEY_COMPONENTS[key][2]) for key in noop]

    # Who brought each component in, to explain a refusal.
    sources: dict[str, list[str]] = {}
    for overrides in by_selector.values():
        for key in overrides:
            for component in KEY_COMPONENTS[key][1]:
                sources.setdefault(component, []).append(key)

    for dest in flags:
        key_class, flag_components, why = flag_entry(dest)
        name = flag_name(dest)
        components |= flag_components
        for component in flag_components:
            sources.setdefault(component, []).append(name)
        if key_class is KeyClass.INFRA:
            infra.append((name, why))
        elif key_class is KeyClass.DANGEROUS:
            dangerous.append((name, why))
        elif key_class is KeyClass.NOOP:
            noop.append((name, why))

    if raw_component:
        requested, bad = parse_components(raw_component)
        if bad:
            _exit(
                logger,
                f"unknown --component value(s): {', '.join(bad)}. Known: "
                f"{', '.join(sorted(COMPONENT_STEPS))}.",
            )
        for component in requested:
            sources.setdefault(component, []).append("--component")
        components |= requested

    # Gated on where a change lands, not on which key named it, so a
    # warn-only key cannot reach a dangerous component without --force.
    reached = sorted(components & set(DANGEROUS_COMPONENTS))
    if (dangerous or reached) and not getattr(args, "force", False):
        lines = ["these change(s) cannot be applied to a live stack in place:"]
        lines += [f"  {key}: {why}" for key, why in dangerous]
        lines += [
            f"  {component} (via {', '.join(dict.fromkeys(sources[component]))}): "
            f"{DANGEROUS_COMPONENTS[component]}"
            for component in reached
        ]
        lines.append(
            "Run `teardown` then `standup` for a clean deployment, or pass "
            "--force to update anyway (the stack may then differ from what a "
            "clean standup would produce)."
        )
        _exit(logger, *lines)

    for key, why in infra:
        logger.log_warning(f"{key} is shared infrastructure -- updating it {why}.")

    for name, why in noop:
        logger.log_warning(
            f"{name} does not affect any deployed component ({why}), so "
            "nothing is restarted for it."
        )

    if dangerous or reached:
        logger.log_warning(
            "--force: applying change(s) that a clean standup would handle "
            "differently -- " + ", ".join([key for key, _ in dangerous] + reached)
        )

    return components


def step_spec(args, context, components, logger) -> str:
    """The standup steps to run, from the components or from ``-s``.

    Exits non-zero when ``-s`` reaches a dangerous component without ``--force``.
    """
    methods = context.deployed_methods or []
    override = getattr(args, "step", None)
    if override:
        risky = set(StepExecutor.parse_step_list(override)) & dangerous_steps(methods)
        if risky and not getattr(args, "force", False):
            names = sorted(
                component
                for component in DANGEROUS_COMPONENTS
                if components_to_steps({component}, deployed_methods=methods) & risky
            )
            _exit(
                logger,
                f"-s {override} runs step(s) {', '.join(map(str, sorted(risky)))}, "
                "which cannot be applied to a live stack in place:",
                *[f"  {name}: {DANGEROUS_COMPONENTS[name]}" for name in names],
                "Pass --force to run them anyway.",
            )
        logger.log_info(
            f"-s/--step {override} overrides the steps inferred from the change.",
            emoji="\U0001f527",
        )
        return override

    steps = components_to_steps(components, deployed_methods=methods)
    return ",".join(str(step) for step in sorted(steps))


def scope_summary(args, context, components, spec: str) -> str:
    """One line saying what this update re-applies."""
    if getattr(args, "step", None):
        return f"Updating standup step(s) {spec}"
    in_scope = sorted(
        prune_components(components, deployed_methods=context.deployed_methods)
    )
    return f"Updating component(s) {', '.join(in_scope)} via standup step(s) {spec}"


def _workload_probe(methods: set[str], info: dict) -> list[str] | None:
    """``kubectl get`` arguments that find a release-less stack's workload."""
    label = info.get("model_id_label")
    if "standalone" in methods and label:
        return ["deployment", f"vllm-standalone-{label}"]
    if "fma" in methods and label:
        return ["deployment", "-l", f"app=fma-requester-{label}"]
    guide = info.get("kustomize_guide")
    if "kustomize" in methods and guide:
        return ["pods", "-l", f"llm-d.ai/guide={guide.split('/')[-1]}"]
    return None


def missing_stacks(context, stacks_info: list[dict], logger) -> list[str]:
    """The stacks in scope that were never stood up.

    Checked per stack, not per namespace: sibling stacks share a namespace
    but not their releases, so "something is deployed here" would let the
    update half-create a missing stack -- its steps in scope would install it
    while the steps out of scope (namespace, PVCs, weights) stay missing.
    """
    if context.dry_run or context.container_only:
        return []

    # Normally done by step 00, which an update's scope may leave out.
    context.resolve_cluster()

    cmd = context.require_cmd()
    default_namespace = context.require_namespace()
    methods = set(context.deployed_methods or [])
    releases_by_namespace: dict[str, set[str] | None] = {}
    missing = []

    for info in stacks_info:
        stack_name = info.get("stack_name", "")
        if context.stack_filter and stack_name not in context.stack_filter:
            continue
        namespace = info.get("namespace") or default_namespace
        label = info.get("model_id_label")

        if "modelservice" in methods:
            if not label:
                continue
            if namespace not in releases_by_namespace:
                releases = list_helm_releases(cmd, namespace, _LIVE_RELEASE_STATUSES)
                if releases is None:
                    logger.log_warning(
                        f"Could not list helm releases in {namespace} -- "
                        "skipping the deployed-stack check there."
                    )
                releases_by_namespace[namespace] = (
                    None
                    if releases is None
                    else {entry.get("name", "") for entry in releases}
                )
            installed = releases_by_namespace[namespace]
            if installed is None or {f"{label}-ms", f"{label}-router"} & installed:
                continue

        probe = _workload_probe(methods & RELEASELESS_METHODS, info)
        if probe:
            result = cmd.kube(
                "get", *probe, "--namespace", namespace, "-o", "name", check=False
            )
            if result.success and result.stdout.strip():
                continue
        elif "modelservice" not in methods:
            continue

        missing.append(f"{stack_name} (namespace {namespace})")

    return missing


def warn_sibling_stacks(args, context, logger) -> None:
    """Warn when an unscoped change also restarts sibling stacks.

    A change with no stack selector applies to every stack sharing the
    namespace, so their pods roll too. Cheap to miss, expensive to find out
    from a graph.
    """
    in_scope = [
        path.name
        for path in context.rendered_stacks
        if not context.stack_filter or path.name in context.stack_filter
    ]
    if len(in_scope) < 2:
        return

    by_selector = getattr(args, "user_set_overrides_by_stack", None) or {}
    if GLOBAL_SELECTOR not in by_selector and not getattr(args, "changed_flags", None):
        return

    logger.log_warning(
        f"This update applies to {len(in_scope)} stacks sharing the namespace "
        f"({', '.join(in_scope)}): a change with no stack selector restarts "
        "all of them. Scope it with --stack NAME, or with "
        "--set 'NAME:key=value'."
    )
