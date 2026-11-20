"""The restricted-scc RoleBinding must reference restricted-v2.

Namespace admins hold ``use`` on restricted-v2 (bound to
system:authenticated) but not on the old restricted SCC, so a binding
referencing the old name cannot be created without cluster-admin help.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import yaml

from llmdbenchmark.parser.cluster_resource_resolver import ClusterResourceResolver
from llmdbenchmark.parser.render_plans import RenderPlans

_REPO = Path(__file__).resolve().parents[1]
_SCENARIOS = _REPO / "config/scenarios/guides"


def test_restricted_scc_binding_uses_v2(tmp_path):
    version_resolver = MagicMock()
    version_resolver.resolve_all.side_effect = lambda values, **kwargs: values
    logger = MagicMock()

    result = RenderPlans(
        template_dir=_REPO / "config/templates/jinja",
        defaults_file=_REPO / "config/templates/values/defaults.yaml",
        scenarios_file=_SCENARIOS / "optimized-baseline.yaml",
        output_dir=tmp_path,
        logger=logger,
        version_resolver=version_resolver,
        cluster_resource_resolver=ClusterResourceResolver(logger=logger, dry_run=True),
    ).eval()

    assert not result.has_errors, result.to_dict()
    rendered_path = next(tmp_path.rglob("05_namespace_sa_rbac_secret.yaml"))
    rendered = list(yaml.safe_load_all(rendered_path.read_text(encoding="utf-8")))
    bindings = {
        doc["metadata"]["name"]: doc
        for doc in rendered
        if doc and doc.get("kind") == "RoleBinding"
    }
    assert "inference-perf-restricted-v2-scc" in bindings
    assert (
        bindings["inference-perf-restricted-v2-scc"]["roleRef"]["name"]
        == "system:openshift:scc:restricted-v2"
    )
