"""Tests for `RenderPlans._hoist_modelservice_sections`.

Scenarios may nest method-specific sections under `modelservice:` to document
their scope. Every template, resolver, and standup step reads these as
TOP-LEVEL keys, so the renderer hoists them back to the top level before the
resolver chain runs.

The contract this module pins:

- Nested modelservice-only sections are lifted to the
  top level and removed from `modelservice` (single home in the resolved config).
- The nested block deep-merges ON TOP OF whatever top-level block exists
  (defaults.yaml base, or a flat scenario override) -- nested wins, and
  untouched sibling keys survive.
- A flat-only scenario is untouched (no-op), so existing scenarios render
  identically.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from llmdbenchmark.parser.render_plans import RenderPlans

_REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def renderer():
    """Bypass __init__ -- we only need the pure logic under test."""
    r = RenderPlans.__new__(RenderPlans)
    r.logger = MagicMock()
    return r


class TestHoist:
    def test_nested_sections_are_hoisted_and_popped(self, renderer):
        values = {
            "modelservice": {
                "enabled": True,
                "gateway": {"className": "epponly"},
                "routing": {"connector": "nixlv2"},
                "router": {"epp": {"replicas": 2}},
                "httpRoute": {"requestTimeout": "300s"},
                "prefill": {"replicas": 2},
                "decode": {"replicas": 4},
                "inferenceExtension": {"enabled": True},
                "multinode": {"enabled": True},
            },
        }
        out = renderer._hoist_modelservice_sections(values)

        # Lifted to the top level.
        assert out["gateway"] == {"className": "epponly"}
        assert out["routing"] == {"connector": "nixlv2"}
        assert out["router"] == {"epp": {"replicas": 2}}
        assert out["httpRoute"] == {"requestTimeout": "300s"}
        assert out["prefill"] == {"replicas": 2}
        assert out["decode"] == {"replicas": 4}
        assert out["inferenceExtension"] == {"enabled": True}
        assert out["multinode"] == {"enabled": True}

        # Removed from modelservice; the toggle survives.
        assert out["modelservice"] == {"enabled": True}

    def test_nested_wins_over_existing_top_level(self, renderer):
        """Nested block deep-merges on top of the (defaults) top-level block:
        nested keys win, untouched sibling keys survive."""
        values = {
            "gateway": {"className": "istio", "namespace": "llmdbench"},
            "router": {"epp": {"replicas": 1, "image": "base"}},
            "modelservice": {
                "enabled": True,
                "gateway": {"className": "epponly"},
                "router": {"epp": {"replicas": 4}},
            },
        }
        out = renderer._hoist_modelservice_sections(values)

        # className overridden by nested; namespace preserved from base.
        assert out["gateway"] == {"className": "epponly", "namespace": "llmdbench"}
        # replicas overridden by nested; image preserved from base (deep merge).
        assert out["router"] == {"epp": {"replicas": 4, "image": "base"}}
        assert "gateway" not in out["modelservice"]
        assert "router" not in out["modelservice"]

    def test_flat_only_scenario_is_untouched(self, renderer):
        """No nested keys -> exact no-op, so flat scenarios render identically."""
        values = {
            "gateway": {"className": "epponly"},
            "router": {"epp": {"replicas": 1}},
            "routing": {"connector": "nixlv2"},
            "modelservice": {"enabled": True},
        }
        before = {k: dict(v) if isinstance(v, dict) else v for k, v in values.items()}
        out = renderer._hoist_modelservice_sections(values)
        assert out == before

    def test_partial_nesting_only_hoists_present_keys(self, renderer):
        """Only `router` nested; `gateway` stays flat and is left alone."""
        values = {
            "gateway": {"className": "epponly"},
            "modelservice": {
                "enabled": True,
                "router": {"epp": {"replicas": 3}},
            },
        }
        out = renderer._hoist_modelservice_sections(values)
        assert out["gateway"] == {"className": "epponly"}
        assert out["router"] == {"epp": {"replicas": 3}}
        assert out["modelservice"] == {"enabled": True}

    def test_missing_modelservice_is_noop(self, renderer):
        values = {"gateway": {"className": "epponly"}}
        out = renderer._hoist_modelservice_sections(values)
        assert out == {"gateway": {"className": "epponly"}}

    def test_modelservice_not_a_dict_is_noop(self, renderer):
        values = {"modelservice": None, "gateway": {"className": "epponly"}}
        out = renderer._hoist_modelservice_sections(values)
        assert out == {"modelservice": None, "gateway": {"className": "epponly"}}


class TestHoistOrderingIntegration:
    """End-to-end guard for the hoist-vs-setup_overrides ordering.

    Regression: the hoist must run BEFORE setup overrides are merged, so a
    DoE treatment / CLI override on the TOP-LEVEL dotted path (e.g.
    ``router.epp.pluginsConfigFile``) still wins over a scenario's nested
    ``modelservice.router`` block. If the hoist ran after setup overrides,
    the nested scenario value would silently clobber the treatment and break
    experiment sweeps (see experiments/precise-prefix-cache-aware.yaml).

    Renders the real precise-prefix-cache-routing guide (whose router block
    is nested under modelservice) with dry-run resolvers (no cluster).
    """

    _SCENARIO = _REPO / "config/scenarios/guides/precise-prefix-cache-routing.yaml"

    def _plugins_config_file(self, tmp_path, setup_overrides=None):
        """Render and return the resolved ``router.epp.pluginsConfigFile``."""
        import yaml
        from llmdbenchmark.parser.version_resolver import VersionResolver
        from llmdbenchmark.parser.cluster_resource_resolver import (
            ClusterResourceResolver,
        )

        log = MagicMock()
        res = RenderPlans(
            template_dir=_REPO / "config/templates/jinja",
            defaults_file=_REPO / "config/templates/values/defaults.yaml",
            scenarios_file=self._SCENARIO,
            output_dir=tmp_path,
            logger=log,
            version_resolver=VersionResolver(logger=log, dry_run=True),
            cluster_resource_resolver=ClusterResourceResolver(logger=log, dry_run=True),
            setup_overrides=setup_overrides,
        ).eval()
        assert not res.has_errors, res.to_dict()
        rendered = list(tmp_path.rglob("12_router-values.yaml"))
        assert rendered, "12_router-values.yaml was not rendered"
        doc = yaml.safe_load(rendered[0].read_text())
        # The pluginsConfigFile *selects* which config file to use; it is a
        # distinct field from the pluginsCustomConfig blob (whose map key
        # happens to share the filename), so assert on it precisely.
        return doc["router"]["epp"]["pluginsConfigFile"]

    def test_scenario_nested_value_used_without_override(self, tmp_path):
        # The scenario nests router.epp.pluginsConfigFile under modelservice;
        # with no treatment override, that value must survive the hoist.
        assert (
            self._plugins_config_file(tmp_path)
            == "precise-prefix-cache-routing-plugins.yaml"
        )

    def test_top_level_treatment_override_wins_over_nested(self, tmp_path):
        # A DoE treatment / CLI override on the top-level dotted path must win
        # over the scenario's nested modelservice.router value.
        sentinel = "TREATMENT-WINS.yaml"
        assert (
            self._plugins_config_file(
                tmp_path,
                setup_overrides={"router": {"epp": {"pluginsConfigFile": sentinel}}},
            )
            == sentinel
        )
