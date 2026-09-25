"""Tests for iterated `${VAR}` substitution in `GuideVariableResolver`.

Guides frequently store shell-fragment values in variables that themselves
reference other variables — the canonical case is:

    export ROUTER_BASE_VALUES="-f ${REPO_ROOT}/guides/recipes/router/base.values.yaml"
    export ROUTER_VALUES="-f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml"
    …
    helm install … ${ROUTER_BASE_VALUES} ${ROUTER_VALUES} …

A single `re.sub` pass on the helm command substitutes `${ROUTER_BASE_VALUES}`
but leaves the newly-emerged `${REPO_ROOT}` inside it untouched. These tests
lock in the iterative behaviour that fixes that.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from llmdbenchmark.kustomize.variable_resolver import GuideVariableResolver


# GuideVariableResolver runs `Path(repo_path).resolve()` on construction,
# which follows symlinks. On macOS `/tmp` resolves to `/private/tmp`, which
# would make the asserted paths platform-specific. Resolve once here so the
# expected values match whichever platform runs the tests.
_REPO_PATH = str(Path("/tmp/llmd-repo").resolve())


def _make_resolver(readme_variables: dict[str, str] | None = None):
    return GuideVariableResolver(
        guide_name="optimized-baseline",
        namespace="llm-d-ns",
        gaie_version="v1.6.0",
        repo_path=_REPO_PATH,
        readme_variables=readme_variables or {},
    )


class TestNestedSubstitution:
    def test_variable_value_containing_repo_root_is_resolved(self):
        resolver = _make_resolver(
            {
                "ROUTER_BASE_VALUES": "-f ${REPO_ROOT}/guides/recipes/router/base.values.yaml",
            }
        )
        out = resolver.resolve("helm install foo ${ROUTER_BASE_VALUES} -n ns")
        assert (
            out
            == f"helm install foo -f {_REPO_PATH}/guides/recipes/router/base.values.yaml -n ns"
        )

    def test_variable_value_containing_multiple_refs(self):
        resolver = _make_resolver(
            {
                "ROUTER_VALUES": "-f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml",
            }
        )
        out = resolver.resolve("helm install foo ${ROUTER_VALUES} -n ns")
        assert (
            out
            == f"helm install foo -f {_REPO_PATH}/guides/optimized-baseline/router/optimized-baseline.values.yaml -n ns"
        )

    def test_three_levels_deep(self):
        resolver = _make_resolver(
            {
                "OUTER": "prefix/${MIDDLE}",
                "MIDDLE": "layer${REPO_ROOT}/suffix",
            }
        )
        out = resolver.resolve("path=${OUTER}")
        assert out == f"path=prefix/layer{_REPO_PATH}/suffix"

    def test_unset_variable_left_as_literal(self):
        resolver = _make_resolver()
        out = resolver.resolve("helm install ${DOES_NOT_EXIST} foo")
        assert out == "helm install ${DOES_NOT_EXIST} foo"

    def test_no_change_when_no_placeholders(self):
        resolver = _make_resolver()
        out = resolver.resolve("kubectl get pods")
        # `_absolutise_paths` still runs; but there are no `guides/…` paths
        # here so the string should pass through unchanged.
        assert out == "kubectl get pods"

    def test_cycle_bounded_and_falls_back_to_literal(self):
        """A cyclic reference must not hang. After the round cap, unresolved
        `${VAR}` refs remain as literals rather than infinite recursion."""
        resolver = _make_resolver({"A": "${B}", "B": "${A}"})
        out = resolver.resolve("value=${A}")
        # The cycle is bounded — the string still contains a ${…} literal,
        # but the call returns (doesn't hang).
        assert "${" in out


class TestOptimizedBaselineRegression:
    """The exact shape that surfaced the bug in CI — reproduce it end-to-end."""

    def test_full_helm_command_resolves_completely(self):
        # These four variables are what the fork's optimized-baseline guide.yaml
        # exports; the CI log showed them harvested (post-regex-fix) but the
        # `${REPO_ROOT}` inside them still unresolved after a single substitution
        # pass, leading to `-f /guides/…` in the helm invocation.
        readme_vars = {
            "GUIDE_NAME": "optimized-baseline",
            "REPO_ROOT": "/home/runner/work/llm-d/llm-d",  # will be overridden below
            "ROUTER_BASE_VALUES": "-f ${REPO_ROOT}/guides/recipes/router/base.values.yaml",
            "ROUTER_VALUES": "-f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml",
            "MONITORING_VALUES": "",
        }
        resolver = GuideVariableResolver(
            guide_name="optimized-baseline",
            namespace="llm-d-nightly-optimized-baseline-gke-gpu",
            gaie_version="v1.6.0",
            repo_path="/home/runner/work/llm-d/llm-d",
            readme_variables=readme_vars,
            variable_overrides={
                "INFRA_PROVIDER": "gke",
                "GUIDE_NAME": "optimized-baseline",
                "NAMESPACE": "llm-d-nightly-optimized-baseline-gke-gpu",
                "REPO_ROOT": "/home/runner/work/llm-d/llm-d",
                "ROUTER_CHART_VERSION": "v0.9.0",
            },
        )
        raw = (
            "helm install ${GUIDE_NAME} ${ROUTER_STANDALONE_CHART} "
            "${ROUTER_BASE_VALUES} ${MONITORING_VALUES} ${ROUTER_VALUES} "
            "-n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}"
        )
        out = resolver.resolve(raw)
        # No `${…}` should remain in the final helm command.
        assert "${" not in out, f"unresolved placeholder in: {out}"
        # And the `-f` values must reference full absolute paths.
        assert (
            "-f /home/runner/work/llm-d/llm-d/guides/recipes/router/base.values.yaml"
            in out
        )
        assert (
            "-f /home/runner/work/llm-d/llm-d/guides/optimized-baseline/router/optimized-baseline.values.yaml"
            in out
        )


class TestInfraProviderSubpathHandling:
    def test_preserves_nested_infra_provider_when_no_topology_in_readme(self):
        resolver = GuideVariableResolver(
            guide_name="pd-disaggregation",
            namespace="llm-d-pd",
            gaie_version="v1.6.1",
            repo_path=_REPO_PATH,
            readme_variables={"INFRA_PROVIDER": "base"},
            variable_overrides={"INFRA_PROVIDER": "gke/base"},
        )
        out = resolver.resolve(
            "kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/${INFRA_PROVIDER}"
        )
        assert (
            out
            == f"kubectl apply -n llm-d-pd -k {_REPO_PATH}/guides/pd-disaggregation/modelserver/gpu/vllm/gke/base"
        )

    def test_splits_infra_provider_into_topology_when_readme_defines_topology(self):
        resolver = GuideVariableResolver(
            guide_name="multimodal-serving/e-disaggregation",
            namespace="llm-d-mm",
            gaie_version="v1.6.1",
            repo_path=_REPO_PATH,
            readme_variables={
                "GUIDE_PATH": "multimodal-serving/e-disaggregation",
                "TOPOLOGY": "e-pd",
                "INFRA_PROVIDER": "gke",
                "MODEL_SERVER_PATH": "${REPO_ROOT}/guides/${GUIDE_PATH}/modelserver/gpu/vllm/${TOPOLOGY}/${INFRA_PROVIDER}",
            },
            variable_overrides={"INFRA_PROVIDER": "e-p-d/gke"},
        )
        out = resolver.resolve("kubectl apply -n ${NAMESPACE} -k ${MODEL_SERVER_PATH}")
        assert (
            out
            == f"kubectl apply -n llm-d-mm -k {_REPO_PATH}/guides/multimodal-serving/e-disaggregation/modelserver/gpu/vllm/e-p-d/gke"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
