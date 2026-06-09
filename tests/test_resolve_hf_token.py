"""Tests for ``RenderPlans._resolve_hf_token`` env-var fallback chain.

Pins the contract that:

- All three env vars (``HF_TOKEN``, ``LLMDBENCH_HF_TOKEN``,
  ``HUGGING_FACE_HUB_TOKEN``) trigger ``huggingface.enabled=True``
  and inject the token + its base64 form.
- ``HF_TOKEN`` wins when multiple are set.
- With none of them set, ``huggingface.enabled`` is forced to
  ``False`` and the token / base64 fields are zeroed out so the
  rendered Secret YAML and harness-pod env block are both skipped.

The chain mirrors what ``_ensure_hf_token_secret`` (kustomize-mode
secret enforcement) and ``step_03_detect_endpoint`` already honour --
this test guards against future drift between those code paths.
"""

from __future__ import annotations

import base64
from unittest.mock import MagicMock

import pytest

from llmdbenchmark.parser.render_plans import RenderPlans


@pytest.fixture
def renderer():
    """Build a RenderPlans instance with a mocked logger only.

    Mirrors the fixture in ``test_config_variable_substitution.py`` --
    we want to exercise the pure-Python helper without paying for the
    template/scenario disk reads ``__init__`` does.
    """
    logger = MagicMock()
    logger.log_info = MagicMock()
    logger.log_warning = MagicMock()
    r = RenderPlans.__new__(RenderPlans)
    r.logger = logger
    return r


def _clear_hf_env(monkeypatch):
    """Strip every env var the resolver consults so each test starts clean."""
    for var in ("HF_TOKEN", "LLMDBENCH_HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        monkeypatch.delenv(var, raising=False)


class TestResolveHfToken:
    """Pin the three-env-var fallback chain plus the no-token case."""

    def test_hf_token_env_var_wins(self, renderer, monkeypatch):
        _clear_hf_env(monkeypatch)
        monkeypatch.setenv("HF_TOKEN", "hf_from_plain_var")

        values = {"huggingface": {"token": ""}}
        result = renderer._resolve_hf_token(values)

        assert result["huggingface"]["enabled"] is True
        assert result["huggingface"]["token"] == "hf_from_plain_var"
        assert result["huggingface"]["tokenBase64"] == base64.b64encode(
            b"hf_from_plain_var"
        ).decode("utf-8")

    def test_llmdbench_hf_token_env_var_picked_up(self, renderer, monkeypatch):
        """The CI/project-prefixed name was previously a silent gap."""
        _clear_hf_env(monkeypatch)
        monkeypatch.setenv("LLMDBENCH_HF_TOKEN", "hf_from_llmdbench_var")

        values = {"huggingface": {"token": ""}}
        result = renderer._resolve_hf_token(values)

        assert result["huggingface"]["enabled"] is True
        assert result["huggingface"]["token"] == "hf_from_llmdbench_var"
        assert result["huggingface"]["tokenBase64"] == base64.b64encode(
            b"hf_from_llmdbench_var"
        ).decode("utf-8")

    def test_hugging_face_hub_token_env_var_picked_up(self, renderer, monkeypatch):
        _clear_hf_env(monkeypatch)
        monkeypatch.setenv("HUGGING_FACE_HUB_TOKEN", "hf_from_sdk_var")

        values = {"huggingface": {"token": ""}}
        result = renderer._resolve_hf_token(values)

        assert result["huggingface"]["enabled"] is True
        assert result["huggingface"]["token"] == "hf_from_sdk_var"

    def test_hf_token_wins_over_llmdbench_hf_token(self, renderer, monkeypatch):
        """Precedence: HF_TOKEN > LLMDBENCH_HF_TOKEN > HUGGING_FACE_HUB_TOKEN."""
        _clear_hf_env(monkeypatch)
        monkeypatch.setenv("HF_TOKEN", "hf_winning")
        monkeypatch.setenv("LLMDBENCH_HF_TOKEN", "hf_should_lose")
        monkeypatch.setenv("HUGGING_FACE_HUB_TOKEN", "hf_should_also_lose")

        values = {"huggingface": {"token": ""}}
        result = renderer._resolve_hf_token(values)

        assert result["huggingface"]["token"] == "hf_winning"

    def test_llmdbench_hf_token_wins_over_hugging_face_hub_token(
        self, renderer, monkeypatch
    ):
        _clear_hf_env(monkeypatch)
        monkeypatch.setenv("LLMDBENCH_HF_TOKEN", "hf_llmdbench")
        monkeypatch.setenv("HUGGING_FACE_HUB_TOKEN", "hf_should_lose")

        values = {"huggingface": {"token": ""}}
        result = renderer._resolve_hf_token(values)

        assert result["huggingface"]["token"] == "hf_llmdbench"

    def test_no_env_vars_disables_huggingface(self, renderer, monkeypatch):
        """With nothing set, the harness pod's HF env block is skipped
        and the rendered Secret is omitted -- public-model standups
        keep working, gated-model standups fail at the access check."""
        _clear_hf_env(monkeypatch)

        values = {"huggingface": {"token": ""}}
        result = renderer._resolve_hf_token(values)

        assert result["huggingface"]["enabled"] is False
        assert result["huggingface"]["token"] == ""
        assert result["huggingface"]["tokenBase64"] == ""

    def test_pre_set_non_sentinel_token_short_circuits_env_lookup(
        self, renderer, monkeypatch
    ):
        """If the scenario already pinned a real token, don't override it."""
        _clear_hf_env(monkeypatch)
        monkeypatch.setenv("HF_TOKEN", "hf_from_env_should_not_be_used")

        values = {"huggingface": {"token": "hf_scenario_pinned"}}
        result = renderer._resolve_hf_token(values)

        # Token preserved, enabled flipped on, no base64 added (the
        # short-circuit branch doesn't touch base64 -- the rendered
        # Secret template handles it from `token` if needed).
        assert result["huggingface"]["enabled"] is True
        assert result["huggingface"]["token"] == "hf_scenario_pinned"

    def test_sentinel_token_treated_as_empty(self, renderer, monkeypatch):
        """REPLACE_TOKEN is a sentinel -- env lookup should still run."""
        _clear_hf_env(monkeypatch)
        monkeypatch.setenv("LLMDBENCH_HF_TOKEN", "hf_from_env")

        values = {"huggingface": {"token": "REPLACE_TOKEN"}}
        result = renderer._resolve_hf_token(values)

        assert result["huggingface"]["enabled"] is True
        assert result["huggingface"]["token"] == "hf_from_env"

    def test_original_values_dict_is_not_mutated(self, renderer, monkeypatch):
        """The helper deepcopies, so the caller's dict stays untouched."""
        _clear_hf_env(monkeypatch)
        monkeypatch.setenv("HF_TOKEN", "hf_anything")

        values = {"huggingface": {"token": "", "secretName": "should-survive"}}
        result = renderer._resolve_hf_token(values)

        assert values["huggingface"]["token"] == ""
        assert values["huggingface"].get("enabled") is None
        assert result is not values


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
