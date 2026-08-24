"""Tests for smoketest validation of ``vllmVariants`` (context-length-aware
routing).

When a role declares ``vllmVariants``, ``config/templates/jinja/_macros.j2``
emits *one* env var per parameter holding *every* replica's value joined by
``,,`` -- e.g. ``VLLM_MAX_MODEL_LEN="2048,,2048"``, ``VLLM_MAX_NUM_SEQ="64,,16"``.
All replicas share one pod template, so that list is what lands in the pod spec;
``set_llmdbench_environment.py`` splits it at container start and re-exports the
entry matching the pod's LWS index.

The validator previously compared the raw spec value against the single scalar
``model.maxModelLen``, so any scenario using ``vllmVariants`` failed with
``VLLM_MAX_MODEL_LEN=2048,,2048 (expected 16384)`` even though the deployment
was correct. These tests pin:

1. The ``,,`` list is compared against the joined variant values, not a scalar.
2. The two other variant-driven env vars (``VLLM_MAX_NUM_SEQ``,
   ``VLLM_MAX_NUM_BATCHED_TOKENS``) are checked too -- they were never checked
   at all before, in either the scalar or the variant case.
3. Gating matches the template exactly, including the quirk that
   ``maxNumSeq``/``maxNumBatchedTokens`` are emitted as lists only when the
   *first* variant declares them.
4. Scenarios without ``vllmVariants`` keep the old scalar behaviour.
"""

from __future__ import annotations

import sys
import types

import pytest

# Stub planner so we can import smoketest modules (see
# test_smoketest_inference.py for the same pattern + rationale).
if "planner" not in sys.modules:
    planner_stub = types.ModuleType("planner")
    capacity_stub = types.ModuleType("planner.capacity_planner")
    capacity_stub.__getattr__ = lambda name: lambda *a, **kw: None  # type: ignore[attr-defined]
    sys.modules["planner"] = planner_stub
    sys.modules["planner.capacity_planner"] = capacity_stub

from llmdbenchmark.smoketests.base import BaseSmoketest  # noqa: E402


# ---------------------------------------------------------------------------
# assert_env_variant_list
# ---------------------------------------------------------------------------


class TestAssertEnvVariantList:
    """The helper compares against the ,,-joined list the template renders."""

    def test_matching_list_passes(self):
        env = {"VLLM_MAX_MODEL_LEN": "2048,,2048"}
        result = BaseSmoketest.assert_env_variant_list(
            env, "VLLM_MAX_MODEL_LEN", [2048, 2048]
        )
        assert result.passed
        assert result.name == "env_VLLM_MAX_MODEL_LEN"

    def test_differing_values_join_in_order(self):
        """Order matters -- index N of the list is pod index N's value."""
        env = {"VLLM_MAX_NUM_SEQ": "64,,16"}
        assert BaseSmoketest.assert_env_variant_list(
            env, "VLLM_MAX_NUM_SEQ", [64, 16]
        ).passed
        # Reversed must NOT pass: decode-0 and decode-1 would be swapped.
        assert not BaseSmoketest.assert_env_variant_list(
            env, "VLLM_MAX_NUM_SEQ", [16, 64]
        ).passed

    def test_wrong_value_fails_with_both_sides(self):
        env = {"VLLM_MAX_MODEL_LEN": "2048,,2048"}
        result = BaseSmoketest.assert_env_variant_list(
            env, "VLLM_MAX_MODEL_LEN", [2048, 4096]
        )
        assert not result.passed
        assert result.expected == "2048,,4096"
        assert result.actual == "2048,,2048"

    def test_missing_env_var_fails(self):
        result = BaseSmoketest.assert_env_variant_list(
            {}, "VLLM_MAX_MODEL_LEN", [2048, 2048]
        )
        assert not result.passed
        assert result.actual == "not set"

    def test_single_variant_has_no_delimiter(self):
        """One variant renders as a bare scalar, not ``2048,,``."""
        env = {"VLLM_MAX_MODEL_LEN": "2048"}
        assert BaseSmoketest.assert_env_variant_list(
            env, "VLLM_MAX_MODEL_LEN", [2048]
        ).passed

    def test_omitted_key_renders_as_empty_segment(self):
        """The template maps the attribute with no default and the Jinja env is
        non-strict, so a variant missing the key stringifies to "" -- not the
        literal "None"."""
        env = {"VLLM_MAX_NUM_SEQ": "64,,"}
        assert BaseSmoketest.assert_env_variant_list(
            env, "VLLM_MAX_NUM_SEQ", [64, None]
        ).passed

    def test_message_mentions_variant_count(self):
        """The failure has to explain why a ,, list is expected, or the next
        reader diagnoses it as a rendering bug like we did."""
        env = {"VLLM_MAX_MODEL_LEN": "2048,,2048"}
        result = BaseSmoketest.assert_env_variant_list(
            env, "VLLM_MAX_MODEL_LEN", [2048, 4096]
        )
        assert "2 variants" in result.message
        assert "pod index" in result.message


# ---------------------------------------------------------------------------
# validate_role_pods gating
# ---------------------------------------------------------------------------


def _collect(monkeypatch, config: dict, env: dict, role: str = "decode"):
    """Run validate_role_pods against a synthetic pod and return
    {check_name: CheckResult} for the env checks it emitted."""
    from llmdbenchmark.smoketests.base import SmoketestReport

    smoketest = BaseSmoketest.__new__(BaseSmoketest)
    pod = {
        "metadata": {"name": f"test-{role}-0", "namespace": "test-ns"},
        "spec": {
            "nodeName": "node-0",
            "containers": [
                {
                    "name": "vllm",
                    # Auto-generated command form: flags reference the env vars.
                    "args": [
                        "vllm serve --max-model-len $VLLM_MAX_MODEL_LEN"
                        " --block-size $VLLM_BLOCK_SIZE"
                    ],
                    "env": [{"name": k, "value": v} for k, v in env.items()],
                    "resources": {},
                }
            ],
        },
    }
    monkeypatch.setattr(
        BaseSmoketest, "get_pod_specs", lambda self, *a, **kw: [pod], raising=True
    )
    report = SmoketestReport()
    smoketest.validate_role_pods(
        cmd=None,
        namespace="test-ns",
        config=config,
        role=role,
        model_short="test-model",
        report=report,
    )
    return {c.name: c for c in report.checks}


def _base_config(role_extra: dict) -> dict:
    return {
        "model": {"maxModelLen": 16384, "blockSize": 16},
        "vllmCommon": {"flags": {}},
        "decode": role_extra,
    }


class TestValidateRolePodsVariantGating:
    """Gating must mirror _macros.j2:253-279 exactly."""

    def test_variants_replace_scalar_max_model_len(self, monkeypatch):
        """The regression: model.maxModelLen is 16384 but variants say 2048,
        so 2048,,2048 in the spec is correct and must pass."""
        config = _base_config(
            {
                "replicas": 2,
                "contextLengthRanges": ["0-1000", "1000-2048"],
                "vllmVariants": [
                    {"maxModelLen": 2048, "maxNumSeq": 64},
                    {"maxModelLen": 2048, "maxNumSeq": 16},
                ],
            }
        )
        env = {
            "VLLM_MAX_MODEL_LEN": "2048,,2048",
            "VLLM_MAX_NUM_SEQ": "64,,16",
            "VLLM_BLOCK_SIZE": "16",
            "VLLM_IS_DECODE": "1",
        }
        checks = _collect(monkeypatch, config, env)
        assert checks["env_VLLM_MAX_MODEL_LEN"].passed
        assert checks["env_VLLM_MAX_NUM_SEQ"].passed

    def test_max_num_batched_tokens_not_checked_when_variants_omit_it(
        self, monkeypatch
    ):
        """The template only emits the ,, list when variants[0] declares the
        key; otherwise it falls back to the scalar default. Asserting the
        variant list here would fail against a legitimately scalar value."""
        config = _base_config(
            {
                "replicas": 2,
                "vllmVariants": [
                    {"maxModelLen": 2048, "maxNumSeq": 64},
                    {"maxModelLen": 2048, "maxNumSeq": 16},
                ],
            }
        )
        env = {
            "VLLM_MAX_MODEL_LEN": "2048,,2048",
            "VLLM_MAX_NUM_SEQ": "64,,16",
            # Scalar: no variant declared maxNumBatchedTokens.
            "VLLM_MAX_NUM_BATCHED_TOKENS": "256",
            "VLLM_IS_DECODE": "1",
        }
        checks = _collect(monkeypatch, config, env)
        assert "env_VLLM_MAX_NUM_BATCHED_TOKENS" not in checks

    def test_max_num_batched_tokens_checked_when_variants_declare_it(self, monkeypatch):
        config = _base_config(
            {
                "replicas": 2,
                "vllmVariants": [
                    {"maxModelLen": 2048, "maxNumBatchedTokens": 512},
                    {"maxModelLen": 2048, "maxNumBatchedTokens": 1024},
                ],
            }
        )
        env = {
            "VLLM_MAX_MODEL_LEN": "2048,,2048",
            "VLLM_MAX_NUM_BATCHED_TOKENS": "512,,1024",
            "VLLM_IS_DECODE": "1",
        }
        checks = _collect(monkeypatch, config, env)
        assert checks["env_VLLM_MAX_NUM_BATCHED_TOKENS"].passed

    def test_wrong_variant_list_still_fails(self, monkeypatch):
        """The fix must not turn into a blanket pass -- a genuinely wrong
        rendering has to be caught."""
        config = _base_config(
            {
                "replicas": 2,
                "vllmVariants": [
                    {"maxModelLen": 2048, "maxNumSeq": 64},
                    {"maxModelLen": 2048, "maxNumSeq": 16},
                ],
            }
        )
        env = {
            # Second pod's maxNumSeq lost -- e.g. a join bug in the template.
            "VLLM_MAX_NUM_SEQ": "64",
            "VLLM_MAX_MODEL_LEN": "2048,,2048",
            "VLLM_IS_DECODE": "1",
        }
        checks = _collect(monkeypatch, config, env)
        assert not checks["env_VLLM_MAX_NUM_SEQ"].passed

    def test_no_variants_keeps_scalar_behaviour(self, monkeypatch):
        """Every existing scenario must be unaffected."""
        config = _base_config({"replicas": 1})
        env = {
            "VLLM_MAX_MODEL_LEN": "16384",
            "VLLM_BLOCK_SIZE": "16",
            "VLLM_IS_DECODE": "1",
        }
        checks = _collect(monkeypatch, config, env)
        assert checks["env_VLLM_MAX_MODEL_LEN"].passed

    def test_no_variants_scalar_mismatch_still_fails(self, monkeypatch):
        config = _base_config({"replicas": 1})
        env = {
            "VLLM_MAX_MODEL_LEN": "4096",
            "VLLM_IS_DECODE": "1",
        }
        checks = _collect(monkeypatch, config, env)
        assert not checks["env_VLLM_MAX_MODEL_LEN"].passed

    def test_custom_command_skips_variant_checks(self, monkeypatch):
        """With a customCommand the auto-generated flags don't apply, so the
        whole block (variant checks included) is skipped."""
        config = _base_config(
            {
                "replicas": 2,
                "vllm": {"customCommand": ["vllm", "serve", "--max-model-len", "999"]},
                "vllmVariants": [
                    {"maxModelLen": 2048, "maxNumSeq": 64},
                    {"maxModelLen": 2048, "maxNumSeq": 16},
                ],
            }
        )
        env = {"VLLM_MAX_MODEL_LEN": "2048,,2048", "VLLM_IS_DECODE": "1"}
        checks = _collect(monkeypatch, config, env)
        assert "env_VLLM_MAX_MODEL_LEN" not in checks


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
