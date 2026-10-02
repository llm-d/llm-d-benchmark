"""Component-scope inference for the ``update`` subcommand.

Maps the top-level config keys touched by ``--set`` onto the standup steps
that own the matching Kubernetes resources, so an update re-applies only
those steps instead of the whole standup.

Pure policy: nothing here touches a cluster or renders a template.
"""

from enum import Enum


class KeyClass(Enum):
    """How an update may treat a top-level config key."""

    SAFE = "safe"
    INFRA = "infra"
    DANGEROUS = "dangerous"
    NOOP = "noop"


#: Logical component name -> the standup step numbers that deploy it.
COMPONENT_STEPS: dict[str, frozenset[int]] = {
    "vllm": frozenset({8}),
    "epp": frozenset({7}),
    "infra": frozenset({6}),
    "standalone": frozenset({5}),
    "fma": frozenset({5, 8}),
    "nok8s": frozenset({5}),
    "kustomize": frozenset({5}),
    "prism": frozenset({9}),
    "monitoring": frozenset({3, 8}),
    "namespace": frozenset({4}),
    "admin": frozenset({2}),
}

#: Components that may not be re-applied without ``--force``, and why.
DANGEROUS_COMPONENTS: dict[str, str] = {
    "namespace": "recreates PVCs, secrets and the weight-download job",
    "admin": "re-applies cluster-scoped CRDs",
    "kustomize": "re-applies the raw kustomize tree",
    "nok8s": "re-runs the non-Kubernetes deployment",
}

#: Deploy methods with no per-stack helm release, so a deployed stack is
#: found by its workload instead.
RELEASELESS_METHODS: frozenset[str] = frozenset({"standalone", "kustomize", "fma"})

#: Steps that only make sense for a given deploy method.
_METHOD_STEPS: dict[int, frozenset[str]] = {
    5: frozenset({"standalone", "fma", "kustomize", "nok8s"}),
    6: frozenset({"modelservice"}),
    7: frozenset({"modelservice"}),
    8: frozenset({"modelservice"}),
}

#: The deploy method a component belongs to, where it has one. Another
#: method may own the same step number.
_COMPONENT_METHOD: dict[str, str] = {
    "standalone": "standalone",
    "fma": "fma",
    "kustomize": "kustomize",
    "nok8s": "nok8s",
    "vllm": "modelservice",
    "epp": "modelservice",
    "infra": "modelservice",
}

_VLLM = frozenset({"vllm"})
_EPP = frozenset({"epp"})

#: Top-level config key -> (class, components, why).
#: ``why`` is shown to the user when the key is refused or warned about.
KEY_COMPONENTS: dict[str, tuple[KeyClass, frozenset[str], str]] = {
    # -- vLLM serving pods -------------------------------------------------
    "accelerator": (KeyClass.SAFE, _VLLM | {"standalone"}, ""),
    "affinity": (KeyClass.SAFE, _VLLM | {"standalone"}, ""),
    "annotations": (KeyClass.SAFE, _VLLM | {"standalone", "fma"}, ""),
    "common": (KeyClass.SAFE, _VLLM | {"standalone"}, ""),
    "control": (KeyClass.SAFE, _VLLM | {"standalone"}, ""),
    "decode": (KeyClass.SAFE, _VLLM | {"fma"}, ""),
    "dra": (KeyClass.SAFE, _VLLM, ""),
    "extraObjects": (KeyClass.SAFE, _VLLM, ""),
    "httpRoute": (KeyClass.SAFE, _VLLM | {"standalone"}, ""),
    "images": (KeyClass.SAFE, _VLLM | _EPP | {"standalone"}, ""),
    "labels": (KeyClass.SAFE, _VLLM | {"standalone", "fma"}, ""),
    "modelArtifacts": (KeyClass.SAFE, _VLLM, ""),
    "modelservice": (KeyClass.SAFE, _VLLM, ""),
    "multinode": (KeyClass.SAFE, _VLLM, ""),
    "prefill": (KeyClass.SAFE, _VLLM | {"fma"}, ""),
    "resourcePresets": (KeyClass.SAFE, _VLLM, ""),
    "routing": (KeyClass.SAFE, _VLLM | {"standalone"}, ""),
    "schedulerName": (KeyClass.SAFE, _VLLM | {"standalone"}, ""),
    "vllmCommon": (KeyClass.SAFE, _VLLM | {"standalone", "fma"}, ""),
    # -- EPP / router ------------------------------------------------------
    "router": (KeyClass.SAFE, _EPP, ""),
    # -- other deploy methods ---------------------------------------------
    "standalone": (KeyClass.SAFE, frozenset({"standalone", "vllm", "epp", "fma"}), ""),
    "fma": (KeyClass.SAFE, frozenset({"fma"}), ""),
    "prism": (KeyClass.SAFE, frozenset({"prism"}), ""),
    # -- helm plumbing shared by every release ----------------------------
    "chartVersions": (KeyClass.SAFE, _VLLM | _EPP | {"infra"}, ""),
    "helmRepositories": (KeyClass.SAFE, _VLLM | _EPP | {"infra"}, ""),
    # -- infra: re-runs a global step -------------------------------------
    "gateway": (
        KeyClass.INFRA,
        _VLLM | _EPP | {"infra", "admin"},
        "reinstalls the gateway provider and re-applies every release",
    ),
    "gatewayProviders": (
        KeyClass.INFRA,
        frozenset({"infra", "admin"}),
        "reinstalls the gateway provider",
    ),
    "monitoring": (
        KeyClass.INFRA,
        _VLLM | _EPP | {"monitoring", "standalone"},
        "re-applies cluster monitoring config and PodMonitors",
    ),
    "openshiftMonitoring": (
        KeyClass.INFRA,
        frozenset({"monitoring"}),
        "re-applies cluster monitoring config",
    ),
    "keda": (
        KeyClass.INFRA,
        _VLLM | {"monitoring", "fma"},
        "re-applies KEDA ScaledObjects",
    ),
    "eppKedaSaturation": (
        KeyClass.INFRA,
        _VLLM | {"monitoring"},
        "re-applies EPP saturation autoscaling resources",
    ),
    "wva": (
        KeyClass.INFRA,
        _VLLM | {"monitoring", "fma"},
        "reinstalls the Workload Variant Autoscaler controller",
    ),
    "lws": (
        KeyClass.INFRA,
        frozenset({"admin"}),
        "reinstalls the LeaderWorkerSet controller",
    ),
    "serviceAccount": (
        KeyClass.INFRA,
        frozenset({"namespace"}),
        "re-applies namespace RBAC and service accounts",
    ),
    "serviceAccountOverride": (
        KeyClass.INFRA,
        _VLLM | {"namespace"},
        "re-applies namespace RBAC and service accounts",
    ),
    "huggingface": (
        KeyClass.INFRA,
        _VLLM | _EPP | {"namespace", "standalone", "fma"},
        "recreates the HuggingFace token secret",
    ),
    # -- dangerous: not updatable in place --------------------------------
    "model": (
        KeyClass.DANGEROUS,
        _VLLM | _EPP | {"namespace", "standalone", "fma"},
        "changes the served model, so the weights must be downloaded again",
    ),
    "downloadJob": (
        KeyClass.DANGEROUS,
        frozenset({"namespace"}),
        "re-runs the model-weight download job",
    ),
    "storage": (
        KeyClass.DANGEROUS,
        _VLLM | {"namespace", "prism"},
        "recreates PVCs, which cannot be resized or rebound in place",
    ),
    "namespace": (
        KeyClass.DANGEROUS,
        _VLLM | _EPP | {"admin", "namespace", "infra"},
        "moves every resource to another namespace, orphaning the current one",
    ),
    "gatewayApiCrd": (
        KeyClass.DANGEROUS,
        frozenset({"admin"}),
        "re-applies cluster-scoped CRDs, which affects every tenant",
    ),
    "release": (
        KeyClass.DANGEROUS,
        _VLLM | _EPP | {"infra"},
        "renames every helm release, orphaning the ones already installed",
    ),
    "kustomize": (
        KeyClass.DANGEROUS,
        frozenset({"kustomize"}),
        "switches to the guide-manifest deploy path",
    ),
    "nok8s": (
        KeyClass.DANGEROUS,
        frozenset({"nok8s"}),
        "switches to the container (no-Kubernetes) deploy path",
    ),
    # -- no-op: nothing deployed by standup reads these -------------------
    "harness": (KeyClass.NOOP, frozenset(), "only the run phase reads it"),
    "experiment": (KeyClass.NOOP, frozenset(), "only the experiment phase reads it"),
    "dataAccess": (KeyClass.NOOP, frozenset(), "only the run phase reads it"),
    "description": (KeyClass.NOOP, frozenset(), "it is run metadata"),
    "idleCleanup": (
        KeyClass.NOOP,
        frozenset(),
        "a standalone cleanup CronJob reads it",
    ),
}


#: Flag (argparse dest) -> the top-level key it sets, so a flag given on
#: ``update`` answers to the same scope and gate as a ``--set`` of that key.
FLAG_KEYS: dict[str, str] = {
    "models": "model",
    "release": "release",
    "gateway_class": "gateway",
    "affinity": "affinity",
    "annotations": "annotations",
    "wva": "wva",
    "epp_keda_saturation": "eppKedaSaturation",
    "monitoring": "monitoring",
    "prism": "prism",
    "no_pvc": "storage",
}

#: Flags that set no single key.
FLAG_ONLY: dict[str, tuple[KeyClass, frozenset[str], str]] = {
    "methods": (KeyClass.DANGEROUS, frozenset(), "switches the deploy method"),
    "full_infra": (
        KeyClass.NOOP,
        frozenset(),
        "it only decides which setup steps a standup runs",
    ),
}


def flag_entry(dest: str) -> tuple[KeyClass, frozenset[str], str]:
    """``(class, components, why)`` for a flag, like a ``KEY_COMPONENTS`` entry."""
    if dest in FLAG_ONLY:
        return FLAG_ONLY[dest]
    return KEY_COMPONENTS[FLAG_KEYS[dest]]


def flag_name(dest: str) -> str:
    """How the user typed a flag, for messages."""
    return "--" + dest.replace("_", "-")


def dangerous_steps(deployed_methods: list[str] | None) -> set[int]:
    """Steps that re-apply a dangerous component for these deploy methods."""
    return components_to_steps(
        set(DANGEROUS_COMPONENTS), deployed_methods=deployed_methods
    )


def classify_overrides(
    by_selector: dict[str, dict],
) -> tuple[
    set[str], list[tuple[str, str]], list[tuple[str, str]], list[str], list[str]
]:
    """Map ``--set`` buckets onto the components they touch.

    Returns ``(components, infra, dangerous, noop, unknown)``, where the
    three middle lists hold ``(key, why)`` / key entries for reporting.

    Components are unioned across every selector: the executor filters by
    step number for the whole run, not per stack, so a stack-scoped
    override still decides which steps are in scope. ``--stack`` is the
    lever for narrowing stacks.
    """
    components: set[str] = set()
    infra: list[tuple[str, str]] = []
    dangerous: list[tuple[str, str]] = []
    noop: list[str] = []
    unknown: list[str] = []

    for overrides in by_selector.values():
        for key in overrides:
            entry = KEY_COMPONENTS.get(key)
            if entry is None:
                if key not in unknown:
                    unknown.append(key)
                continue
            key_class, key_components, why = entry
            components |= set(key_components)
            if key_class is KeyClass.INFRA and (key, why) not in infra:
                infra.append((key, why))
            elif key_class is KeyClass.DANGEROUS and (key, why) not in dangerous:
                dangerous.append((key, why))
            elif key_class is KeyClass.NOOP and key not in noop:
                noop.append(key)

    return components, infra, dangerous, noop, unknown


def prune_components(
    components: set[str],
    *,
    deployed_methods: list[str] | None = None,
) -> set[str]:
    """Drop components whose steps this deploy method never runs.

    Keeps the reported component list honest: a modelservice stack must not
    be told it updated "standalone".
    """
    if not deployed_methods:
        return set(components)
    return {
        component
        for component in components
        if _component_is_deployed(component, deployed_methods)
    }


def _component_is_deployed(
    component: str,
    deployed_methods: list[str] | None,
) -> bool:
    """Whether this component's own deploy method is in use."""
    if not deployed_methods:
        return True
    method = _COMPONENT_METHOD.get(component)
    return method is None or method in set(deployed_methods)


def components_to_steps(
    components: set[str],
    *,
    deployed_methods: list[str] | None = None,
) -> set[int]:
    """Expand component names into the standup step numbers to run."""
    steps: set[int] = set()
    for component in components:
        if not _component_is_deployed(component, deployed_methods):
            continue
        steps |= COMPONENT_STEPS.get(component, frozenset())

    # 07 and 08 read values files that 06 writes, and every run has a new
    # workspace.
    if steps & {7, 8}:
        steps.add(6)

    if deployed_methods:
        methods = set(deployed_methods)
        steps = {
            step
            for step in steps
            if step not in _METHOD_STEPS or (_METHOD_STEPS[step] & methods)
        }

    return steps


def parse_components(raw: str) -> tuple[set[str], list[str]]:
    """Parse a ``--component`` list into known names plus any unknown ones."""
    requested = [part.strip() for part in raw.split(",") if part.strip()]
    known = {name for name in requested if name in COMPONENT_STEPS}
    unknown = [name for name in requested if name not in COMPONENT_STEPS]
    return known, unknown
