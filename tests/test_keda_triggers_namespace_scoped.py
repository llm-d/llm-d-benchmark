"""Every rendered eppKedaSaturation trigger is pinned to the stack's namespace.

On a cluster-wide store (OpenShift Thanos, or any Prometheus scraping several
namespaces) a query without a namespace label aggregates every EPP serving the
same model or pool name, so a guide's autoscaler would scale on another
tenant's traffic. This covers the defaults.yaml trigger list (inherited by
epp-keda-saturation and workload-autoscaling) and every guide that defines its
own.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from llmdbenchmark.parser.cluster_resource_resolver import ClusterResourceResolver
from llmdbenchmark.parser.render_plans import RenderPlans
from llmdbenchmark.parser.version_resolver import VersionResolver

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = PROJECT_ROOT / "config" / "templates" / "jinja"
DEFAULTS = PROJECT_ROOT / "config" / "templates" / "values" / "defaults.yaml"
GUIDES = PROJECT_ROOT / "config" / "scenarios" / "guides"

KEDA_GUIDES = [
    "epp-keda-saturation",
    "keda-epp-token-aware",
    "keda-epp-inflight",
    "keda-epp-token-aware-pd-disaggregation",
]

# EPP and vLLM series carry a namespace label; every selector on them must pin it.
SERIES = re.compile(r"(llm_d_epp_\w+|vllm:\w+)\{([^}]*)\}")


def _scaled_objects(tmp_path: Path, guide: str) -> list[dict]:
    logger = MagicMock()
    errors = RenderPlans(
        template_dir=TEMPLATES,
        defaults_file=DEFAULTS,
        scenarios_file=GUIDES / f"{guide}.yaml",
        output_dir=tmp_path / "out",
        logger=logger,
        version_resolver=VersionResolver(logger=logger, dry_run=True),
        cluster_resource_resolver=ClusterResourceResolver(logger=logger, dry_run=True),
    ).eval()
    assert not errors.has_errors, errors.to_dict()
    [rendered] = list((tmp_path / "out").rglob("30_keda-scaledobject*.yaml"))
    docs = yaml.safe_load_all(rendered.read_text())
    return [d for d in docs if d and d.get("kind") == "ScaledObject"]


@pytest.mark.parametrize("guide", KEDA_GUIDES)
def test_every_trigger_series_is_namespace_scoped(tmp_path, guide):
    scaled_objects = _scaled_objects(tmp_path, guide)
    assert scaled_objects
    for so in scaled_objects:
        namespace = so["metadata"]["namespace"]
        for trigger in so["spec"]["triggers"]:
            query = trigger["metadata"]["query"]
            selectors = SERIES.findall(query)
            assert selectors, f"{trigger['name']}: no EPP/vLLM series in {query!r}"
            for series, labels in selectors:
                assert f'namespace="{namespace}"' in labels, (
                    f"{guide}/{trigger['name']}: {series}{{{labels}}} is not pinned to "
                    f"namespace {namespace!r}"
                )
