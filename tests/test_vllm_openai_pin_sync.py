from __future__ import annotations

import re
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULTS_PATH = PROJECT_ROOT / "config" / "templates" / "values" / "defaults.yaml"
UPSTREAM_VERSIONS_PATH = PROJECT_ROOT / "docs" / "upstream-versions.md"
CPU_SCENARIO_PATH = PROJECT_ROOT / "config" / "scenarios" / "examples" / "cpu.yaml"
BUILD_DOCKERFILE_PATH = PROJECT_ROOT / "build" / "Dockerfile"


def _doc_pin_for(dependency: str) -> str:
    pattern = rf"^\| \*\*{re.escape(dependency)}\*\* \| `([^`]+)` \|"
    for line in UPSTREAM_VERSIONS_PATH.read_text(encoding="utf-8").splitlines():
        match = re.match(pattern, line)
        if match:
            return match.group(1)
    raise AssertionError(f"Did not find {dependency} in {UPSTREAM_VERSIONS_PATH}")


def _docker_arg_value(content: str, name: str) -> str:
    match = re.search(rf"^ARG {re.escape(name)}=(\S+)$", content, re.MULTILINE)
    if match:
        return match.group(1)
    raise AssertionError(f"Did not find ARG {name} in {BUILD_DOCKERFILE_PATH}")


def test_vllm_and_vllm_openai_pins_stay_in_sync():
    defaults = yaml.safe_load(DEFAULTS_PATH.read_text(encoding="utf-8"))
    cpu_scenario = yaml.safe_load(CPU_SCENARIO_PATH.read_text(encoding="utf-8"))
    dockerfile = BUILD_DOCKERFILE_PATH.read_text(encoding="utf-8")

    vllm_pin = defaults["_anchors"]["vllm-openai_version"]
    uds_tokenizer_pin = defaults["_anchors"]["llm-d-uds-tokenizer_version"]

    assert defaults["images"]["vllm"]["tag"] == vllm_pin
    assert defaults["images"]["vllmOpenai"]["tag"] == vllm_pin
    assert _doc_pin_for("vllm") == vllm_pin
    assert _doc_pin_for("vllmOpenai") == vllm_pin
    scenarios = cpu_scenario["scenario"]
    assert scenarios, f"No scenarios defined in {CPU_SCENARIO_PATH}"
    for scenario in scenarios:
        cpu_images = scenario["common"]["images"]
        assert cpu_images["vllm"]["tag"] == vllm_pin
        assert cpu_images["vllmOpenai"]["tag"] == vllm_pin

    assert _docker_arg_value(dockerfile, "VLLM_BENCHMARK_BRANCH") == vllm_pin

    assert defaults["images"]["udsTokenizer"]["tag"] == uds_tokenizer_pin
    assert _doc_pin_for("udsTokenizer") == uds_tokenizer_pin


def test_agentgateway_pin_stays_in_sync_with_upstream_versions_doc():
    defaults = yaml.safe_load(DEFAULTS_PATH.read_text(encoding="utf-8"))

    agentgateway_pin = defaults["_anchors"]["agentgateway_version"]

    assert defaults["chartVersions"]["agentgateway"] == agentgateway_pin
    assert _doc_pin_for("agentgateway") == agentgateway_pin


def test_inference_pool_pin_stays_in_sync_with_defaults_and_doc():
    defaults = yaml.safe_load(DEFAULTS_PATH.read_text(encoding="utf-8"))

    gaie_pin = defaults["_anchors"]["gaie_version"]

    assert defaults["chartVersions"]["inferencePool"] == gaie_pin
    assert defaults["gatewayApiCrd"]["inferenceExtensionRevision"] == gaie_pin
    assert _doc_pin_for("inferencePool") == gaie_pin
