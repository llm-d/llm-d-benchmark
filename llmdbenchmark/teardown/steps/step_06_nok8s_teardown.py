"""Step 06 -- Teardown the nok8s container stack (no Kubernetes).

Removes the vLLM/EPP/Envoy containers launched by
step_06_nok8s_deploy.py, driven by the rendered ``34_nok8s-containers.yaml``
launch spec.
"""

from pathlib import Path

import yaml

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext


class NoK8sTeardownStep(Step):
    """Remove the local container stack deployed by the nok8s method."""

    def __init__(self):
        super().__init__(
            number=6,
            name="nok8s_teardown",
            description="Remove nok8s containers (vLLM + EPP + Envoy)",
            phase=Phase.TEARDOWN,
            per_stack=True,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        return "nok8s" not in (context.deployed_methods or [])

    def execute(
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        cmd = context.require_cmd()
        runtime = context.container_runtime or "docker"

        names: list[str] = []
        spec_yaml = (
            self._find_yaml(stack_path, "34_nok8s-containers") if stack_path else None
        )
        if spec_yaml and self._has_yaml_content(spec_yaml):
            spec = yaml.safe_load(spec_yaml.read_text(encoding="utf-8")) or {}
            runtime = spec.get("runtime", runtime)
            names = [c["name"] for c in spec.get("containers", [])]

        # Fallback to the well-known names if the spec is unavailable.
        if not names:
            names = ["envoy", "epp", "vllm-0"]

        for name in names:
            cmd.execute(f"{runtime} rm -f {name}", check=False)

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=f"Removed nok8s containers: {', '.join(names)}",
            stack_name=stack_path.name if stack_path else None,
        )
