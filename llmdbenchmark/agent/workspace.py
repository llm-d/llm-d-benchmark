"""The Agent Session Workspace: the only I/O in this package.

Follows the mkdir-on-access convention of
``llmdbenchmark.executor.context.ExecutionContext.run_dir()`` without
importing ``ExecutionContext`` or the global ``config`` singleton -- the
Agent Core has no cluster and no run.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from llmdbenchmark.agent.facts import (
    AgentRenderedRunCommand,
    ExecutionFacts,
    RecommendationOutput,
    SloGoodput,
)


def write_agent_session_workspace(
    session_root: Path,
    execution_facts: ExecutionFacts,
    recommendation: RecommendationOutput,
    run_command: AgentRenderedRunCommand,
    benchmark_job_manifest: dict,
    slo_goodput: SloGoodput | None = None,
) -> Path:
    """Write the Agent Session Workspace under
    ``<session_root>/<Benchmark Session ID>/`` and return that directory."""
    session_dir = Path(session_root) / execution_facts.benchmark_session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    (session_dir / "recommendation.yaml").write_text(
        yaml.safe_dump(
            recommendation.model_dump(mode="json"),
            sort_keys=True,
            default_flow_style=False,
        )
    )
    (session_dir / "run-command.txt").write_text(run_command.rendered + "\n")
    (session_dir / "run-command.json").write_text(
        json.dumps(run_command.argv, indent=2, sort_keys=False) + "\n"
    )
    (session_dir / "benchmark-job.yaml").write_text(
        yaml.safe_dump(benchmark_job_manifest, sort_keys=True, default_flow_style=False)
    )
    if slo_goodput is not None:
        (session_dir / "slo-goodput.yaml").write_text(
            yaml.safe_dump(
                slo_goodput.model_dump(mode="json"),
                sort_keys=True,
                default_flow_style=False,
            )
        )

    return session_dir
