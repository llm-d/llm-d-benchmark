"""``lws.enabled: true`` must force ``multinode.enabled: true``."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from llmdbenchmark.parser.render_plans import RenderPlans


@pytest.fixture
def renderer():
    r = RenderPlans.__new__(RenderPlans)
    r.logger = MagicMock()
    return r


def test_lws_enabled_forces_multinode(renderer):
    values = {"lws": {"enabled": True}, "multinode": {"enabled": False}}
    result = renderer._resolve_lws_multinode(values)
    assert result["multinode"]["enabled"] is True
    assert values["multinode"]["enabled"] is False  # input not mutated


def test_lws_enabled_creates_missing_multinode_block(renderer):
    result = renderer._resolve_lws_multinode({"lws": {"enabled": True}})
    assert result["multinode"] == {"enabled": True}


def test_lws_enabled_preserves_other_multinode_keys(renderer):
    values = {"lws": {"enabled": True}, "multinode": {"enabled": False, "x": 1}}
    result = renderer._resolve_lws_multinode(values)
    assert result["multinode"] == {"enabled": True, "x": 1}


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"lws": {"enabled": False}, "multinode": {"enabled": False}},
        {"lws": None},
    ],
)
def test_lws_disabled_leaves_multinode_untouched(renderer, values):
    assert renderer._resolve_lws_multinode(values) is values


def test_multinode_already_enabled_is_noop(renderer):
    values = {"lws": {"enabled": True}, "multinode": {"enabled": True}}
    assert renderer._resolve_lws_multinode(values) is values
