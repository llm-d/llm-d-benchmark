"""Tests for FMA controller Helm release matching during teardown.

Regression coverage for: a full-scenario teardown reporting success while
leaving the FMA controllers Helm release (``<model_id_label>-fma-dp``)
behind, so the next same-model standup no-ops on it and the controller pod
never redeploys.

``UninstallHelmStep._release_matches`` identifies a release three ways:
by ``--release`` substring, by the model labels re-derived at teardown
time, and -- on a full-scenario teardown only -- by chart identity. The
first is cosmetic on the FMA path (the release is keyed on the model
label, not on ``release``) and the second only fires when teardown is
given the same model flags as standup. That leaves chart identity as the
only safety net for a release whose chart is model-agnostic, and
``fma-controllers`` was missing from ``_MANAGED_CHART_PREFIXES``.

The last test here is the one that keeps this honest over time: rather
than hard-coding the chart name a second time, it reads the chart this
tool actually installs out of the shipped ``defaults.yaml`` and asserts
the prefix list covers it. Repointing ``fma.chart.url`` at a differently
named chart fails that test instead of silently reopening this bug.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from llmdbenchmark.teardown.steps.step_01_uninstall_helm import UninstallHelmStep

# What `helm list -o json` reports in `chart` for the release created by
# config/templates/jinja/26_helmfile-fma-controllers.yaml.j2: the chart's
# own name and version, not the OCI reference it was pulled from.
FMA_CHART = "fma-controllers-0.6.4"

DEFAULTS_YAML = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "templates"
    / "values"
    / "defaults.yaml"
)


class TestFmaReleaseMatchesByChartIdentity:
    """UninstallHelmStep._release_matches on the FMA controllers release."""

    def test_full_teardown_matches_fma_chart_despite_label_mismatch(self):
        """The bug: teardown was run without re-supplying the model flags,
        so the re-derived model label does not match the one standup used
        and the ``--release`` substring never appears in an FMA release
        name. Only chart identity can catch it."""
        assert UninstallHelmStep._release_matches(
            "qwen-qwe-04b6bb85-fma-dp",
            "llmdbench",
            ["some-other-model-label"],
            FMA_CHART,
            full_teardown=True,
        )

    def test_model_label_match_still_works_when_flags_are_symmetric(self):
        """Nightlies pass the model flags to both standup and teardown,
        which is why they never hit this; that path must keep working
        without relying on the chart fallback."""
        assert UninstallHelmStep._release_matches(
            "qwen-qwe-04b6bb85-fma-dp",
            "llmdbench",
            ["qwen-qwe-04b6bb85"],
            FMA_CHART,
            full_teardown=False,
        )

    def test_partial_stack_teardown_does_not_use_chart_fallback(self):
        """A --stack-filtered teardown must not sweep up a sibling stack's
        FMA release in the same namespace just because it shares a chart."""
        assert not UninstallHelmStep._release_matches(
            "sibling-model-fma-dp",
            "llmdbench",
            ["some-other-model-label"],
            FMA_CHART,
            full_teardown=False,
        )

    def test_unrelated_chart_named_after_fma_does_not_match(self):
        """Control: broadening the list must not turn every release
        mentioning FMA into ours. ``startswith`` is the contract."""
        assert not UninstallHelmStep._release_matches(
            "someone-elses-fma-dp",
            "llmdbench",
            ["some-other-model-label"],
            "third-party-fma-controllers-1.0.0",
            full_teardown=True,
        )


class TestManagedChartPrefixesCoverShippedFmaChart:
    """The prefix list must cover the chart this tool actually installs."""

    def test_shipped_fma_chart_url_is_covered_by_a_managed_prefix(self):
        defaults = yaml.safe_load(DEFAULTS_YAML.read_text(encoding="utf-8"))
        chart_url = defaults["fma"]["chart"]["url"]
        chart_name = chart_url.rsplit("/", 1)[-1]
        version = str(defaults["fma"]["chart"]["version"])

        # `helm list` reports "<chart name>-<version>".
        assert f"{chart_name}-{version}".startswith(
            UninstallHelmStep._MANAGED_CHART_PREFIXES
        ), (
            f"fma.chart.url resolves to chart {chart_name!r}, which no entry "
            f"in _MANAGED_CHART_PREFIXES matches; a full teardown would leave "
            f"the FMA controllers release behind while reporting success"
        )
