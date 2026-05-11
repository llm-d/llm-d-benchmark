"""Step 06 -- Deploy an llm-d guide via kustomize (well-lit-path)."""

from __future__ import annotations

import shlex
from pathlib import Path

import yaml

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.executor.command import CommandExecutor
from llmdbenchmark.kustomize.readme_parser import (
    CommandPhase,
    DeployMode,
    parse_guide_readme,
)
from llmdbenchmark.kustomize.variable_resolver import GuideVariableResolver


class KustomizeDeployStep(Step):
    """Deploy an llm-d guide using commands parsed from its README.md."""

    def __init__(self):
        super().__init__(
            number=6,
            name="kustomize_deploy",
            description="Deploy llm-d guide via kustomize (well-lit-path)",
            phase=Phase.STANDUP,
            per_stack=True,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        return "kustomize" not in context.deployed_methods

    def execute(
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        if stack_path is None:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="No stack path provided for per-stack step",
                errors=["stack_path is required"],
            )

        errors: list[str] = []
        cmd = context.require_cmd()
        plan_config = self._load_stack_config(stack_path)
        namespace = context.require_namespace()

        kust_config = plan_config.get("kustomize", {})
        if not kust_config.get("enabled"):
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message="kustomize.enabled is false, skipping",
                stack_name=stack_path.name,
            )

        guide_name = kust_config.get("guideName", "")
        repo_path = kust_config.get("repoPath") or context.llmd_repo_path or ""
        repo_ref = kust_config.get("repoRef", "main")
        gaie_version = kust_config.get("gaieVersion", "")
        accel_backend = kust_config.get("acceleratorBackend", "gpu/vllm")
        monitoring = kust_config.get("monitoring", False)
        overlay_path = kust_config.get("overlayPath", "")
        patches = list(kust_config.get("patches", []))
        extra_helm_values = kust_config.get("extraHelmValues", [])
        extra_helm_sets = kust_config.get("extraHelmSets", {})
        deploy_timeout = int(
            kust_config.get("deployTimeout") or context.kustomize_deploy_timeout
        )

        if not guide_name:
            return self._fail(["kustomize.guideName is required"], stack_path,
                              "kustomize.guideName not set")

        if not repo_path:
            clone_result = self._ensure_repo_cloned(
                cmd, context, repo_ref, errors,
            )
            if clone_result is None:
                return self._fail(errors, stack_path, "Failed to clone llm-d repo",
                                  context=context)
            repo_path = clone_result

        readme_path = Path(repo_path) / "guides" / guide_name / "README.md"
        if not readme_path.exists():
            return self._fail(
                [f"File does not exist: {readme_path}"],
                stack_path, f"Guide README not found: {readme_path}",
            )

        context.logger.log_info(f"Parsing guide README: {readme_path}")

        parsed = parse_guide_readme(readme_path, guide_name)
        if parsed.variables:
            context.logger.log_info(
                f"README variables: {', '.join(f'{k}={v}' for k, v in parsed.variables.items())}"
            )

        if not gaie_version:
            gaie_version = parsed.variables.get("GAIE_VERSION", "v1.5.0")

        resolver = GuideVariableResolver(
            guide_name=guide_name,
            namespace=namespace,
            gaie_version=gaie_version,
            repo_path=repo_path,
            accelerator_backend=accel_backend,
            extra_variables=kust_config.get("extraVariables", {}),
            readme_variables=parsed.variables,
        )

        # --- 1. Prerequisites ---
        prereq_cmds = parsed.get_commands(CommandPhase.PREREQUISITES)
        for gc in prereq_cmds:
            resolved = resolver.resolve(gc.raw)
            if "create namespace" in resolved:
                ns_check = cmd.kube("get", "namespace", namespace,
                                    check=False, force=True)
                if ns_check.success:
                    context.logger.log_info(
                        f"Namespace {namespace} already exists, skipping create")
                    continue
            context.logger.log_info(f"[prerequisites] {resolved[:120]}")
            result = self._run_resolved(cmd, resolved, check=False)
            if not result.success:
                errors.append(f"Prerequisite failed: {result.stderr}")

        if errors:
            return self._fail(errors, stack_path, "Prerequisite commands failed",
                              context=context)

        # --- 1b. HF token secret ---
        self._ensure_hf_token_secret(cmd, context, namespace)

        # --- 2. Router ---
        router_cmds = parsed.get_commands(CommandPhase.ROUTER, DeployMode.STANDALONE)
        for gc in router_cmds:
            resolved = resolver.resolve(gc.raw)
            resolved = self._inject_extra_helm_args(
                resolved, extra_helm_values, extra_helm_sets,
            )
            context.logger.log_info(f"[router] {resolved[:120]}")
            result = self._run_resolved(cmd, resolved, check=False)
            if not result.success:
                errors.append(f"Router deploy failed: {result.stderr}")

        if errors:
            return self._fail(errors, stack_path, "Router deployment failed",
                              context=context)

        # --- 3. Model Server ---
        patches = self._inject_env_patches(patches, context)
        modelserver_cmds = parsed.get_commands(CommandPhase.MODELSERVER)
        target_cmd = self._select_modelserver_command(
            modelserver_cmds, accel_backend, resolver,
        )

        if target_cmd is None:
            return self._fail(
                [f"No modelserver command found for backend '{accel_backend}'"],
                stack_path, "No modelserver command for backend",
            )

        resolved_ms = resolver.resolve(target_cmd.raw)
        ms_path = self._extract_kustomize_path(resolved_ms)
        if not ms_path:
            ms_path = str(
                Path(repo_path) / "guides" / guide_name
                / "modelserver" / accel_backend
            )

        needs_wrapper = (
            patches
            or (overlay_path and Path(overlay_path).is_dir())
        )

        if needs_wrapper:
            kustomize_dir = self._build_overlay_wrapper(
                context, Path(ms_path), overlay_path, patches,
            )
            context.logger.log_info(
                f"[modelserver] kubectl apply -k {kustomize_dir} (with overrides)")
            result = cmd.kube("apply", "-n", namespace,
                              "-k", str(kustomize_dir), check=False)
        else:
            context.logger.log_info(
                f"[modelserver] kubectl apply -k {ms_path}")
            result = cmd.kube("apply", "-n", namespace,
                              "-k", ms_path, check=False)

        if not result.success:
            errors.append(f"Model server deploy failed: {result.stderr}")
            return self._fail(errors, stack_path, "Model server deployment failed",
                              context=context)

        # --- 4. Monitoring ---
        if monitoring:
            mon_path = (
                Path(repo_path) / "guides" / "recipes"
                / "modelserver" / "components" / "monitoring"
            )
            context.logger.log_info(f"[monitoring] kubectl apply -k {mon_path}")
            result = cmd.kube("apply", "-n", namespace,
                              "-k", str(mon_path), check=False)
            if not result.success:
                context.logger.log_warning(
                    f"Monitoring apply failed (non-fatal): {result.stderr}")

        # --- 5. Wait ---
        context.logger.log_info(f"Waiting for pods (timeout={deploy_timeout}s)...")
        wait_result = cmd.wait_for_pods(
            label=f"llm-d.ai/guide={guide_name}",
            namespace=namespace,
            timeout=deploy_timeout,
            poll_interval=10,
            description=f"kustomize {guide_name}",
        )
        if not wait_result.success:
            return self._fail(
                [f"Pods not ready: {wait_result.stderr}"],
                stack_path, "Pod readiness timeout",
                context=context,
            )

        # --- 6. Endpoint ---
        epp_service = f"{guide_name}-epp"
        context.deployed_endpoints[stack_path.name] = epp_service
        context.logger.log_info(f"Endpoint registered: {epp_service}")

        cmd.kube("get", "deployment,service,pods",
                 "--namespace", namespace, check=False)

        self._propagate_standup_parameters(cmd, context, plan_config, guide_name)

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=f"Kustomize deployment of guide '{guide_name}' complete",
            stack_name=stack_path.name,
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
            if rest and rest[0] == "install":
                rest = ["upgrade", "--install"] + rest[1:]
            return cmd.helm(*rest, check=check)

        return cmd.execute(resolved, check=check)

    _LLMD_REPO_URL = "https://github.com/llm-d/llm-d.git"

    @staticmethod
    def _ensure_repo_cloned(
        cmd: CommandExecutor,
        context: ExecutionContext,
        ref: str,
        errors: list[str],
    ) -> str | None:
        repo_dir = context.workspace / "llm-d"

        if (repo_dir / "guides").is_dir():
            context.logger.log_info(
                f"llm-d repo already present at {repo_dir}, reusing"
            )
            result = cmd.execute(
                f"git -C {shlex.quote(str(repo_dir))} fetch --depth 1 origin {shlex.quote(ref)}",
                check=False,
            )
            if result.success:
                cmd.execute(
                    f"git -C {shlex.quote(str(repo_dir))} checkout FETCH_HEAD",
                    check=False,
                )
            return str(repo_dir)

        context.logger.log_info(
            f"No llm-d repo path configured -- cloning {KustomizeDeployStep._LLMD_REPO_URL} "
            f"(ref={ref}) into {repo_dir}"
        )
        clone_cmd = (
            f"git clone --depth 1 --branch {shlex.quote(ref)} "
            f"{shlex.quote(KustomizeDeployStep._LLMD_REPO_URL)} "
            f"{shlex.quote(str(repo_dir))}"
        )
        result = cmd.execute(clone_cmd, check=False)
        if not result.success:
            errors.append(
                f"Failed to clone llm-d repo: {result.stderr}. "
                "Set --llmd-repo-path to a local clone instead."
            )
            return None

        context.logger.log_info(f"llm-d repo cloned to {repo_dir}")
        return str(repo_dir)

    _HF_SECRET_NAME = "llm-d-hf-token"

    @staticmethod
    def _ensure_hf_token_secret(
        cmd: CommandExecutor, context: ExecutionContext, namespace: str,
    ) -> None:
        import os

        hf_token = (
            os.environ.get("HF_TOKEN")
            or os.environ.get("LLMDBENCH_HF_TOKEN")
            or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        )
        if not hf_token:
            context.logger.log_info(
                "No HF_TOKEN in environment -- skipping secret creation "
                "(gated models will fail)"
            )
            return

        secret_name = KustomizeDeployStep._HF_SECRET_NAME
        check = cmd.kube(
            "get", "secret", secret_name,
            "--namespace", namespace,
            check=False,
        )
        if check.success:
            context.logger.log_info(
                f"HF token secret '{secret_name}' already exists in {namespace}"
            )
            return

        result = cmd.kube(
            "create", "secret", "generic", secret_name,
            f"--from-literal=HF_TOKEN={hf_token}",
            "--namespace", namespace,
            check=False,
        )
        if result.success:
            context.logger.log_info(
                f"Created HF token secret '{secret_name}' in {namespace}"
            )
        else:
            context.logger.log_warning(
                f"Failed to create HF token secret: {result.stderr[:200]}"
            )

    def _fail(
        self, errors: list[str], stack_path: Path, message: str,
        context: ExecutionContext | None = None,
    ) -> StepResult:
        if context:
            for err in errors:
                context.logger.log_error(f"  {err}")
        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=False,
            message=message,
            errors=errors,
            stack_name=stack_path.name,
        )

    @staticmethod
    def _extract_kustomize_path(resolved_cmd: str) -> str | None:
        tokens = shlex.split(resolved_cmd)
        for i, tok in enumerate(tokens):
            if tok in ("-k", "--kustomize") and i + 1 < len(tokens):
                return tokens[i + 1]
            if tok.startswith("-k") and len(tok) > 2:
                return tok[2:]
        return None

    @staticmethod
    def _select_modelserver_command(commands, accel_backend, resolver):
        for gc in commands:
            resolved = resolver.resolve(gc.raw)
            if f"modelserver/{accel_backend}" in resolved:
                return gc
        if commands:
            return commands[0]
        return None

    @staticmethod
    def _inject_extra_helm_args(
        helm_cmd: str,
        extra_values: list[str],
        extra_sets: dict[str, str],
    ) -> str:
        parts = [helm_cmd]
        for vf in extra_values:
            abs_vf = str(Path(vf).resolve()) if not Path(vf).is_absolute() else vf
            parts.append(f"-f {abs_vf}")
        for k, v in extra_sets.items():
            parts.append(f"--set {k}={v}")
        return " ".join(parts)

    @staticmethod
    def _inject_env_patches(
        patches: list[dict],
        context: ExecutionContext,
    ) -> list[dict]:
        import os

        priority_class = os.environ.get("LLMDBENCH_PRIORITY_CLASS", "")
        if priority_class:
            context.logger.log_info(
                f"Injecting priorityClassName={priority_class} "
                "from LLMDBENCH_PRIORITY_CLASS"
            )
            patches.append({"patch": (
                "apiVersion: apps/v1\n"
                "kind: Deployment\n"
                "metadata:\n"
                "  name: decode\n"
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                f"      priorityClassName: {priority_class}\n"
            )})

        return patches

    @staticmethod
    def _build_overlay_wrapper(
        context: ExecutionContext,
        guide_ms_path: Path,
        overlay_path: str = "",
        patches_config: list[dict] | None = None,
    ) -> Path:
        import os

        overlay_dir = context.workspace / "setup" / "kustomize-overlay"
        overlay_dir.mkdir(parents=True, exist_ok=True)

        rel_base = os.path.relpath(guide_ms_path.resolve(), overlay_dir.resolve())

        wrapper: dict = {
            "apiVersion": "kustomize.config.k8s.io/v1beta1",
            "kind": "Kustomization",
            "resources": [rel_base],
        }

        patch_entries: list[dict] = []

        if overlay_path:
            overlay = Path(overlay_path).resolve()
            rel_overlay = os.path.relpath(overlay, overlay_dir.resolve())
            if (overlay / "kustomization.yaml").exists():
                wrapper["components"] = [rel_overlay]
            else:
                for patch_file in sorted(overlay.glob("*.yaml")):
                    rel_pf = os.path.relpath(patch_file, overlay_dir.resolve())
                    patch_entries.append({"path": rel_pf})

        if patches_config:
            for i, entry in enumerate(patches_config):
                patch_yaml = entry.get("patch", "")
                if not patch_yaml:
                    continue
                patch_file = overlay_dir / f"patch-{i:02d}.yaml"
                patch_file.write_text(patch_yaml, encoding="utf-8")
                patch_entries.append({"path": f"patch-{i:02d}.yaml"})
            context.logger.log_info(
                f"Wrote {len(patches_config)} inline patch(es) to {overlay_dir}"
            )

        if patch_entries:
            wrapper["patches"] = patch_entries

        (overlay_dir / "kustomization.yaml").write_text(
            yaml.dump(wrapper, default_flow_style=False), encoding="utf-8",
        )

        context.logger.log_info(f"Overlay wrapper written to {overlay_dir}")
        return overlay_dir

    def _propagate_standup_parameters(
        self, cmd: CommandExecutor, context: ExecutionContext,
        plan_config: dict, guide_name: str,
    ):
        """Persist deploy metadata as a ConfigMap."""
        from datetime import datetime, timezone
        from llmdbenchmark import __version__

        harness_ns = context.harness_namespace or context.require_namespace()
        cm_name = "llm-d-benchmark-standup-parameters"

        model_cfg = plan_config.get("model", {})
        kust_cfg = plan_config.get("kustomize", {})

        params = {
            "tool_name": "llm-d-benchmark",
            "tool_version": __version__,
            "deployed_by": context.username or "unknown",
            "deployed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "cluster_name": context.cluster_name or "",
            "platform_type": context.platform_type,
            "namespace": context.namespace or "",
            "harness_namespace": harness_ns,
            "deploy_methods": ",".join(context.deployed_methods),
            "guide_name": guide_name,
            "accelerator_backend": kust_cfg.get("acceleratorBackend", "gpu/vllm"),
            "model_name": model_cfg.get("name", ""),
            "gaie_version": kust_cfg.get("gaieVersion", ""),
        }

        literal_args = []
        for key, value in params.items():
            literal_args.append(f"--from-literal={key}={value}")

        create_args = [
            "create", "configmap", cm_name,
            "--namespace", harness_ns,
        ] + literal_args + ["--dry-run=client", "-o", "yaml"]

        result = cmd.kube(*create_args)
        if result.success:
            yaml_path = context.setup_yamls_dir() / "standup-parameters.yaml"
            yaml_path.write_text(result.stdout, encoding="utf-8")
            apply_result = cmd.kube("apply", "-f", str(yaml_path))
            if apply_result.success:
                context.logger.log_info(
                    f"Deployment metadata saved to configmap/{cm_name} in ns/{harness_ns}"
                )
