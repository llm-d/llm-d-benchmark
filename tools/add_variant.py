#!/usr/bin/env python3
"""
add_variant.py — Add a secondary WVA variant to an existing single-stack benchmark.

Implements Topology B: one shared InferencePool/EPP fed by two Deployments,
each with its own VariantAutoscaling (VA) at a different variantCost. The WVA
saturation solver uses variantCost to decide which variant to scale first.

Label strategy
--------------
The InferencePool created by the primary standup selects pods by:
  llm-d.ai/inferenceServing: "true"   (camelCase)
  llm-d.ai/model:            <hash>

The primary Deployment selector additionally requires:
  llm-d.ai/inference-serving: "true"  (kebab-case)

The secondary Deployment this script creates:
  - KEEPS  llm-d.ai/inferenceServing + llm-d.ai/model  → joins the pool
  - OMITS  llm-d.ai/inference-serving (kebab)           → not claimed by primary
  - ADDS   wva.llmd.ai/variant: <suffix>                → unique selector

Both VAs share the same spec.modelID so the WVA solver groups them.

Usage
-----
  python hack/benchmark/add_variant.py -n NAMESPACE \
      --config hack/benchmark/scenarios/guides/variants/<name>.yaml [--dry-run]

The variant config yaml declares only what differs from the primary.
Schema:

  suffix: v2                     # required; secondary name suffix
  variantCost: "5.0"             # default "5.0"
  minReplicas: 1                 # default 1
  maxReplicas: 10                # default 10
  parallelism:
    tensor: 2                    # rewrites --tensor-parallel-size
  resources:
    nvidia.com/gpu: 2            # mirrors limits + requests on GPU containers
"""

import argparse
import copy
import json
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print(
        "ERROR: PyYAML is required. Install with: pip install pyyaml", file=sys.stderr
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# kubectl helpers
# ---------------------------------------------------------------------------


def kubectl(*args, stdin=None, check=True):
    cmd = ["kubectl"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, input=stdin)
    if check and result.returncode != 0:
        print(f"ERROR: {' '.join(cmd)}\n{result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    return result.stdout


def kubectl_apply(obj, dry_run=False):
    payload = json.dumps(obj)
    if dry_run:
        print("---")
        print(json.dumps(obj, indent=2))
        return
    kubectl("apply", "-f", "-", stdin=payload)


def _strip_managed(obj):
    """Remove server-managed fields before re-applying as a new object."""
    meta = obj.setdefault("metadata", {})
    for field in (
        "resourceVersion",
        "uid",
        "generation",
        "creationTimestamp",
        "managedFields",
        "selfLink",
    ):
        meta.pop(field, None)
    ann = meta.get("annotations", {})
    ann.pop("kubectl.kubernetes.io/last-applied-configuration", None)
    if not ann:
        meta.pop("annotations", None)
    obj.pop("status", None)
    tmpl_meta = obj.get("spec", {}).get("template", {}).get("metadata", {})
    tmpl_meta.pop("creationTimestamp", None)
    tmpl_meta.pop("annotations", None)
    return obj


# ---------------------------------------------------------------------------
# Variant config parsing
# ---------------------------------------------------------------------------

CONFIG_DEFAULTS = {
    "variantCost": "5.0",
    "minReplicas": 1,
    "maxReplicas": 10,
}


def load_variant_config(path):
    """Load a variant override yaml, validate, and apply defaults.

    Required: `suffix`. All other keys are optional and inherit the primary's
    value if omitted (with the defaults in CONFIG_DEFAULTS for VA fields).
    """
    p = Path(path)
    if not p.is_file():
        print(f"ERROR: variant config not found: {p}", file=sys.stderr)
        sys.exit(1)
    try:
        cfg = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as e:
        print(f"ERROR: failed to parse {p}: {e}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(cfg, dict):
        print(
            f"ERROR: variant config {p} must be a yaml mapping, got "
            f"{type(cfg).__name__}",
            file=sys.stderr,
        )
        sys.exit(1)
    if "suffix" not in cfg or not isinstance(cfg["suffix"], str) or not cfg["suffix"]:
        print(f"ERROR: variant config {p} must set non-empty 'suffix'", file=sys.stderr)
        sys.exit(1)
    for k, v in CONFIG_DEFAULTS.items():
        cfg.setdefault(k, v)
    cfg["variantCost"] = str(cfg["variantCost"])
    cfg["minReplicas"] = int(cfg["minReplicas"])
    cfg["maxReplicas"] = int(cfg["maxReplicas"])
    return cfg


# ---------------------------------------------------------------------------
# Resource discovery
# ---------------------------------------------------------------------------


def find_primary_deployment(namespace):
    # The llm-d.ai/* labels live on spec.selector.matchLabels (not metadata.labels),
    # so kubectl -l filtering doesn't work — fetch all Deployments and filter here.
    out = kubectl("get", "deployment", "-n", namespace, "-o", "json")
    items = json.loads(out)["items"]

    def _is_primary(d):
        sel = d.get("spec", {}).get("selector", {}).get("matchLabels", {})
        if sel.get("llm-d.ai/inference-serving") != "true":
            return False
        if sel.get("llm-d.ai/role") != "decode":
            return False
        # Exclude secondary variants created by this script
        if "wva.llmd.ai/variant" in sel:
            return False
        return True

    primaries = [d for d in items if _is_primary(d)]
    if not primaries:
        print(
            "ERROR: No primary decode deployment found "
            "(spec.selector: llm-d.ai/inference-serving=true,llm-d.ai/role=decode)",
            file=sys.stderr,
        )
        sys.exit(1)
    if len(primaries) > 1:
        names = [d["metadata"]["name"] for d in primaries]
        print(f"ERROR: Multiple primary deployments found: {names}.", file=sys.stderr)
        sys.exit(1)
    return primaries[0]


def find_primary_va(namespace, deployment_name):
    out = kubectl("get", "variantautoscaling", "-n", namespace, "-o", "json")
    vas = json.loads(out)["items"]
    for va in vas:
        if va["spec"]["scaleTargetRef"]["name"] == deployment_name:
            return va
    print(
        f"ERROR: No VariantAutoscaling found targeting deployment '{deployment_name}'",
        file=sys.stderr,
    )
    sys.exit(1)


def find_primary_hpa(namespace, deployment_name):
    out = kubectl("get", "hpa", "-n", namespace, "-o", "json")
    hpas = json.loads(out)["items"]
    for hpa in hpas:
        if hpa["spec"]["scaleTargetRef"]["name"] == deployment_name:
            return hpa
    print(
        f"ERROR: No HPA found targeting deployment '{deployment_name}'", file=sys.stderr
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Container-arg overrides
# ---------------------------------------------------------------------------


def _override_tensor_parallel(containers, tp_value):
    """Replace `--tensor-parallel-size N` (or append it) in every container's args.

    vLLM accepts either `--tensor-parallel-size N` (two-arg) or
    `--tensor-parallel-size=N` (one-arg). Handle both.
    """
    flag = "--tensor-parallel-size"
    target = str(tp_value)
    for c in containers:
        args = c.get("args")
        if not isinstance(args, list):
            continue
        new_args = []
        i = 0
        replaced = False
        while i < len(args):
            a = args[i]
            if a == flag and i + 1 < len(args):
                new_args.extend([flag, target])
                i += 2
                replaced = True
            elif isinstance(a, str) and a.startswith(flag + "="):
                new_args.append(f"{flag}={target}")
                i += 1
                replaced = True
            else:
                new_args.append(a)
                i += 1
        if not replaced:
            new_args.extend([flag, target])
        c["args"] = new_args


def _override_gpu_resources(containers, gpu_count):
    """Set both limits and requests of nvidia.com/gpu on every container that
    already requests a GPU. Containers without a GPU resource are untouched
    (init containers, sidecars).
    """
    target = str(gpu_count)
    for c in containers:
        res = c.get("resources") or {}
        limits = res.get("limits") or {}
        requests = res.get("requests") or {}
        already_has_gpu = "nvidia.com/gpu" in limits or "nvidia.com/gpu" in requests
        if not already_has_gpu:
            continue
        limits["nvidia.com/gpu"] = target
        requests["nvidia.com/gpu"] = target
        res["limits"] = limits
        res["requests"] = requests
        c["resources"] = res


def _read_tensor_parallel(containers):
    """Return the TP value seen in the first container that sets it, or None."""
    flag = "--tensor-parallel-size"
    for c in containers:
        args = c.get("args") or []
        for i, a in enumerate(args):
            if a == flag and i + 1 < len(args):
                return args[i + 1]
            if isinstance(a, str) and a.startswith(flag + "="):
                return a.split("=", 1)[1]
    return None


def _read_gpu_per_pod(containers):
    """Return the GPU count from the first container with one, or None."""
    for c in containers:
        res = c.get("resources") or {}
        for bucket in ("limits", "requests"):
            v = (res.get(bucket) or {}).get("nvidia.com/gpu")
            if v is not None:
                return v
    return None


def _all_containers(deployment):
    """All scrape-relevant containers in a Deployment template (main +
    initContainers). Returns a list of dicts (mutable references)."""
    spec = deployment.get("spec", {}).get("template", {}).get("spec", {})
    return list(spec.get("containers") or []) + list(spec.get("initContainers") or [])


# ---------------------------------------------------------------------------
# Object builders
# ---------------------------------------------------------------------------


def make_secondary_deployment(primary, cfg, namespace):
    sec = copy.deepcopy(primary)
    _strip_managed(sec)

    suffix = cfg["suffix"]
    primary_name = primary["metadata"]["name"]
    sec_name = f"{primary_name}-{suffix}"
    sec["metadata"]["name"] = sec_name
    sec["metadata"]["namespace"] = namespace

    spec = sec["spec"]
    spec["replicas"] = 1

    # --- pod template labels ------------------------------------------------
    tmpl_labels = spec["template"]["metadata"].setdefault("labels", {})
    # Remove kebab label so primary Deployment selector won't claim these pods
    tmpl_labels.pop("llm-d.ai/inference-serving", None)
    # Add variant discriminator
    tmpl_labels["wva.llmd.ai/variant"] = suffix
    # Override the WVA variant label inherited from the primary pod template so
    # this deployment's metrics map to the v2 VariantAutoscaling, not primary.
    # Required by PR #1145 (Prometheus relabeling -> llm_d_ai_variant).
    tmpl_labels["llm-d.ai/variant"] = sec_name

    # --- Deployment selector ------------------------------------------------
    # Must match the pod template labels (minus kebab, plus variant).
    # Kubernetes selector is immutable after creation so get it right once.
    sel = spec["selector"]["matchLabels"]
    sel.pop("llm-d.ai/inference-serving", None)
    sel["wva.llmd.ai/variant"] = suffix
    # Override inherited primary's llm-d.ai/variant value so this selector
    # matches the secondary's pod-template labels (PR #1145 alignment).
    sel["llm-d.ai/variant"] = sec_name

    # --- shape overrides ----------------------------------------------------
    pod_spec = spec["template"]["spec"]
    main_containers = pod_spec.setdefault("containers", [])

    tp = (cfg.get("parallelism") or {}).get("tensor")
    if tp is not None:
        _override_tensor_parallel(main_containers, tp)

    gpu = (cfg.get("resources") or {}).get("nvidia.com/gpu")
    if gpu is not None:
        _override_gpu_resources(main_containers, gpu)

    return sec


def make_secondary_va(primary_va, sec_dep_name, cfg, namespace):
    primary_name = primary_va["metadata"]["name"]
    sec = copy.deepcopy(primary_va)
    _strip_managed(sec)

    sec["metadata"]["name"] = f"{primary_name}-{cfg['suffix']}"
    sec["metadata"]["namespace"] = namespace
    # Inherit controller-instance label so the namespace-scoped controller sees it
    sec["metadata"].setdefault("labels", {})
    sec["metadata"]["labels"]["wva.llmd.ai/controller-instance"] = namespace

    sec["spec"] = {
        "scaleTargetRef": {
            "kind": "Deployment",
            "name": sec_dep_name,
        },
        "modelID": primary_va["spec"]["modelID"],
        "variantCost": cfg["variantCost"],
        "minReplicas": cfg["minReplicas"],
        "maxReplicas": cfg["maxReplicas"],
    }
    return sec


def make_secondary_hpa(primary_hpa, sec_dep_name, cfg, namespace):
    primary_name = primary_hpa["metadata"]["name"]
    sec = copy.deepcopy(primary_hpa)
    _strip_managed(sec)

    sec["metadata"]["name"] = f"{primary_name}-{cfg['suffix']}"
    sec["metadata"]["namespace"] = namespace

    sec["spec"]["scaleTargetRef"]["name"] = sec_dep_name
    sec["spec"]["minReplicas"] = cfg["minReplicas"]
    sec["spec"]["maxReplicas"] = cfg["maxReplicas"]

    for m in sec["spec"].get("metrics", []):
        if m.get("type") == "External":
            sel = m["external"]["metric"]["selector"]["matchLabels"]
            if "variant_name" in sel:
                sel["variant_name"] = sec_dep_name

    return sec


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(
        description="Add a secondary WVA variant to an existing benchmark deployment."
    )
    ap.add_argument("-n", "--namespace", required=True, help="Kubernetes namespace")
    ap.add_argument(
        "--config",
        required=True,
        help="Path to a variant override yaml (see module docstring)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Print manifests as JSON without applying",
    )
    args = ap.parse_args()

    ns = args.namespace
    cfg = load_variant_config(args.config)
    suffix = cfg["suffix"]

    print(f"[1/3] Finding primary decode Deployment in namespace '{ns}'...")
    primary_dep = find_primary_deployment(ns)
    dep_name = primary_dep["metadata"]["name"]
    model_hash = (
        primary_dep.get("spec", {})
        .get("selector", {})
        .get("matchLabels", {})
        .get("llm-d.ai/model", "?")
    )
    primary_containers = _all_containers(primary_dep)
    primary_tp = _read_tensor_parallel(primary_containers) or "1"
    primary_gpu = _read_gpu_per_pod(primary_containers) or "1"
    print(f"      {dep_name}  (llm-d.ai/model={model_hash})")

    print("[2/3] Finding primary VariantAutoscaling...")
    primary_va = find_primary_va(ns, dep_name)
    model_id = primary_va["spec"]["modelID"]
    primary_cost = primary_va["spec"].get("variantCost", "?")
    print(
        f"      {primary_va['metadata']['name']}  "
        f"(modelID={model_id}, variantCost={primary_cost})"
    )

    print("[3/3] Finding primary HPA...")
    primary_hpa = find_primary_hpa(ns, dep_name)
    print(f"      {primary_hpa['metadata']['name']}")

    sec_dep_name = f"{dep_name}-{suffix}"

    print(
        f"\nCreating secondary variant '{suffix}'  "
        f"variantCost={cfg['variantCost']}  modelID={model_id}\n"
    )

    sec_dep = make_secondary_deployment(primary_dep, cfg, ns)
    sec_va = make_secondary_va(primary_va, sec_dep_name, cfg, ns)
    sec_hpa = make_secondary_hpa(primary_hpa, sec_dep_name, cfg, ns)

    for kind, obj in [
        ("Deployment", sec_dep),
        ("VariantAutoscaling", sec_va),
        ("HPA", sec_hpa),
    ]:
        name = obj["metadata"]["name"]
        print(f"  Applying {kind}: {name}")
        kubectl_apply(obj, dry_run=args.dry_run)

    if args.dry_run:
        return

    sec_containers = _all_containers(sec_dep)
    sec_tp = _read_tensor_parallel(sec_containers) or "1"
    sec_gpu = _read_gpu_per_pod(sec_containers) or "1"

    print()
    print("Secondary variant created successfully.")
    print(
        f"  Primary   (cost {primary_cost:>5}, TP={primary_tp}, "
        f"{primary_gpu} GPU/pod): {dep_name}"
    )
    print(
        f"  Secondary (cost {cfg['variantCost']:>5}, TP={sec_tp}, "
        f"{sec_gpu} GPU/pod): {sec_dep_name}"
    )
    print()
    print("Both VAs share modelID=" + repr(model_id) + ".")
    print("WVA will scale the cheaper variant first when both are saturated.")
    print()
    print("Verify:")
    print(f"  kubectl get va,hpa -n {ns}")
    print(
        f"  kubectl get pods -n {ns} "
        f"-l 'llm-d.ai/inferenceServing=true,llm-d.ai/model={model_hash}'"
    )


if __name__ == "__main__":
    main()
