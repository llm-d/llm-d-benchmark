"""Teardown Step 00 -- Preflight checks: load plan config and print summary banner."""

from pathlib import Path

import yaml

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.utilities.cluster import print_phase_banner


class TeardownPreflightStep(Step):
    """Load teardown configuration and print summary banner."""

    def __init__(self):
        super().__init__(
            number=0,
            name="teardown_preflight",
            description="Validate cluster connectivity and load teardown config",
            phase=Phase.TEARDOWN,
            per_stack=False,
        )

    def execute(
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        plan_config = self._load_plan_config(context)
        if not plan_config:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message=(
                    "No rendered plan config found. Run 'llmdbenchmark plan' "
                    "first, or ensure the rendered output directory is accessible."
                ),
                errors=["plan config (config.yaml) not found"],
            )

        context.namespace = (
            context.namespace
            or self._require_config(plan_config, "namespace", "name")
        )
        context.harness_namespace = (
            context.harness_namespace
            or plan_config.get("harness", {}).get("namespace", "")
            or context.namespace
        )
        context.release = self._require_config(plan_config, "release")

        mode = "deep" if context.deep_clean else "normal"
        if context.dry_run:
            mode = f"{mode} (dry-run)"

        extra = {
            "Mode": mode,
            "Release": context.release,
        }
        harness_ns = context.harness_namespace
        if harness_ns and harness_ns != context.namespace:
            extra["Harness NS"] = harness_ns

        print_phase_banner(context, extra_fields=extra)

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=(
                f"Preflight complete "
                f"(ns={context.namespace}, platform={context.platform_type})"
            ),
        )

    def _load_plan_config(self, context: ExecutionContext) -> dict | None:
        """Load the first stack's config.yaml for namespace/release info."""
        for stack_path in context.rendered_stacks or []:
            config_file = stack_path / "config.yaml"
            if not config_file.exists():
                continue
            try:
                with open(config_file, encoding="utf-8") as f:
                    cfg = yaml.safe_load(f)
                if cfg:
                    return cfg
            except (OSError, yaml.YAMLError):
                continue
        return None
