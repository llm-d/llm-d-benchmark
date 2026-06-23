"""Tests for the run-treatment override warning surface.

Pins the contract that:

- ``apply_overrides`` returns ``(content, unmatched_keys)``.
- A key whose dotted PARENT path doesn't exist in the workload profile is
  flagged as unmatched (silent no-op territory).
- A key whose parent path exists but whose LEAF is new is still applied
  (intentional add-new-field overrides keep working).
- ``classify_override_miss`` distinguishes two classes:

  1. Plan-level prefixes (``decode.``, ``router.``, ``schedulerName``,
     ...) -> hint points at ``setup.treatments`` or
     ``kustomize.extraHelmSets``.
  2. Anything else -> generic typo / wrong-harness hint.

These guard the user-visible diagnostic that fires when someone puts a
plan/scenario field under the top-level ``treatments:`` block or the
``--overrides`` flag.
"""

from __future__ import annotations

import textwrap

import pytest

from llmdbenchmark.utilities.profile_renderer import (
    apply_overrides,
    classify_override_miss,
)


PROFILE = textwrap.dedent(
    """\
    api:
      type: completion
      streaming: true
    data:
      shared_prefix:
        question_len: 100
        output_len: 100
    load:
      stages:
      - rate: 1
        duration: 60
    """
)


# ---------------------------------------------------------------------------
# apply_overrides matching behaviour
# ---------------------------------------------------------------------------


class TestApplyOverridesMatching:
    def test_matched_key_is_applied_and_returns_empty_unmatched(self):
        content, unmatched = apply_overrides(
            PROFILE, {"data.shared_prefix.question_len": "500"}
        )
        assert unmatched == []
        import yaml

        parsed = yaml.safe_load(content)
        assert parsed["data"]["shared_prefix"]["question_len"] == 500

    def test_unmatched_parent_path_is_reported(self):
        _, unmatched = apply_overrides(
            PROFILE, {"router.epp.pluginsConfigFile": "x.yaml"}
        )
        assert unmatched == ["router.epp.pluginsConfigFile"]

    def test_missing_leaf_with_intact_parents_is_still_applied(self):
        """Treatments that add a NEW leaf to an existing parent dict are valid.
        We only warn when the parent chain is broken (silent no-op territory)."""
        content, unmatched = apply_overrides(
            PROFILE, {"data.shared_prefix.new_field": "42"}
        )
        assert unmatched == []
        import yaml

        parsed = yaml.safe_load(content)
        assert parsed["data"]["shared_prefix"]["new_field"] == 42

    def test_partial_path_match_is_reported_when_first_break_is_partway(self):
        """``data.nonexistent.foo`` -- ``data`` exists, but ``nonexistent`` doesn't.
        Reported as unmatched."""
        _, unmatched = apply_overrides(PROFILE, {"data.nonexistent.foo": "1"})
        assert unmatched == ["data.nonexistent.foo"]

    def test_multiple_overrides_partition_correctly(self):
        _, unmatched = apply_overrides(
            PROFILE,
            {
                "api.streaming": "false",
                "data.shared_prefix.question_len": "300",
                "decode.replicas": "4",
                "router.epp.pluginsConfigFile": "x.yaml",
            },
        )
        assert set(unmatched) == {
            "decode.replicas",
            "router.epp.pluginsConfigFile",
        }

    def test_list_indexed_path_is_currently_reported_as_unmatched(self):
        """``load.stages.0.rate`` -- the walker hits the ``stages`` list and
        ``"0" in <list>`` is False, so the path is reported as unmatched.

        This is a pre-existing limitation of apply_overrides (it only walks
        dicts), unrelated to the plan-level warning. We pin it here so a
        future contributor who adds list-index support gets a failing test
        to update, rather than silently changing the warning surface.
        """
        _, unmatched = apply_overrides(PROFILE, {"load.stages.0.rate": "10"})
        assert unmatched == ["load.stages.0.rate"]

    def test_yaml_parse_failure_returns_original_and_empty_unmatched(self):
        bad = "this: is:\n  - broken: ["
        content, unmatched = apply_overrides(bad, {"x.y": "1"})
        assert content == bad
        assert unmatched == []

    def test_non_dict_root_returns_original_and_empty_unmatched(self):
        content, unmatched = apply_overrides("just a string", {"x.y": "1"})
        assert content == "just a string"
        assert unmatched == []


# ---------------------------------------------------------------------------
# classify_override_miss hint quality
# ---------------------------------------------------------------------------


class TestClassifyOverrideMiss:
    @pytest.mark.parametrize(
        "key",
        [
            "decode.replicas",
            "decode.parallelism.tensor",
            "prefill.replicas",
            "standalone.enabled",
            "modelservice.enabled",
            "fma.enabled",
            "kustomize.extraHelmSets.epp.pluginsConfigFile",
            "router.epp.pluginsConfigFile",
            "router.tokenizer.enabled",
            "router.monitoring.prometheus.enabled",
            "vllmCommon.kvTransfer.enabled",
            "model.maxModelLen",
            "schedulerName",
            "scheduler.config",
            "gateway.className",
            "routing.proxy.connector",
            "storage.modelPvc.name",
            "wva.enabled",
            "huggingface.enabled",
        ],
    )
    def test_plan_level_prefix_gets_sharper_hint(self, key: str):
        msg = classify_override_miss(key)
        assert key in msg
        assert "plan/scenario field" in msg
        assert "setup.treatments" in msg
        assert "kustomize.extraHelmSets" in msg

    @pytest.mark.parametrize(
        "key",
        [
            "load.stages.0.rrate",
            "data.shared_prefix.queston_len",
            "api.streaminng",
            "some.random.path",
        ],
    )
    def test_non_plan_key_gets_typo_hint(self, key: str):
        msg = classify_override_miss(key)
        assert key in msg
        assert "silently dropped" in msg
        assert "typo" in msg or "wrong harness" in msg
        assert "plan/scenario field" not in msg


# ---------------------------------------------------------------------------
# End-to-end: step_05 emits the warning
# ---------------------------------------------------------------------------


class TestStep05EmitsWarning:
    """A run-treatment with a plan-level override hits ``apply_overrides``,
    gets reported as unmatched, and the step's log surface should receive a
    warning per missed key.

    We don't drive the full step here -- that requires a full ExecutionContext.
    Instead we re-use the writer + classifier directly, mirroring what step_05
    does at L196-205, to pin the message shape end-to-end.
    """

    def test_warning_includes_treatment_name_and_classified_hint(self):
        treatment_name = "precise-prefix"
        overrides = {"router.epp.pluginsConfigFile": "precise-prefix-cache-config.yaml"}
        _, unmatched = apply_overrides(PROFILE, overrides)
        assert unmatched == ["router.epp.pluginsConfigFile"]

        for key in unmatched:
            msg = f"Treatment '{treatment_name}': " + classify_override_miss(key)
            assert "Treatment 'precise-prefix'" in msg
            assert "router.epp.pluginsConfigFile" in msg
            assert "setup.treatments" in msg
