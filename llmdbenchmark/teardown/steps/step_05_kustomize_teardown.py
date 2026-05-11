"""Step 05 -- Teardown resources deployed via the kustomize method."""

from __future__ import annotations

import shlex
from pathlib import Path

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.executor.command import CommandExecutor
from llmdbenchmark.kustomize.readme_parser import (
    CommandPhase,
    parse_guide_readme,
)
from llmdbenchmark.kustomize.variable_resolver import GuideVariableResolver


class KustomizeTeardownStep(Step):
    """Remove resources deployed by the kustomize deploy step."""

    def __init__(self):
        super().__init__(
            number=5,
            name="kustomize_teardown",
            description="Teardown llm-d guide deployed via kustomize",
            phase=Phase.TEARDOWN,
            per_stack=True,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        return "kustomize" not in context.deployed_methods

    def execute(
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        errors: list[str] = []
        cmd = context.require_cmd()
        namespace = context.require_namespace()

        plan_config = self._load_plan_config(context) if stack_path is None else self._load_stack_config(stack_path)
        kust_config = plan_config.get("kustomize", {}) if plan_config else {}

        guide_name = kust_config.get("guideName", "")
        repo_path = kust_config.get("repoPath") or context.llmd_repo_path or ""
        if not repo_path:
            auto_cloned = context.workspace / "llm-d"
            if (auto_cloned / "guides").is_dir():
                repo_path = str(auto_cloned)
        gaie_version = kust_config.get("gaieVersion", "")
        accel_backend = kust_config.get("acceleratorBackend", "gpu/vllm")
        monitoring = kust_config.get("monitoring", False)

        if not guide_name or not repo_path:
            context.logger.log_warning(
                "kustomize teardown: guide_name or repo_path missing, "
                "falling back to direct resource deletion"
            )
            return self._fallback_teardown(cmd, context, namespace, guide_name)

        readme_path = Path(repo_path) / "guides" / guide_name / "README.md"

        if readme_path.exists():
            parsed = parse_guide_readme(readme_path, guide_name)

            if not gaie_version:
                gaie_version = parsed.variables.get("GAIE_VERSION", "")
            if not gaie_version:
                context.logger.log_warning(
                    "GAIE_VERSION not resolved -- cleanup commands "
                    "using ${GAIE_VERSION} will not be substituted"
                )

            resolver = GuideVariableResolver(
                guide_name=guide_name,
                namespace=namespace,
                gaie_version=gaie_version,
                repo_path=repo_path,
                accelerator_backend=accel_backend,
                extra_variables=kust_config.get("extraVariables", {}),
                readme_variables=parsed.variables,
            )

            cleanup_cmds = parsed.get_cleanup_commands()

            for gc in cleanup_cmds:
                resolved = resolver.resolve(gc.raw)
                context.logger.log_info(f"[teardown] {resolved[:120]}")
                result = self._run_resolved(cmd, resolved, check=False)
                if not result.success:
                    context.logger.log_warning(
                        f"Teardown command failed (continuing): {result.stderr[:200]}"
                    )
                    errors.append(result.stderr[:200])
        else:
            context.logger.log_warning(
                f"Guide README not found at {readme_path}, using fallback teardown"
            )
            return self._fallback_teardown(cmd, context, namespace, guide_name)

        if monitoring:
            mon_path = Path(repo_path) / "guides" / "recipes" / "modelserver" / "components" / "monitoring"
            if mon_path.exists():
                result = cmd.kube(
                    "delete", "-n", namespace,
                    "-k", str(mon_path),
                    "--ignore-not-found",
                    check=False,
                )
                if not result.success:
                    context.logger.log_warning(
                        f"Monitoring teardown failed (non-fatal): {result.stderr[:200]}"
                    )

        overlay_dir = context.workspace / "setup" / "kustomize-overlay"
        if overlay_dir.exists():
            import shutil
            shutil.rmtree(overlay_dir, ignore_errors=True)

        if errors:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message=f"Kustomize teardown completed with {len(errors)} warning(s)",
                stack_name=stack_path.name if stack_path else None,
            )

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=f"Kustomize teardown of guide '{guide_name}' complete",
            stack_name=stack_path.name if stack_path else None,
        )

    @staticmethod
    def _run_resolved(
        cmd: CommandExecutor, resolved: str, *, check: bool = True
    ):
        tokens = shlex.split(resolved)
        if not tokens:
            return cmd.execute(resolved, check=check)

        binary = tokens[0]
        rest = tokens[1:]

        if binary in ("kubectl", "oc"):
            return cmd.kube(*rest, check=check)
        if binary == "helm":
            return cmd.helm(*rest, check=check)

        return cmd.execute(resolved, check=check)

    def _fallback_teardown(
        self, cmd, context, namespace: str, guide_name: str
    ) -> StepResult:
        if guide_name:
            cmd.helm(
                "uninstall", guide_name, "-n", namespace,
                check=False,
            )

        cmd.kube(
            "delete", "deployment,service,configmap,serviceaccount",
            "--all", "--namespace", namespace,
            check=False,
        )

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message="Kustomize fallback teardown complete",
        )
