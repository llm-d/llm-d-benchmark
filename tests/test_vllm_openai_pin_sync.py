from __future__ import annotations

import re
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULTS_PATH = PROJECT_ROOT / "config" / "templates" / "values" / "defaults.yaml"
INSTALL_SH_PATH = PROJECT_ROOT / "install.sh"
UPSTREAM_VERSIONS_PATH = PROJECT_ROOT / "docs" / "upstream-versions.md"


def _doc_pin_for(dependency: str) -> str:
    pattern = rf"^\| \*\*{re.escape(dependency)}\*\* \| `([^`]+)` \|"
    for line in UPSTREAM_VERSIONS_PATH.read_text(encoding="utf-8").splitlines():
        match = re.match(pattern, line)
        if match:
            return match.group(1)
    raise AssertionError(f"Did not find {dependency} in {UPSTREAM_VERSIONS_PATH}")


def test_vllm_and_vllm_openai_pins_stay_in_sync():
    defaults = yaml.safe_load(DEFAULTS_PATH.read_text(encoding="utf-8"))

    vllm_pin = defaults["_anchors"]["vllm-openai_version"]
    uds_tokenizer_pin = defaults["_anchors"]["llm-d-uds-tokenizer_version"]

    assert defaults["images"]["vllm"]["tag"] == vllm_pin
    assert defaults["images"]["vllmOpenai"]["tag"] == vllm_pin
    assert _doc_pin_for("vllm") == vllm_pin
    assert _doc_pin_for("vllmOpenai") == vllm_pin

    assert defaults["images"]["udsTokenizer"]["tag"] == uds_tokenizer_pin
    assert _doc_pin_for("udsTokenizer") == uds_tokenizer_pin


def test_oc_pin_stays_in_sync():
    match = re.search(
        r'^\s*oc\)\s+echo\s+"([^"]+)"\s+;;',
        INSTALL_SH_PATH.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert match is not None
    assert _doc_pin_for("oc") == match.group(1)
