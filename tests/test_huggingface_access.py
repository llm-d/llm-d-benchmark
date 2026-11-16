"""Tests for the HuggingFace gated-model access check run before standup.

The Hub returns a gated repo's metadata to any caller, with or without a
token that was granted access, so ``model_info`` cannot tell an authorized
token from an unauthorized one. The fake Hub below behaves that way:
``model_info`` always succeeds, and only ``auth_check`` enforces the gate.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from huggingface_hub.utils import (
    GatedRepoError,
    HfHubHTTPError,
    RepositoryNotFoundError,
)

from llmdbenchmark.utilities import huggingface as hf
from llmdbenchmark.utilities.huggingface import (
    AccessStatus,
    GatedStatus,
    check_model_access,
    user_has_model_access,
)

GATED_MODEL = "meta-llama/Llama-3.1-8B"
GRANTED_TOKEN = "hf_granted"


def _http_error(cls, status_code: int):
    return cls(f"HTTP {status_code}", response=MagicMock(status_code=status_code))


@pytest.fixture
def fake_hub(monkeypatch):
    """Patch the Hub calls; ``fake_hub.auth_error`` overrides the gate check."""
    state = SimpleNamespace(auth_error=None, auth_calls=[])

    def fake_model_info(model_id, token=None):
        return SimpleNamespace(id=model_id, gated="manual")

    def fake_auth_check(model_id, token=None):
        state.auth_calls.append((model_id, token))
        if state.auth_error is not None:
            raise state.auth_error
        if token != GRANTED_TOKEN:
            raise _http_error(GatedRepoError, 403)

    monkeypatch.setattr(hf, "hf_model_info", fake_model_info)
    monkeypatch.setattr(hf, "auth_check", fake_auth_check)
    return state


class TestUserHasModelAccess:
    def test_token_without_access_is_unauthorized(self, fake_hub):
        status = user_has_model_access(GATED_MODEL, "hf_not_granted")
        assert status is AccessStatus.UNAUTHORIZED
        assert fake_hub.auth_calls == [(GATED_MODEL, "hf_not_granted")]

    def test_token_with_access_is_authorized(self, fake_hub):
        status = user_has_model_access(GATED_MODEL, GRANTED_TOKEN)
        assert status is AccessStatus.AUTHORIZED

    @pytest.mark.parametrize("status_code", [401, 403])
    def test_rejected_token_is_unauthorized(self, fake_hub, status_code):
        fake_hub.auth_error = _http_error(HfHubHTTPError, status_code)
        status = user_has_model_access(GATED_MODEL, GRANTED_TOKEN)
        assert status is AccessStatus.UNAUTHORIZED

    def test_missing_repo_is_unauthorized(self, fake_hub):
        fake_hub.auth_error = _http_error(RepositoryNotFoundError, 404)
        status = user_has_model_access(GATED_MODEL, GRANTED_TOKEN)
        assert status is AccessStatus.UNAUTHORIZED

    def test_server_error_is_inconclusive(self, fake_hub):
        fake_hub.auth_error = _http_error(HfHubHTTPError, 503)
        status = user_has_model_access(GATED_MODEL, GRANTED_TOKEN)
        assert status is AccessStatus.ERROR


class TestCheckModelAccess:
    def test_gated_model_without_access_fails(self, fake_hub):
        result = check_model_access(GATED_MODEL, "hf_not_granted")

        assert result.gated is GatedStatus.GATED
        assert result.access is AccessStatus.UNAUTHORIZED
        assert not result.ok
        assert f"https://huggingface.co/{GATED_MODEL}" in result.detail

    def test_gated_model_with_access_passes(self, fake_hub):
        result = check_model_access(GATED_MODEL, GRANTED_TOKEN)

        assert result.gated is GatedStatus.GATED
        assert result.access is AccessStatus.AUTHORIZED
        assert result.ok
