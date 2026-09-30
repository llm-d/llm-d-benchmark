"""Records how a stack was stood up, in the cluster next to the stack itself.

The benchmark report reads the deploy metadata from a ConfigMap. The flags
of the original standup go to a Secret, because ``--set`` may carry a token;
``update`` reads them back so they do not have to be repeated on every later
change.
"""

import base64
import json
from pathlib import Path

import yaml

from llmdbenchmark.parser.cli_overrides import (
    OverrideParseError,
    parse_override_pair,
    split_override_pairs,
)

CONFIGMAP_NAME = "llm-d-benchmark-standup-parameters"
SECRET_NAME = "llm-d-benchmark-standup-invocation"

#: Secret key -> the argparse dest it was taken from. Only flags that
#: change what gets rendered belong here; a flag that only affects timeouts
#: or logging would be noise.
INVOCATION_FIELDS: dict[str, str] = {
    "invocation_set": "set_overrides",
    "invocation_spec": "specification_file",
    "invocation_models": "models",
    "invocation_methods": "methods",
    "invocation_gateway_class": "gateway_class",
    "invocation_affinity": "affinity",
    "invocation_annotations": "annotations",
    "invocation_release": "release",
    "invocation_wva": "wva",
    "invocation_epp_keda_saturation": "epp_keda_saturation",
    "invocation_no_pvc": "no_pvc",
    "invocation_full_infra": "full_infra",
}

#: The on/off flags above. The stored text alone cannot say it: a string
#: flag may hold the value "true".
BOOL_INVOCATION_DESTS: frozenset[str] = frozenset(
    {"wva", "epp_keda_saturation", "no_pvc", "full_infra"}
)

#: Flags where unset means "use the scenario", so False is stored too:
#: --no-monitoring is not the same as no flag.
TRISTATE_FIELDS: dict[str, str] = {
    "invocation_monitoring": "monitoring",
    "invocation_prism": "prism",
}

#: Not reused, only compared: flags taken with --stack describe those stacks only.
STACK_KEY = "invocation_stack"
CLUSTER_CONFIG_KEY = "invocation_cluster_config"

#: Upper bound on the Secret read, which also runs under --dry-run.
READ_TIMEOUT_SECONDS = 10


def stack_names(raw) -> frozenset[str] | None:
    """``--stack`` as a set of names, or None for every stack."""
    names = frozenset(
        name.strip() for name in str(raw or "").split(",") if name.strip()
    )
    return names or None


def invocation_params(args) -> dict[str, str]:
    """Flatten the render-affecting flags of this invocation.

    Not redacted: a redacted value would be re-applied as the literal string
    on the next update. This is why they are kept in a Secret.
    """
    params: dict[str, str] = {}
    for key, dest in INVOCATION_FIELDS.items():
        value = getattr(args, dest, None)
        if value is None or value is False or value == "" or value == []:
            continue
        if isinstance(value, (list, tuple)):
            # JSON, because a --set value may hold commas and newlines.
            value = json.dumps([str(item) for item in value])
        elif isinstance(value, bool):
            value = "true"
        params[key] = str(value)

    for key, dest in TRISTATE_FIELDS.items():
        value = getattr(args, dest, None)
        if value is not None:
            params[key] = "true" if value else "false"

    cluster_config = getattr(args, "cluster_config", None)
    if cluster_config:
        params[CLUSTER_CONFIG_KEY] = str(Path(cluster_config).resolve())

    stacks = stack_names(getattr(args, "stack", None))
    if stacks:
        params[STACK_KEY] = ",".join(sorted(stacks))
    return params


def write(cmd, context, params: dict[str, str]) -> bool:
    """Create or replace the ConfigMap, and the Secret with this invocation's flags."""
    harness_ns = context.harness_namespace or context.require_namespace()
    manifest = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": CONFIGMAP_NAME, "namespace": harness_ns},
        "data": {key: str(value) for key, value in params.items()},
    }
    if not _apply(cmd, context, manifest, "standup-parameters.yaml"):
        context.logger.log_warning(
            f"Could not write the {CONFIGMAP_NAME} ConfigMap -- the benchmark "
            "report cannot tell how this stack was stood up."
        )
        return False
    flags_written = write_invocation(cmd, context, context.invocation_params)

    if getattr(context, "dry_run", False):
        return flags_written

    context.logger.log_info(
        f"📋 Deployment metadata to configmap/{CONFIGMAP_NAME} in ns/{harness_ns}"
    )
    context.logger.log_info(
        f"   {cmd._kube_bin} get configmap {CONFIGMAP_NAME} -n {harness_ns} -o yaml"
    )
    return flags_written


def write_invocation(cmd, context, params: dict[str, str]) -> bool:
    """Create or replace the Secret with the flags a later update reuses."""
    harness_ns = context.harness_namespace or context.require_namespace()
    manifest = {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "Opaque",
        "metadata": {"name": SECRET_NAME, "namespace": harness_ns},
        # Not stringData: apply would not remove a key that is gone.
        "data": {
            key: base64.b64encode(str(value).encode()).decode()
            for key, value in params.items()
        },
    }
    if not _apply(cmd, context, manifest, "standup-invocation.yaml"):
        context.logger.log_warning(
            f"Could not write the {SECRET_NAME} Secret -- a later `update` "
            "cannot tell which flags this standup used."
        )
        return False
    return True


def _apply(cmd, context, manifest: dict, filename: str) -> bool:
    """Built here rather than with ``kubectl create --from-literal``: values
    may contain newlines, and CommandExecutor passes the command through a
    shell, where a newline cuts it."""
    yaml_path = context.setup_yamls_dir() / filename
    yaml_path.write_text(
        yaml.safe_dump(manifest, default_flow_style=False, sort_keys=True),
        encoding="utf-8",
    )
    return cmd.kube("apply", "-f", str(yaml_path)).success


def read(cmd, namespace: str) -> dict[str, str] | None:
    """Return the stored flags: empty when there are none, None when the
    read itself failed."""
    result = cmd.kube(
        "get",
        "secret",
        SECRET_NAME,
        "--namespace",
        namespace,
        "-o",
        "json",
        f"--request-timeout={READ_TIMEOUT_SECONDS}s",
        check=False,
        force=True,
        timeout=READ_TIMEOUT_SECONDS,
    )
    if not result.success:
        if "NotFound" in (result.stderr or ""):
            return {}
        return None
    if not (result.stdout or "").strip():
        return {}
    try:
        data = json.loads(result.stdout).get("data") or {}
        return {
            key: base64.b64decode(value, validate=True).decode()
            for key, value in data.items()
        }
    except (json.JSONDecodeError, AttributeError, ValueError):
        return None


def stored_set_values(data: dict[str, str]) -> list[str]:
    """The ``--set`` values a standup stored, one entry per ``--set``."""
    raw = data.get("invocation_set")
    if not raw:
        return []
    try:
        values = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [str(value) for value in values] if isinstance(values, list) else []


def _set_pairs(values: list[str], logger=None) -> list[tuple[str, str, object, str]]:
    """``(selector, key, value, text)`` for every pair in some ``--set`` values."""
    pairs = []
    for value in values:
        for pair in split_override_pairs(value):
            try:
                selector, key, parsed = parse_override_pair(pair)
            except OverrideParseError as exc:
                if logger is not None:
                    logger.log_warning(
                        f"Ignoring unparseable persisted --set pair {pair!r} "
                        f"from the {SECRET_NAME} Secret: {exc}. Pass it "
                        "again if it still applies."
                    )
                continue
            pairs.append((selector, key, parsed, pair))
    return pairs


def _typed_flags(args) -> dict[str, object]:
    """The flags given on this invocation, by argparse dest."""
    typed = {}
    for key, dest in list(INVOCATION_FIELDS.items()) + list(TRISTATE_FIELDS.items()):
        if key in ("invocation_set", "invocation_spec"):
            continue
        value = getattr(args, dest, None)
        if value is not None and value != "":
            typed[dest] = value
    return typed


def _flag_changed(dest: str, value, data: dict[str, str]) -> bool:
    """Whether a given flag differs from what the standup stored."""
    key = next(
        k for k, d in {**INVOCATION_FIELDS, **TRISTATE_FIELDS}.items() if d == dest
    )
    stored = data.get(key)
    if dest in BOOL_INVOCATION_DESTS:
        return bool(value) != (stored == "true")
    if dest in TRISTATE_FIELDS.values():
        return ("true" if value else "false") != stored
    return str(value) != stored


def reuse_invocation(args, cmd, logger) -> bool:
    """Merge the original standup's flags into this update's args.

    An update re-renders the whole plan, so a flag the standup passed and
    this invocation omits would silently revert that knob. Anything given
    here wins. Also records on *args* what this invocation really changes:
    ``changed_set_overrides`` and ``changed_flags``.

    Runs under --dry-run too: the read is a read-only `kubectl get`, and the
    plan to review must be the plan that runs.

    Returns False when the update must stop.
    """
    reuse = getattr(args, "reuse_invocation", True)
    typed_values = list(getattr(args, "set_overrides", None) or [])
    typed_pairs = _set_pairs(typed_values)
    typed_flags = _typed_flags(args)

    # Until the stored flags are known, everything given is a change.
    args.changed_set_overrides = [text for _, _, _, text in typed_pairs]
    args.changed_flags = sorted(typed_flags)
    args.record_invocation = True

    # The Secret is in the harness namespace, where the standup steps
    # wrote it -- a `-p infra,harness` stack keeps the two apart.
    parts = [
        part.strip() for part in str(getattr(args, "namespace", None) or "").split(",")
    ]
    namespace = parts[-1] if parts[-1] else None
    if not namespace:
        if reuse:
            logger.log_error(
                f"update needs -p/--namespace to read the {SECRET_NAME} "
                "Secret with the original standup's flags (or pass "
                "--no-reuse-invocation and repeat them all)."
            )
            return False
        return True

    data = read(cmd, namespace)
    if data is None:
        if reuse:
            logger.log_error(
                f"Could not read the {SECRET_NAME} Secret in {namespace}. "
                "Without it the original standup's flags would be reverted. "
                "Check the cluster connection, or pass --no-reuse-invocation."
            )
            return False
        return True

    if not data:
        if reuse:
            logger.log_warning(
                f"No {SECRET_NAME} Secret in {namespace}: cannot tell "
                "which flags the original standup used. Any flag it passed "
                "and this update omits will be reverted -- pass them again, "
                "or --no-reuse-invocation to silence this."
            )
        return True

    # Kept as is by the deploy steps when this update is not recorded.
    args.stored_invocation = {
        key: value for key, value in data.items() if key.startswith("invocation_")
    }

    stored_stacks = stack_names(data.get(STACK_KEY))
    stacks = stack_names(getattr(args, "stack", None))
    if stored_stacks is not None and not (stacks and stacks <= stored_stacks):
        logger.log_warning(
            f"The stored flags are for --stack {','.join(sorted(stored_stacks))} "
            "only, and this update also covers other stacks. They are not "
            "reused, and this update is not recorded: pass the flags again, "
            "or scope the update with --stack."
        )
        args.record_invocation = False
        return True
    if stacks != stored_stacks:
        # One set of flags cannot say "this change, for some stacks only".
        args.record_invocation = False
        logger.log_warning(
            "This update covers fewer stacks than the standup, so it is not "
            "recorded: a later update for all stacks would revert it. Use a "
            "stack-scoped --set 'NAME:key=value' without --stack to keep it."
        )

    stored_pairs = _set_pairs(stored_set_values(data), logger)
    stored_by_key = {(sel, key): value for sel, key, value, _ in stored_pairs}
    args.changed_set_overrides = [
        text
        for sel, key, value, text in typed_pairs
        if (sel, key) not in stored_by_key or stored_by_key[(sel, key)] != value
    ]
    args.changed_flags = sorted(
        dest for dest, value in typed_flags.items() if _flag_changed(dest, value, data)
    )

    stored_spec = data.get("invocation_spec")
    current_spec = getattr(args, "specification_file", None)
    if stored_spec and current_spec and str(current_spec) != stored_spec:
        logger.log_warning(
            f"This update renders {current_spec}, but the stack was stood up "
            f"from {stored_spec}. Anything that differs between the two is "
            "re-applied."
        )

    if not reuse:
        return True

    reused = []

    # A pair given again here replaces the stored one for the same selector
    # and key, and stored pairs go first: the later pair wins.
    typed_keys = {(sel, key) for sel, key, _, _ in typed_pairs}
    kept = [text for sel, key, _, text in stored_pairs if (sel, key) not in typed_keys]
    if kept:
        args.set_overrides = kept + typed_values
        reused.append(f"--set ({len(kept)} pair(s))")

    for key, dest in INVOCATION_FIELDS.items():
        if key in ("invocation_set", "invocation_spec") or key not in data:
            continue
        if dest in typed_flags:
            continue
        value = data[key]
        setattr(args, dest, value == "true" if dest in BOOL_INVOCATION_DESTS else value)
        reused.append(f"{dest}={value}")

    for key, dest in TRISTATE_FIELDS.items():
        if key in data and dest not in typed_flags:
            setattr(args, dest, data[key] == "true")
            reused.append(f"{dest}={data[key]}")

    path = data.get(CLUSTER_CONFIG_KEY)
    if path and not getattr(args, "cluster_config", None):
        if Path(path).is_file():
            args.cluster_config = path
            reused.append(f"cluster_config={path}")
        else:
            logger.log_warning(
                f"The original standup used --cluster-config {path}, which is "
                "not readable here. Its values are NOT applied; pass "
                "--cluster-config to restore them."
            )

    if reused:
        logger.log_info(
            "Reusing flags from the original standup: " + ", ".join(reused),
            emoji="♻️",
        )
    return True


def merge_invocation(cmd, context, args) -> bool:
    """Record this invocation's flags.

    Done apart from the deploy steps: they may not be in the update's scope,
    and the next update would then reuse the old flags and revert this one.
    """
    return write_invocation(cmd, context, invocation_params(args))
