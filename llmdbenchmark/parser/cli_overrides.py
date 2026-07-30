"""Parse ``--set`` scenario overrides from the command line.

These are *scenario* (plan) overrides: dotted paths into the merged stack
config, deep-merged on top of ``defaults.yaml`` + ``shared:`` + the stack
block by :class:`~llmdbenchmark.parser.render_plans.RenderPlans`.  They are
NOT the same thing as ``run -o`` / ``experiment -o``, which override the
rendered *workload profile* YAML (see
:mod:`llmdbenchmark.utilities.profile_renderer`).

Grammar -- split on the first ``=``, then look for ``:`` in the key half
only, so values containing colons (``quay.io/llm-d/epp:v1.2``) are never
mistaken for a stack selector::

    decode.replicas=2                  -> every stack (implicit "*")
    llama-31-8b:decode.replicas=2      -> that stack only
    '*-8b:decode.replicas=2'           -> fnmatch glob over stack names

Multiple pairs are comma-separated (commas inside ``[]``/``{}``/quotes are
respected) and the flag is repeatable.  Values are parsed with
``yaml.safe_load`` so ``4``, ``true``, ``[a, b]`` and ``{a: 1}`` mean the
same thing they would inside the scenario YAML.
"""

from __future__ import annotations

import datetime
import fnmatch
from typing import Any

import yaml

from llmdbenchmark.experiment.parser import dotted_to_nested

# Selector that matches every stack -- what a pair with no ``stack:`` prefix
# gets. Also the bucket the ``--cluster-config`` file is folded into, so the
# whole scenario-override precedence chain lives in one structure.
GLOBAL_SELECTOR = "*"

# Characters that make a selector a glob rather than an exact stack name.
_GLOB_CHARS = "*?["


class OverrideParseError(ValueError):
    """Raised when a ``--set`` expression cannot be parsed."""


def split_override_pairs(raw: str) -> list[str]:
    """Split one ``--set`` value on top-level commas.

    Commas inside brackets/braces/parens or inside quotes belong to the
    value, not to the pair separator, so ``a=[1,2],b=3`` yields two pairs.
    """
    pairs: list[str] = []
    buf: list[str] = []
    depth = 0
    quote: str | None = None

    for char in raw:
        if quote is not None:
            buf.append(char)
            if char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
            buf.append(char)
            continue
        if char in "[{(":
            depth += 1
        elif char in "]})":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            pairs.append("".join(buf))
            buf = []
            continue
        buf.append(char)

    pairs.append("".join(buf))
    return [p.strip() for p in pairs if p.strip()]


def coerce_value(raw: str) -> Any:
    """Coerce a CLI-supplied value the way the scenario YAML would.

    ``yaml.safe_load`` gives int/float/bool/list/mapping/quoted-string for
    free.  An empty right-hand side (``foo=``) means the empty string, not
    ``None`` -- ``None`` is skipped by ``deep_merge`` and would silently
    no-op.  Anything YAML can't parse is kept as a plain string.
    """
    stripped = raw.strip()
    if stripped == "":
        return ""
    try:
        return yaml.safe_load(stripped)
    except yaml.YAMLError:
        return raw


def surprising_coercion(raw: str, value: Any) -> str | None:
    """Describe a YAML coercion a user is unlikely to have intended.

    YAML 1.1 reads ``012`` as octal 10, ``1:30`` as sexagesimal 90, ``0x10``
    as 16 and ``2024-01-01`` as a date. Those readings are correct -- they
    are exactly what the scenario file would do -- but on a command line
    they silently change the value, so they are worth a warning with the
    quoting escape hatch.

    Returns a message, or None when the coercion is unremarkable.
    """
    if isinstance(value, datetime.date):  # covers datetime.datetime too
        return (
            f"value {raw.strip()!r} was read as a YAML date ({value}); "
            f"quote it (e.g. \"'{raw.strip()}'\") to keep it a string"
        )

    # bool is a subclass of int -- true/false/on/off are documented and
    # expected, so they are never "surprising".
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None

    stripped = raw.strip()
    try:
        plain = float(stripped)
    except ValueError:
        # YAML produced a number from something Python won't read as one
        # (0x10, 1:30, .inf).
        return (
            f"value {stripped!r} was read as the number {value!r}; "
            f"quote it (e.g. \"'{stripped}'\") to keep it a string"
        )

    if plain != float(value):
        # Both read it as a number, but differently (012 -> 10 octal).
        return (
            f"value {stripped!r} was read as {value!r}, not {stripped}; "
            f"quote it (e.g. \"'{stripped}'\") to keep it a string"
        )

    return None


def parse_override_pair(pair: str) -> tuple[str, str, Any]:
    """Parse one ``[selector:]dotted.key=value`` expression.

    Returns ``(selector, dotted_key, value)``.
    """
    if "=" not in pair:
        raise OverrideParseError(
            f"invalid override '{pair}': expected [stack:]dotted.key=value"
        )

    key_part, raw_value = pair.split("=", 1)
    key_part = key_part.strip()

    selector = GLOBAL_SELECTOR
    if ":" in key_part:
        selector, key_part = key_part.split(":", 1)
        selector = selector.strip()
        key_part = key_part.strip()
        if not selector:
            raise OverrideParseError(
                f"invalid override '{pair}': empty stack selector before ':'"
            )

    if not key_part:
        raise OverrideParseError(f"invalid override '{pair}': empty key")

    return selector, key_part, coerce_value(raw_value)


def parse_cli_overrides(
    values: str | list[str] | None,
) -> tuple[dict[str, dict], list[str]]:
    """Parse ``--set`` values into ``{selector: nested_override_dict}``.

    ``values`` is whatever argparse collected (a repeatable flag, so a list;
    a bare string is accepted for env-var convenience).  Returns the parsed
    buckets plus a list of non-fatal warnings.  Selector insertion order is
    preserved, which is how ties are broken in :func:`selectors_for_stack`.

    Raises :class:`OverrideParseError` on a malformed expression -- a typo
    here means the user did not get the config they asked for, so it must
    fail loudly rather than be dropped.
    """
    if not values:
        return {}, []
    if isinstance(values, str):
        values = [values]

    warnings: list[str] = []
    flat_by_selector: dict[str, dict[str, Any]] = {}

    for raw in values:
        for pair in split_override_pairs(raw):
            selector, key, value = parse_override_pair(pair)
            bucket = flat_by_selector.setdefault(selector, {})
            secret = is_secret_path(key)
            if key in bucket:
                shown = f"({REDACTED})" if secret else f"({bucket[key]!r} -> {value!r})"
                warnings.append(
                    f"override '{key}' set more than once for "
                    f"'{selector}' -- last value wins {shown}"
                )
            if value is None:
                warnings.append(
                    f"override '{key}' resolves to null, which cannot clear a "
                    "value (null is treated as 'no value' by the config merge) "
                    "-- use an explicit empty string ('') instead"
                )
            raw_value = pair.split("=", 1)[1]
            if not secret and (
                (surprise := surprising_coercion(raw_value, value)) is not None
            ):
                # The message quotes the raw value, so it is suppressed
                # entirely for credential paths rather than redacted.
                warnings.append(f"override '{key}': {surprise}")
            bucket[key] = value

    nested_by_selector: dict[str, dict] = {}
    for selector, flat in flat_by_selector.items():
        try:
            nested_by_selector[selector] = dotted_to_nested(flat)
        except ValueError as exc:
            raise OverrideParseError(f"conflicting overrides: {exc}") from exc

    return nested_by_selector, warnings


def is_glob(selector: str) -> bool:
    """True when the selector is a pattern rather than an exact stack name."""
    return any(char in selector for char in _GLOB_CHARS)


def selectors_for_stack(
    by_selector: dict[str, dict],
    stack_name: str,
) -> list[str]:
    """Selectors applying to ``stack_name``, least specific first.

    Ordering is by specificity -- global, then globs, then exact names --
    with command-line order breaking ties inside each tier.  So
    ``-o 'wva.hpa.maxReplicas=6' -o 'llama:wva.hpa.maxReplicas=2'`` gives
    llama 2 regardless of which flag came first.
    """
    if not by_selector:
        return []

    globals_: list[str] = []
    globs: list[str] = []
    exacts: list[str] = []

    for selector in by_selector:
        if selector == GLOBAL_SELECTOR:
            globals_.append(selector)
        elif is_glob(selector):
            if fnmatch.fnmatchcase(stack_name, selector):
                globs.append(selector)
        elif selector == stack_name:
            exacts.append(selector)

    return globals_ + globs + exacts


def validate_selectors(
    by_selector: dict[str, dict],
    known_stack_names: list[str],
) -> list[str]:
    """Return errors for selectors that match no stack in the scenario.

    A mistyped selector is otherwise a silent no-op: the render succeeds and
    deploys a stack the user believes they modified.  Mirrors the fail-fast
    treatment ``--stack`` already gets.
    """
    errors: list[str] = []
    known_sorted = ", ".join(sorted(known_stack_names)) or "<none>"

    for selector in by_selector:
        if selector == GLOBAL_SELECTOR:
            continue
        if is_glob(selector):
            if not any(
                fnmatch.fnmatchcase(name, selector) for name in known_stack_names
            ):
                errors.append(
                    f"override selector '{selector}' matched no stack in this "
                    f"scenario. Known stacks: {known_sorted}."
                )
        elif selector not in known_stack_names:
            errors.append(
                f"override selector '{selector}' references an unknown stack. "
                f"Known stacks: {known_sorted}."
            )

    return errors


# Config paths whose value must never reach a log. ``huggingface.token`` is
# a plain-text HF token in ``defaults.yaml``, and overrides are echoed back
# with their values, so an unfiltered log would leak it to stdout and to the
# workspace log files. Matched on the LAST dotted segment, case-insensitively.
_SECRET_SEGMENTS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "credential",
        "credentials",
        "apikey",
        "api_key",
        "accesskey",
        "privatekey",
    }
)

#: What a redacted value renders as -- matches the convention already used by
#: the kustomize deploy step.
REDACTED = "<redacted>"


def is_secret_path(dotted_path: str) -> bool:
    """True when a config path holds a credential that must not be logged.

    Deliberately errs toward over-masking: ``token``/``tokenBase64``/
    ``tokenKey`` all match the ``token`` prefix. ``maxNumBatchedTokens``
    does not (it ends in "tokens" and starts with "max"), so ordinary
    numeric fields keep showing their values.
    """
    segment = dotted_path.rsplit(".", 1)[-1].lower()
    return segment.startswith("token") or segment in _SECRET_SEGMENTS


def redact(dotted_path: str, value: Any) -> Any:
    """Return ``value``, or the redaction marker for a secret-bearing path."""
    return REDACTED if is_secret_path(dotted_path) else value


_MISSING = object()


def dotted_leaves(nested: dict, prefix: str = "") -> list[tuple[str, Any]]:
    """Flatten a nested override dict to ``(dotted.path, value)`` pairs.

    An empty mapping is itself a leaf -- ``kustomize.extraHelmSets={}`` is a
    meaningful assignment, not an empty branch to recurse into.
    """
    leaves: list[tuple[str, Any]] = []
    for key, value in nested.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and value:
            leaves.extend(dotted_leaves(value, path))
        else:
            leaves.append((path, value))
    return leaves


def resolve_dotted(values: dict, dotted_path: str) -> Any:
    """Return the value at ``dotted_path``, or ``_MISSING`` if absent.

    Used to report the pre-override value in logs; compare the result
    against :data:`MISSING` rather than ``None`` (a key explicitly set to
    ``None`` is not the same as an absent key).
    """
    current: Any = values
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


#: Sentinel returned by :func:`resolve_dotted` for an absent path.
MISSING = _MISSING


def find_broken_parent_paths(
    overrides: dict,
    base: dict,
    prefix: str = "",
) -> tuple[list[str], list[tuple[str, str]]]:
    """Classify override paths that cannot address what the user meant.

    Returns ``(unknown_parents, clobbered)``:

    - ``unknown_parents`` -- the parent key is absent from ``base``, so the
      override creates a new block. Usually a typo (``decode.resurces``),
      but legitimate for free-form blocks such as
      ``kustomize.guideVariableOverrides``, so this is warn-only. Mirrors
      the rule :func:`~llmdbenchmark.utilities.profile_renderer.apply_overrides`
      uses for workload-profile overrides.
    - ``clobbered`` -- ``(path, type_name)`` where the parent *exists* but is
      a list or scalar, so descending into it would silently REPLACE it.
      ``vllmCommon.volumeMounts.0.mountPath=/x`` would turn a two-element
      list into ``{"0": {...}}``, dropping both mounts. Dotted paths cannot
      index into lists here (unlike workload-profile overrides), so this is
      always a mistake and callers treat it as fatal. Assigning a whole new
      list is still fine -- that is a leaf, not a descent.

    A bare top-level typo (``fooo=1``) is indistinguishable from a
    legitimate new key and is not reported.
    """
    unknown: list[str] = []
    clobbered: list[tuple[str, str]] = []

    for key, value in overrides.items():
        path = f"{prefix}.{key}" if prefix else key
        if not isinstance(value, dict) or not value:
            continue
        has_key = isinstance(base, dict) and key in base
        child = base.get(key) if isinstance(base, dict) else None
        if isinstance(child, dict):
            child_unknown, child_clobbered = find_broken_parent_paths(
                value, child, path
            )
            unknown.extend(child_unknown)
            clobbered.extend(child_clobbered)
        elif has_key and child is not None:
            clobbered.append((path, type(child).__name__))
        else:
            unknown.append(path)

    return unknown, clobbered
