"""Step 00 -- Validate system dependencies and print cluster summary banner."""

from pathlib import Path

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.executor.deps import (
    check_system_dependencies,
    check_python_version,
    check_helm_version,
    check_helmfile_version,
    MIN_HELM_MAJOR,
    MIN_HELMFILE_VERSION,
)
from llmdbenchmark.utilities.cluster import print_phase_banner


class EnsureInfraStep(Step):
    """Validate system dependencies and print cluster summary banner."""

    def __init__(self):
        super().__init__(
            number=0,
            name="ensure_infra",
            description="Validate system dependencies and cluster connectivity",
            phase=Phase.STANDUP,
            per_stack=False,
        )

    def execute(
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        errors = []

        py_ok, py_version = check_python_version()
        if not py_ok:
            errors.append(f"Python >= 3.11 required, found {py_version}")

        dep_result = check_system_dependencies()
        if dep_result.has_missing_required:
            errors.append(
                f"Missing required tools: {', '.join(dep_result.missing_required)}"
            )

        if dep_result.missing_optional:
            if context.logger:
                for tool in dep_result.missing_optional:
                    context.logger.log_warning(f"Optional tool not found: {tool}")

        # Helm 4 toolchain guard. Standup deploys via helmfile; a Helm-3 host
        # or a pre-1.5 helmfile makes `helmfile template` panic with an
        # opaque "unknown flag: --client" error. Fail fast here with an
        # actionable message instead. Skipped on --dry-run (nothing deploys)
        # and only when the tool is actually present (a missing tool is
        # already reported above).
        if not context.dry_run:
            if "helm" in dep_result.available:
                helm_ok, helm_ver = check_helm_version()
                if not helm_ok:
                    errors.append(
                        f"Helm >= {MIN_HELM_MAJOR}.x required for standup "
                        f"(found {helm_ver}). Run ./install.sh to install the "
                        f"pinned Helm 4 toolchain."
                    )
            if "helmfile" in dep_result.available:
                hf_ok, hf_ver = check_helmfile_version()
                if not hf_ok:
                    min_hf = ".".join(str(p) for p in MIN_HELMFILE_VERSION)
                    errors.append(
                        f"helmfile >= {min_hf} required (Helm 4 compatible; "
                        f"found {hf_ver}). Older helmfile panics under Helm 4. "
                        f"Run ./install.sh to install the pinned helmfile."
                    )

        if errors:
            for err in errors:
                context.logger.log_error(f"    {err}")
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="Infrastructure checks failed",
                errors=errors,
            )

        print_phase_banner(
            context,
            extra_fields={
                "Python": py_version,
            },
        )

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=(
                f"All checks passed. "
                f"Tools: {', '.join(dep_result.available)}. "
                f"Python: {py_version}. "
                f"Platform: {context.platform_type}"
            ),
            context={
                "python_version": py_version,
                "available_tools": dep_result.available,
                "missing_optional": dep_result.missing_optional,
                "is_openshift": context.is_openshift,
                "is_kind": context.is_kind,
                "is_minikube": context.is_minikube,
                "platform_type": context.platform_type,
                "cluster_name": context.cluster_name,
                "cluster_server": context.cluster_server,
            },
        )
