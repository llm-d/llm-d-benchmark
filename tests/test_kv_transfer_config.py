"""build_kv_transfer_config(): the ``--kv-transfer-config`` JSON.

``vllmCommon.kvTransfer.extraTopLevel`` adds fields next to ``kv_connector``
and ``kv_role`` -- vLLM's ``KVTransferConfig`` has more top-level fields than
those two (e.g. ``kv_load_failure_policy``), which otherwise could only be
set by disabling ``kvTransfer`` and writing the whole JSON by hand. When it
is unset the output must not change, byte for byte.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from llmdbenchmark.parser.config_schema import KvTransferConfig
from llmdbenchmark.parser.render_plans import RenderPlans

_JINJA_DIR = Path(__file__).resolve().parent.parent / "config" / "templates" / "jinja"
_MACROS = (_JINJA_DIR / "_macros.j2").read_text(encoding="utf-8")

_BASE = {"enabled": True, "connector": "NixlConnector", "role": "kv_both"}


def _render(kv_transfer: dict[str, Any]) -> str:
    renderer = RenderPlans.__new__(RenderPlans)
    renderer.logger = MagicMock()
    renderer._jinja_env = None
    rendered = renderer._render_template(
        _MACROS + "\n{{ build_kv_transfer_config() }}",
        {"vllmCommon": {"kvTransfer": kv_transfer}},
    )
    return rendered.strip().splitlines()[-1]


@pytest.mark.parametrize("extra_top_level", [None, {}])
def test_unset_extra_top_level_keeps_output(extra_top_level) -> None:
    kv_transfer = dict(_BASE, extraTopLevel=extra_top_level)
    assert (
        _render(kv_transfer) == '{"kv_connector":"NixlConnector","kv_role":"kv_both"}'
    )
    assert _render(dict(kv_transfer, extraConfig={"num_cpu_blocks": 5000})) == (
        '{"kv_connector":"NixlConnector","kv_role":"kv_both",'
        '"kv_connector_extra_config":{"num_cpu_blocks":5000}}'
    )


def test_extra_top_level_fields_are_siblings_of_connector() -> None:
    rendered = _render(
        dict(
            _BASE,
            connector="MooncakeStoreConnector",
            extraTopLevel={"kv_load_failure_policy": "recompute", "kv_rank": 0},
        )
    )

    assert rendered == (
        '{"kv_connector":"MooncakeStoreConnector","kv_role":"kv_both",'
        '"kv_load_failure_policy":"recompute","kv_rank":0}'
    )


def test_extra_top_level_combines_with_extra_config() -> None:
    rendered = _render(
        dict(
            _BASE,
            extraTopLevel={"kv_load_failure_policy": "recompute"},
            extraConfig={"backends": ["nixl"]},
        )
    )

    assert json.loads(rendered) == {
        "kv_connector": "NixlConnector",
        "kv_role": "kv_both",
        "kv_load_failure_policy": "recompute",
        "kv_connector_extra_config": {"backends": ["nixl"]},
    }


def test_schema_accepts_extra_top_level() -> None:
    config = KvTransferConfig.model_validate(
        dict(_BASE, extraTopLevel={"kv_load_failure_policy": "recompute"})
    )
    assert config.extraTopLevel == {"kv_load_failure_policy": "recompute"}


@pytest.mark.parametrize(
    "key", ["kv_connector", "kv_role", "kv_connector_extra_config"]
)
def test_schema_rejects_keys_owned_by_other_fields(key) -> None:
    with pytest.raises(ValueError, match=key):
        KvTransferConfig.model_validate(dict(_BASE, extraTopLevel={key: "x"}))
