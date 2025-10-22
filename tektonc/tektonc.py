#!/usr/bin/env python3
"""
tektonc — minimal render+expand for Tekton templates with loop nodes.

Authoring grammar (one construct only):
  Loop node := { loopName: str, foreach: { domain: { var: [..], ... } }, tasks: [ <task or loop>, ... ] }
  Task node := any Tekton task map (name, taskRef/taskSpec, params, runAfter, workspaces, retries, when, timeout, ...)

Semantics:
  - Expansion is cartesian over foreach.domain (keys sorted for determinism).
  - Loops can nest; variables from outer loops are in scope for inner loops.
  - Dependencies/parallelism are expressed purely via native Tekton 'runAfter'.
  - 'finally' supports the same loop nodes as 'tasks'.
  - No validation yet (name uniqueness, runAfter targets, DAG acyclicity)—add later.

CLI:
  tektonc -t pipeline.yaml.j2 -f values.yaml [-o build/pipeline.yaml] [--explain]
"""

from __future__ import annotations

import argparse
import copy
import itertools
import os
import sys
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping

import json, yaml
from jinja2 import Environment, StrictUndefined, TemplateError, Undefined
from jinja2.runtime import Undefined as RTUndefined



# ──────────────────────────────────────────────────────────────────────────────
# Jinja helpers
#   Two-pass render:
#     - Outer env: preserves unknown loop vars (e.g., {{ modelRef|dns }} stays literal)
#     - Inner env: strict; resolves loop vars during loop expansion
# ──────────────────────────────────────────────────────────────────────────────

def _dns_inner(s: str) -> str:
    """DNS-1123-ish: lowercase, alnum and dash, trim to 63 chars with hash fallback."""
    import re, hashlib
    s2 = re.sub(r'[^a-z0-9-]+', '-', str(s).lower()).strip('-')
    if len(s2) <= 63:
        return s2
    h = hashlib.sha1(s2.encode()).hexdigest()[:8]
    return (s2[:63-1-8] + '-' + h).strip('-')

def _slug_inner(s: str) -> str:
    """Looser slug for params: keep letters/numbers/._-; replace others with '-'."""
    import re
    return re.sub(r'[^A-Za-z0-9_.-]+', '-', str(s))

# Outer filters: if value is undefined, round-trip original expression
def _dns_outer(val: object) -> str:
    if isinstance(val, RTUndefined):
        name = getattr(val, "_undefined_name", None) or "<?>"
        return "{{ " + name + "|dns }}"
    return _dns_inner(val)  # type: ignore[arg-type]

def _slug_outer(val: object) -> str:
    if isinstance(val, RTUndefined):
        name = getattr(val, "_undefined_name", None) or "<?>"
        return "{{ " + name + "|slug }}"
    return _slug_inner(val)  # type: ignore[arg-type]

class PassthroughUndefined(Undefined):
    """In OUTER render, keep unknown variables as '{{ name }}' so inner pass can resolve them."""
    __slots__ = ()
    def __str__(self) -> str:  # type: ignore[override]
        name = getattr(self, "_undefined_name", None)
        return "{{ " + name + " }}" if name else "{{ ?? }}"
    def __iter__(self):  # allows use in loops without crashing
        return iter(())
    def __bool__(self) -> bool:  # treat undefined as False
        return False

def _enum(seq):
    """Return [{i, item}, ...] for easy serial chains in Jinja."""
    return [{"i": i, "item": v} for i, v in enumerate(seq)]

def build_env_outer() -> Environment:
    env = Environment(undefined=PassthroughUndefined, autoescape=False, trim_blocks=True, lstrip_blocks=False)
    env.filters.update({"dns": _dns_outer, "slug": _slug_outer, "tojson": json.dumps})
    env.globals.update({"enumerate_list": _enum})
    return env

def build_env_inner() -> Environment:
    env = Environment(undefined=StrictUndefined, autoescape=False, trim_blocks=True, lstrip_blocks=False)
    env.filters.update({"dns": _dns_inner, "slug": _slug_inner, "tojson": json.dumps})
    env.globals.update({"enumerate_list": _enum})
    return env


# ──────────────────────────────────────────────────────────────────────────────
# Expander (no validation yet)
# ──────────────────────────────────────────────────────────────────────────────

def expand_document(doc: MutableMapping[str, Any],
                    globals: Mapping[str, Any] | None = None,
                    jinja_env: Environment | None = None) -> Dict[str, Any]:
    """
    Expand loops in a Pipeline document:
      - Recursively expands spec.tasks (required) and spec.finally (optional)
      - Returns a NEW dict; input is not mutated
    """
    env = jinja_env or build_env_inner()
    scope: Dict[str, Any] = dict(globals or {})

    out: Dict[str, Any] = copy.deepcopy(doc)  # type: ignore[assignment]
    spec = out.get("spec") or {}

    spec["tasks"] = expand_list(spec.get("tasks", []), scope, env)
    if "finally" in spec:
        spec["finally"] = expand_list(spec.get("finally", []), scope, env)

    out["spec"] = spec
    return out

def expand_list(nodes: Iterable[Any],
                scope: Mapping[str, Any],
                env: Environment) -> List[Dict[str, Any]]:
    """
    Core recursive expander.

    If a node is a loop node (loopName + foreach.domain + tasks list):
      * Enumerate cartesian product over the domain (keys sorted for determinism)
      * For each binding, extend scope and recursively expand the child 'tasks'
      * Concatenate all expansions

    Else (plain Tekton task):
      * Deep-copy the map; render ALL scalar strings with current scope (via Jinja)
      * Append as a single task in the flat list
    """
    flat: List[Dict[str, Any]] = []
    for node in nodes or []:
        if _is_loop_node(node):
            domain = node["foreach"]["domain"]
            child_nodes = node.get("tasks", [])
            for binding in _cartesian_bindings(domain):
                child_scope = dict(scope)
                child_scope.update(binding)
                flat.extend(expand_list(child_nodes, child_scope, env))
        else:
            rendered = _render_scalars(copy.deepcopy(node), scope, env)
            # After scalar render, node should be a mapping for Tekton; we pass it through
            flat.append(rendered)  # type: ignore[arg-type]
    return flat

# ──────────────────────────────────────────────────────────────────────────────
# Internals
# ──────────────────────────────────────────────────────────────────────────────

def _is_loop_node(node: Any) -> bool:
    """A loop node must be a mapping with loopName, foreach.domain, and tasks (list)."""
    from collections.abc import Mapping as _Mapping
    if not isinstance(node, _Mapping):
        return False
    if "loopName" not in node or "foreach" not in node or "tasks" not in node:
        return False
    f = node["foreach"]
    if not isinstance(f, dict) or "domain" not in f:
        return False
    if not isinstance(node["tasks"], list):
        return False
    return True

def _cartesian_bindings(domain: Mapping[str, Iterable[Any]]) -> Iterable[Dict[str, Any]]:
    """
    Deterministic cartesian enumeration of a domain dict: {var: [v1, v2], ...}
      - Sort domain keys to ensure stable order
      - Preserve the order of each value list
      - Yield dicts like {'var1': v1, 'var2': v2, ...}
    """
    if not isinstance(domain, Mapping):
        raise TypeError("foreach.domain must be a mapping of {var: list}")

    keys = sorted(domain.keys())
    lists: List[List[Any]] = []
    for k in keys:
        vals = domain[k]
        if isinstance(vals, (str, bytes)):
            raise TypeError(f"foreach.domain['{k}'] must be an iterable of values (not string)")
        lists.append(list(vals))

    for combo in itertools.product(*lists):
        yield dict(zip(keys, combo))

def _render_scalars(obj: Any, scope: Mapping[str, Any], env: Environment) -> Any:
    """
    Recursively render scalar strings using Jinja with the given scope.
      - Dict: render values
      - List/Tuple: render each element
      - String: env.from_string(s).render(scope)
      - Other scalars: return as-is

    Note: We do NOT render dict keys — only values.
    """
    from collections.abc import Mapping as _Mapping
    if isinstance(obj, _Mapping):
        return {k: _render_scalars(v, scope, env) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_render_scalars(v, scope, env) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_render_scalars(v, scope, env) for v in obj)
    if isinstance(obj, str):
        try:
            return env.from_string(obj).render(**scope)
        except TemplateError as e:
            raise RuntimeError(f"Template render failed for: {obj!r} (scope keys={list(scope.keys())})") from e
    return obj

# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Render + expand Tekton templates with loop nodes")
    ap.add_argument("-t", "--template", required=True, help="Jinja template file (use - for stdin)")
    ap.add_argument("-f", "--values",   required=True, help="YAML/JSON values file (use - for stdin)")
    ap.add_argument("-o", "--out", help="Output YAML file (default: stdout)")
    ap.add_argument("--explain", action="store_true", help="Print name/runAfter table to stderr after expansion")
    return ap.parse_args(argv)

def _read_text(path: str) -> str:
    return sys.stdin.read() if path == "-" else open(path, "r").read()

def _load_values(path: str) -> Dict[str, Any]:
    data = _read_text(path)
    return yaml.safe_load(data) or {}

def _explain(expanded: Mapping[str, Any]) -> None:
    def print_section(title: str, items: List[Mapping[str, Any]]):
        print(f"# {title}", file=sys.stderr)
        print(f"{'TASK NAME':<60}  RUNAFTER", file=sys.stderr)
        print("-" * 90, file=sys.stderr)
        for t in items:
            name = t.get("name", "<unnamed>")  # type: ignore[assignment]
            ra = t.get("runAfter", [])
            ra_str = ", ".join(ra) if isinstance(ra, list) else str(ra)
            print(f"{name:<60}  {ra_str}", file=sys.stderr)
        print("", file=sys.stderr)

    spec = expanded.get("spec") or {}
    tasks = spec.get("tasks", [])
    print_section("spec.tasks", tasks)
    if "finally" in spec:
        print_section("spec.finally", spec.get("finally", []))

def main(argv=None) -> int:
    args = parse_args(argv)

    try:
        values = _load_values(args.values)

        # 1) OUTER render with globals; loop vars are preserved verbatim
        env_outer = build_env_outer()
        template_src = _read_text(args.template)
        rendered = env_outer.from_string(template_src).render(**values)

        # 2) YAML parse
        doc = yaml.safe_load(rendered)
        if not isinstance(doc, dict):
            print("Rendered template is not a YAML mapping (expected a Pipeline).", file=sys.stderr)
            return 1

        # 3) Loop expansion with INNER strict env (resolves loop vars)
        env_inner = build_env_inner()
        expanded: Dict[str, Any] = expand_document(doc, globals=values, jinja_env=env_inner)

        # 4) Optional explain
        if args.explain:
            _explain(expanded)

        # 5) Output
        out_text = yaml.safe_dump(expanded, sort_keys=False)
        if args.out:
            with open(args.out, "w") as f:
                f.write(out_text)
        else:
            sys.stdout.write(out_text)
        return 0

    except TemplateError as e:
        print(f"Template render error: {e}", file=sys.stderr)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
    return 1

if __name__ == "__main__":
    raise SystemExit(main())
