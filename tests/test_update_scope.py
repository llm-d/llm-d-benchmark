"""Tests for the ``update`` component-scope inference.

Validates that:
- every top-level key of defaults.yaml is classified
- config keys map to the standup steps that own them
- steps 07/08 always pull in their step-06 prerequisite
- the computed step spec is parseable by StepExecutor
- components are pruned to the active deploy method
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from llmdbenchmark.executor.step_executor import StepExecutor
from llmdbenchmark.update import (
    COMPONENT_STEPS,
    DANGEROUS_COMPONENTS,
    FLAG_KEYS,
    FLAG_ONLY,
    KEY_COMPONENTS,
    KeyClass,
    classify_overrides,
    components_to_steps,
    dangerous_steps,
    parse_components,
    prune_components,
)
from llmdbenchmark.utilities.standup_parameters import (
    INVOCATION_FIELDS,
    TRISTATE_FIELDS,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULTS_PATH = PROJECT_ROOT / "config" / "templates" / "values" / "defaults.yaml"

MODELSERVICE = ["modelservice"]


def _defaults_keys() -> set[str]:
    with open(DEFAULTS_PATH, encoding="utf-8") as handle:
        values = yaml.full_load(handle)
    return {key for key in values if not key.startswith("_")}


# ---------------------------------------------------------------------------
# Table coverage
# ---------------------------------------------------------------------------


def test_every_defaults_key_is_classified():
    """A new defaults.yaml key must be classified, not silently refused."""
    missing = sorted(_defaults_keys() - set(KEY_COMPONENTS))
    assert not missing, (
        f"unclassified top-level key(s): {missing}. Add them to "
        "KEY_COMPONENTS in llmdbenchmark/update/__init__.py."
    )


def test_no_classified_key_is_stale():
    extra = sorted(set(KEY_COMPONENTS) - _defaults_keys())
    assert not extra, (
        f"KEY_COMPONENTS names key(s) defaults.yaml no longer has: {extra}"
    )


def test_components_all_have_steps():
    for key, (_, components, _why) in KEY_COMPONENTS.items():
        for component in components:
            assert component in COMPONENT_STEPS, f"{key} names unknown {component}"


def test_dangerous_and_noop_keys_explain_themselves():
    for key, (key_class, _components, why) in KEY_COMPONENTS.items():
        if key_class in (KeyClass.DANGEROUS, KeyClass.INFRA, KeyClass.NOOP):
            assert why, f"{key} is {key_class.value} but carries no explanation"


# ---------------------------------------------------------------------------
# Key -> step mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key,expected",
    [
        ("vllmCommon", {6, 8}),
        ("decode", {6, 8}),
        ("prefill", {6, 8}),
        ("router", {6, 7}),
        ("images", {6, 7, 8}),
        ("prism", {9}),
    ],
)
def test_safe_keys_map_to_expected_steps(key, expected):
    components, _, _, _, _ = classify_overrides({"*": {key: "x"}})
    assert components_to_steps(components, deployed_methods=MODELSERVICE) == expected


def test_step_six_is_added_as_prerequisite():
    """07/08 read the values files only step 06 writes into the workspace."""
    for component in ("vllm", "epp"):
        steps = components_to_steps({component}, deployed_methods=MODELSERVICE)
        assert 6 in steps, f"{component} must pull in step 06"


def test_step_six_not_added_without_seven_or_eight():
    assert components_to_steps({"prism"}, deployed_methods=MODELSERVICE) == {9}


def test_multiple_keys_union_their_steps():
    components, _, _, _, _ = classify_overrides(
        {"*": {"decode": 1}, "llama": {"router": 2}}
    )
    assert components_to_steps(components, deployed_methods=MODELSERVICE) == {6, 7, 8}


# ---------------------------------------------------------------------------
# Classification outcomes
# ---------------------------------------------------------------------------


def test_dangerous_keys_are_reported():
    _, _, dangerous, _, _ = classify_overrides({"*": {"model": {"name": "x"}}})
    assert [key for key, _ in dangerous] == ["model"]


def test_noop_keys_are_reported_and_map_to_nothing():
    components, _, _, noop, _ = classify_overrides({"*": {"harness": {"foo": 1}}})
    assert noop == ["harness"]
    assert components_to_steps(components, deployed_methods=MODELSERVICE) == set()


def test_infra_keys_are_reported():
    _, infra, _, _, _ = classify_overrides({"*": {"wva": {"enabled": True}}})
    assert [key for key, _ in infra] == ["wva"]


def test_unknown_keys_are_reported():
    _, _, _, _, unknown = classify_overrides({"*": {"notAKey": 1}})
    assert unknown == ["notAKey"]


def test_dangerous_keys_still_map_to_components():
    """--force must re-apply something, otherwise it is a silent no-op."""
    for key in ("model", "storage", "namespace", "release"):
        components, _, _, _, _ = classify_overrides({"*": {key: "x"}})
        assert components_to_steps(components, deployed_methods=MODELSERVICE), (
            f"--force {key} would re-apply nothing"
        )


# ---------------------------------------------------------------------------
# Deploy-method pruning
# ---------------------------------------------------------------------------


def test_modelservice_prunes_standalone_only_steps():
    steps = components_to_steps({"vllm", "standalone"}, deployed_methods=MODELSERVICE)
    assert 5 not in steps


def test_standalone_keeps_step_five():
    steps = components_to_steps({"standalone"}, deployed_methods=["standalone"])
    assert steps == {5}


def test_pruned_components_hide_inactive_methods():
    assert prune_components({"vllm", "standalone"}, deployed_methods=MODELSERVICE) == {
        "vllm"
    }


def test_pruning_is_skipped_without_methods():
    assert components_to_steps({"vllm"}) == {6, 8}


# ---------------------------------------------------------------------------
# Step spec round-trip
# ---------------------------------------------------------------------------


def test_step_numbers_round_trip_through_executor():
    spec = ",".join(str(step) for step in sorted(components_to_steps({"vllm"})))
    assert StepExecutor.parse_step_list(spec) == [6, 8]


# ---------------------------------------------------------------------------
# Flags and the gate
# ---------------------------------------------------------------------------


def test_every_stored_flag_is_classified():
    """A stored flag with no entry would never count as a change."""
    dests = {
        dest
        for key, dest in {**INVOCATION_FIELDS, **TRISTATE_FIELDS}.items()
        if key not in ("invocation_set", "invocation_spec")
    }
    assert dests == set(FLAG_KEYS) | set(FLAG_ONLY)
    assert set(FLAG_KEYS.values()) <= set(KEY_COMPONENTS)


def test_dangerous_steps_follow_the_deploy_method():
    assert dangerous_steps(MODELSERVICE) == {2, 4}
    assert dangerous_steps(["standalone"]) == {2, 4}
    assert dangerous_steps(["kustomize"]) == {2, 4, 5}


def test_every_dangerous_component_has_steps():
    assert set(DANGEROUS_COMPONENTS) <= set(COMPONENT_STEPS)


@pytest.mark.parametrize(
    "key, method, step",
    [
        ("monitoring", "standalone", 5),
        ("wva", "fma", 5),
        ("keda", "fma", 5),
        ("vllmCommon", "fma", 5),
        ("decode", "fma", 5),
        ("prefill", "fma", 5),
        ("annotations", "fma", 5),
        ("labels", "fma", 5),
        ("huggingface", "fma", 5),
    ],
)
def test_resources_applied_by_step_five_are_in_scope(key, method, step):
    """Step 05 renders the standalone and FMA resources."""
    components, *_ = classify_overrides({"*": {key: {"x": 1}}})
    assert step in components_to_steps(components, deployed_methods=[method])


@pytest.mark.parametrize("key", ["images", "monitoring", "huggingface", "model"])
def test_keys_read_into_the_router_block_reach_the_epp(key):
    """The plan renderer copies these keys into the EPP values."""
    components, *_ = classify_overrides({"*": {key: {"x": 1}}})
    assert "epp" in components


# ---------------------------------------------------------------------------
# --component parsing
# ---------------------------------------------------------------------------


def test_parse_components_splits_and_validates():
    known, unknown = parse_components("epp, vllm ,nope")
    assert known == {"epp", "vllm"}
    assert unknown == ["nope"]


def test_parse_components_ignores_empty_entries():
    known, unknown = parse_components("epp,,")
    assert known == {"epp"}
    assert unknown == []
