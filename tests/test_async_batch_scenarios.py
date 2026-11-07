"""Template and cross-repo scenario rendering tests for KEDA async-batch (Baseline, M1, M2)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from llmdbenchmark.parser.config_schema import validate_config
from llmdbenchmark.parser.render_plans import RenderPlans
from llmdbenchmark.parser.render_specification import RenderSpecification
from llmdbenchmark.utilities.profile_renderer import build_env_map, render_profile

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AUTOSCALING_ROOT = PROJECT_ROOT.parent / "llm-d-autoscaling"
TEMPLATE_DIR = PROJECT_ROOT / "config" / "templates" / "jinja"
DEFAULTS_FILE = PROJECT_ROOT / "config" / "templates" / "values" / "defaults.yaml"


@pytest.mark.parametrize(
    "profile_stem",
    [
        "sanity_async.yaml.in",
        "backlog_scaleout.yaml.in",
        "hourly_duty_cycle.yaml.in",
    ],
)
def test_async_batch_workload_profiles_render_valid_yaml(profile_stem: str):
    """Ensure every shipped async-batch profile template renders cleanly to valid YAML."""
    profile_path = PROJECT_ROOT / "workload" / "profiles" / "async-batch" / profile_stem
    assert profile_path.is_file()
    defaults = yaml.safe_load(DEFAULTS_FILE.read_text(encoding="utf-8"))
    env_map = build_env_map(
        plan_config=defaults,
        runtime_values={
            "LLMDBENCH_HARNESS_STACK_ENDPOINT_URL": "http://epp.bench.svc.cluster.local:80",
            "LLMDBENCH_DEPLOY_CURRENT_MODEL": "Qwen/Qwen3-32B",
        },
    )
    rendered = render_profile(profile_path.read_text(encoding="utf-8"), env_map)
    parsed = yaml.safe_load(rendered)
    assert isinstance(parsed, dict)
    assert parsed["transport"]["mode"] == "redis-sortedset"
    assert parsed["model"] == "Qwen/Qwen3-32B"
    assert len(parsed["waves"]) >= 1


@pytest.mark.skipif(
    not AUTOSCALING_ROOT.is_dir(),
    reason="Sibling llm-d-autoscaling repository not present",
)
@pytest.mark.parametrize(
    "strategy",
    ["baseline", "cron-window", "backlog"],
)
def test_autoscaling_async_batch_scenarios_render_cleanly(
    tmp_path: Path, strategy: str
):
    """Render llm-d-autoscaling staging/async-batch specs with inference-sim overlay."""
    from llmdbenchmark.config import config as bench_cfg

    spec_file = (
        AUTOSCALING_ROOT
        / "benchmark"
        / "config"
        / "specification"
        / "staging"
        / "async-batch"
        / f"{strategy}.yaml.j2"
    )
    overlay_file = (
        AUTOSCALING_ROOT
        / "benchmark"
        / "config"
        / "cluster-configs"
        / "k8s"
        / "inference-sim.yaml"
    )
    assert spec_file.is_file()
    assert overlay_file.is_file()

    plan_dir = tmp_path / "plan"
    plan_dir.mkdir(parents=True, exist_ok=True)
    bench_cfg.plan_dir = plan_dir
    bench_cfg.log_dir = tmp_path / "logs"
    bench_cfg.log_dir.mkdir(parents=True, exist_ok=True)

    spec_dict = RenderSpecification(
        specification_file=spec_file,
        base_dir=AUTOSCALING_ROOT,
    ).eval()

    overlay = yaml.safe_load(overlay_file.read_text(encoding="utf-8"))
    renderer = RenderPlans(
        template_dir=Path(spec_dict["template_dir"]["path"]),
        defaults_file=Path(spec_dict["values_file"]["path"]),
        scenarios_file=Path(spec_dict["scenario_file"]["path"]),
        output_dir=plan_dir,
        cli_namespace="bench-async",
        setup_overrides_by_stack={"*": overlay},
    )
    result = renderer.eval()
    assert not result.has_errors, f"Render errors for {strategy}: {result.to_dict()}"
    assert len(result.rendered_paths) == 1

    stack_dir = result.rendered_paths[0]
    merged_cfg = yaml.safe_load((stack_dir / "config.yaml").read_text(encoding="utf-8"))
    schema_warnings = validate_config(merged_cfg)
    assert schema_warnings == [], f"Unexpected schema warnings: {schema_warnings}"

    # Verify Redis + llm-d-async Helmfile + async-values rendered for every arm
    redis_docs = [
        d
        for d in yaml.safe_load_all(
            (stack_dir / "36_async-redis.yaml").read_text(encoding="utf-8")
        )
        if d
    ]
    assert [d["kind"] for d in redis_docs] == ["Deployment", "Service"]

    helmfile_docs = [
        d
        for d in yaml.safe_load_all(
            (stack_dir / "10_helmfile-main.yaml").read_text(encoding="utf-8")
        )
        if isinstance(d, dict) and "releases" in d
    ]
    async_releases = [
        r for r in helmfile_docs[0]["releases"] if r["name"].endswith("-async")
    ]
    assert len(async_releases) == 1
    assert async_releases[0]["version"] == "v1.0.0"

    async_vals = yaml.safe_load(
        (stack_dir / "12a_async-values.yaml").read_text(encoding="utf-8")
    )
    assert async_vals["ap"]["image"]["tag"] == "v1.0.0"
    assert async_vals["ap"]["transport"] == "redis-sortedset"
    assert (
        async_vals["ap"]["transportConfig"]["urlSecret"]["url"]
        == "redis://redis.bench-async.svc.cluster.local:6379/0"
    )
    assert (
        async_vals["ap"]["transportConfig"]["queues"][0]["gate_type"]
        == "prometheus-query"
    )

    so_path = stack_dir / "27_keda-scaledobjects.yaml"
    if strategy == "baseline":
        assert int(merged_cfg["decode"]["replicas"]) == 2
        assert not so_path.exists() or not so_path.read_text(encoding="utf-8").strip()
    elif strategy == "cron-window":
        assert int(merged_cfg["decode"]["replicas"]) == 0
        so_docs = [
            d for d in yaml.safe_load_all(so_path.read_text(encoding="utf-8")) if d
        ]
        assert len(so_docs) == 1
        so_spec = so_docs[0]["spec"]
        assert so_spec["minReplicaCount"] == 0
        assert so_spec["maxReplicaCount"] == 2
        assert so_spec["cooldownPeriod"] == 60
        cron_trig = so_docs[0]["spec"]["triggers"][0]
        assert cron_trig["type"] == "cron"
        # KEDA cron trigger must NOT carry metricType: AverageValue
        assert "metricType" not in cron_trig
        assert cron_trig["metadata"]["desiredReplicas"] == "2"
    elif strategy == "backlog":
        so_docs = [
            d for d in yaml.safe_load_all(so_path.read_text(encoding="utf-8")) if d
        ]
        so_spec = so_docs[0]["spec"]
        assert so_spec["minReplicaCount"] == 0
        assert so_spec["cooldownPeriod"] == 15
        assert so_spec["fallback"] == {
            "failureThreshold": 3,
            "replicas": 1,
            "behavior": "currentReplicasIfHigher",
        }
        prom_trig = so_spec["triggers"][0]
        assert prom_trig["type"] == "prometheus"
        assert prom_trig["metricType"] == "AverageValue"
        assert prom_trig["metadata"]["threshold"] == "2000"
        assert prom_trig["metadata"]["activationThreshold"] == "0.5"
        assert "llm_d_async_async_broker_backlog" in prom_trig["metadata"]["query"]
        assert (
            "llm_d_async_async_broker_backlog_source_available"
            in prom_trig["metadata"]["query"]
        )
