"""Step 06 -- Deploy an llm-d guide via kustomize (well-lit-path)."""

from __future__ import annotations

import base64
import shlex
import tempfile
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
        # `ROUTER_CHART_VERSION` is referenced by guide READMEs alongside
        # `GAIE_VERSION` after the llm-d-router chart migration. Default
        # to v0 (the published llm-d-router-* chart version) when the
        # scenario doesn't pin one.
        router_chart_version = kust_config.get("routerChartVersion", "") or "v0"
        accel_backend = kust_config.get("acceleratorBackend", "gpu/vllm")
        monitoring = kust_config.get("monitoring", False)
        overlay_path = kust_config.get("overlayPath", "")
        patches = kust_config.get("patches", [])
        extra_helm_values = kust_config.get("extraHelmValues", [])
        extra_helm_sets = kust_config.get("extraHelmSets", {})
        deploy_timeout = int(
            kust_config.get("deployTimeout") or context.kustomize_deploy_timeout
        )

        if not guide_name:
            return self._fail(
                ["kustomize.guideName is required"],
                stack_path,
                "kustomize.guideName not set",
            )

        self._log_kustomize_authority(context, guide_name, repo_path, repo_ref)

        if not repo_path:
            clone_result = self._ensure_repo_cloned(
                cmd,
                context,
                repo_ref,
                errors,
            )
            if clone_result is None:
                return self._fail(
                    errors, stack_path, "Failed to clone llm-d repo", context=context
                )
            repo_path = clone_result

        readme_path = Path(repo_path) / "guides" / guide_name / "README.md"
        if not readme_path.exists():
            return self._fail(
                [f"File does not exist: {readme_path}"],
                stack_path,
                f"Guide README not found: {readme_path}",
            )

        context.logger.log_info(f"Parsing guide README: {readme_path}")

        parsed = parse_guide_readme(readme_path, guide_name)
        if parsed.variables:
            context.logger.log_info(
                f"README variables: {', '.join(f'{k}={v}' for k, v in parsed.variables.items())}"
            )
        overrodevars = kust_config.get("guideVariableOverrides", {})
        if overrodevars:
            context.logger.log_info(
                f"OVERRODE variables: {', '.join(f'{k}={v}' for k, v in overrodevars.items())}"
            )
        if not gaie_version:
            gaie_version = parsed.variables.get("GAIE_VERSION", "v1.5.0")

        # Allow the scenario / guide README to override the default. The
        # scenario knob (kustomize.routerChartVersion) takes precedence; the
        # README's `export ROUTER_CHART_VERSION=...` falls back next; we then
        # fall back to the v0 default the resolver applies.
        readme_router_chart_version = parsed.variables.get("ROUTER_CHART_VERSION", "")
        effective_router_chart_version = (
            router_chart_version or readme_router_chart_version or "v0"
        )

        resolver = GuideVariableResolver(
            guide_name=guide_name,
            namespace=namespace,
            gaie_version=gaie_version,
            router_chart_version=effective_router_chart_version,
            repo_path=repo_path,
            accelerator_backend=accel_backend,
            variable_overrides=kust_config.get("guideVariableOverrides", {}),
            readme_variables=parsed.variables,
        )

        # --- 1. Prerequisites ---
        prereq_cmds = parsed.get_commands(CommandPhase.PREREQUISITES)
        for gc in prereq_cmds:
            resolved = resolver.resolve(gc.raw)
            if "create namespace" in resolved:
                ns_check = cmd.kube(
                    "get", "namespace", namespace, check=False, force=True
                )
                if ns_check.success:
                    context.logger.log_info(
                        f"Namespace {namespace} already exists, skipping create"
                    )
                    continue
            context.logger.log_info(f"[prerequisites] {resolved[:120]}")
            result = self._run_resolved(cmd, resolved, check=False)
            if not result.success:
                errors.append(f"Prerequisite failed: {result.stderr}")

        if errors:
            return self._fail(
                errors, stack_path, "Prerequisite commands failed", context=context
            )

        # --- 1b. HF token secret ---
        # Upstream guide manifests (since llm-d/llm-d#1684) reference
        # `secret/llm-d-hf-token` without `optional: true`, so missing
        # the Secret hangs every Pod in CreateContainerConfigError.
        # Ensure it exists (or fail fast) in both the deploy namespace
        # and the harness namespace when they differ.
        hf_error = self._ensure_hf_token_secret(cmd, context, namespace)
        if hf_error:
            return self._fail(
                [hf_error], stack_path, "HF token setup failed", context=context
            )
        harness_ns = getattr(context, "harness_namespace", None)
        if harness_ns and harness_ns != namespace:
            hf_error = self._ensure_hf_token_secret(cmd, context, harness_ns)
            if hf_error:
                return self._fail(
                    [hf_error],
                    stack_path,
                    "HF token setup failed in harness namespace",
                    context=context,
                )

        # --- 2. Router ---
        router_cmds = parsed.get_commands(CommandPhase.ROUTER, DeployMode.STANDALONE)
        for gc in router_cmds:
            resolved = resolver.resolve(gc.raw)
            resolved = self._inject_extra_helm_args(
                resolved,
                extra_helm_values,
                extra_helm_sets,
            )
            context.logger.log_info(f"[router] {resolved[:120]}")
            result = self._run_resolved(cmd, resolved, check=False)
            if not result.success:
                errors.append(f"Router deploy failed: {result.stderr}")

        if errors:
            return self._fail(
                errors, stack_path, "Router deployment failed", context=context
            )

        # --- 3. Model Server ---
        modelserver_cmds = parsed.get_commands(CommandPhase.MODELSERVER)
        target_cmd = self._select_modelserver_command(
            modelserver_cmds,
            accel_backend,
            resolver,
        )

        if target_cmd is None:
            return self._fail(
                [f"No modelserver command found for backend '{accel_backend}'"],
                stack_path,
                "No modelserver command for backend",
            )

        resolved_ms = resolver.resolve(target_cmd.raw)
        ms_path = self._extract_kustomize_path(resolved_ms)
        if not ms_path:
            ms_path = str(
                Path(repo_path) / "guides" / guide_name / "modelserver" / accel_backend
            )

        needs_wrapper = patches or (overlay_path and Path(overlay_path).is_dir())

        if needs_wrapper:
            kustomize_dir = self._build_overlay_wrapper(
                context,
                Path(ms_path),
                overlay_path,
                patches,
            )
            context.logger.log_info(
                f"[modelserver] kubectl apply -k {kustomize_dir} (with overrides)"
            )
            result = cmd.kube(
                "apply", "-n", namespace, "-k", str(kustomize_dir), check=False
            )
        else:
            context.logger.log_info(f"[modelserver] kubectl apply -k {ms_path}")
            result = cmd.kube("apply", "-n", namespace, "-k", ms_path, check=False)

        if not result.success:
            errors.append(f"Model server deploy failed: {result.stderr}")
            return self._fail(
                errors, stack_path, "Model server deployment failed", context=context
            )

        # --- 4. Monitoring ---
        if monitoring:
            mon_path = (
                Path(repo_path)
                / "guides"
                / "recipes"
                / "modelserver"
                / "components"
                / "monitoring"
            )
            context.logger.log_info(f"[monitoring] kubectl apply -k {mon_path}")
            result = cmd.kube(
                "apply", "-n", namespace, "-k", str(mon_path), check=False
            )
            if not result.success:
                context.logger.log_warning(
                    f"Monitoring apply failed (non-fatal): {result.stderr}"
                )

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
                stack_path,
                "Pod readiness timeout",
                context=context,
            )

        # --- 6. Endpoint ---
        epp_service = f"{guide_name}-epp"
        context.deployed_endpoints[stack_path.name] = epp_service
        context.logger.log_info(f"Endpoint registered: {epp_service}")

        cmd.kube(
            "get", "deployment,service,pods", "--namespace", namespace, check=False
        )

        self._propagate_standup_parameters(cmd, context, plan_config, guide_name)

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=f"Kustomize deployment of guide '{guide_name}' complete",
            stack_name=stack_path.name,
        )

    @staticmethod
    def _run_resolved(cmd: CommandExecutor, resolved: str, *, check: bool = True):
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
    _HF_SECRET_KEY = "HF_TOKEN"

    @staticmethod
    def _ensure_hf_token_secret(
        cmd: CommandExecutor,
        context: ExecutionContext,
        namespace: str,
    ) -> str | None:
        """Ensure the HF token Secret exists in ``namespace``.

        Kustomize-mode-only.  Upstream llm-d guide manifests (since PR
        llm-d/llm-d#1684) reference ``secret/llm-d-hf-token`` directly
        with no ``optional: true`` -- without it every Pod hangs in
        ``CreateContainerConfigError``.  This helper enforces presence:

        - If a Secret named ``llm-d-hf-token`` already exists in the
          namespace, succeed and leave it alone (supports externally
          managed Secrets via ESO / Vault / manual create).
        - If no Secret exists and ``HF_TOKEN`` /
          ``LLMDBENCH_HF_TOKEN`` / ``HUGGING_FACE_HUB_TOKEN`` is set in
          the process env, create it from that value.
        - If no Secret AND no env token, return a descriptive error
          string so the caller can fail the step fast.

        Returns ``None`` on success, or an error message string on
        failure (caller is expected to surface via ``self._fail``).

        Security: the token value is never passed on a command line
        (which would be logged by ``CommandExecutor``).  Instead we
        build the Secret manifest in-process, base64-encode the token,
        write to a ``NamedTemporaryFile`` (Python defaults to mode
        0600 on Unix), ``kubectl apply -f`` that file, then unlink it.
        Any ``kubectl`` stderr we surface is also scrubbed of the
        literal token value as a belt-and-braces precaution.
        """
        import os

        hf_token = (
            os.environ.get("HF_TOKEN")
            or os.environ.get("LLMDBENCH_HF_TOKEN")
            or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        )

        secret_name = KustomizeDeployStep._HF_SECRET_NAME
        secret_key = KustomizeDeployStep._HF_SECRET_KEY

        # 1. Already in the cluster? Leave it alone -- supports ESO /
        #    Vault / hand-created Secret workflows where the operator
        #    owns rotation.  No env token required in that case.
        check = cmd.kube(
            "get",
            "secret",
            secret_name,
            "--namespace",
            namespace,
            check=False,
        )
        if check.success:
            context.logger.log_info(
                f"HF token secret '{secret_name}' already exists in {namespace}"
            )
            return None

        # 2. No Secret AND no env token -- fail fast with actionable
        #    instructions.  We do this *only* in the kustomize path
        #    because upstream guide manifests now hard-require the
        #    Secret; modelservice/standalone keep their tolerant
        #    behaviour.
        if not hf_token:
            return (
                f"HF_TOKEN is not set and Secret '{secret_name}' does not "
                f"exist in namespace {namespace}.  A HF_TOKEN is now "
                f"required for well-lit-path guides.\n"
                f"\n"
                f"Either:\n"
                f"  1. Export HF_TOKEN (or LLMDBENCH_HF_TOKEN, "
                f"HUGGING_FACE_HUB_TOKEN) before running standup, or\n"
                f"  2. Pre-create the Secret:\n"
                f"     kubectl create secret generic {secret_name} \\\n"
                f"       --from-literal={secret_key}=<your-token> "
                f"-n {namespace}"
            )

        # 3. Have a token, no Secret -- create it without ever putting
        #    the token on a command line.  We build the manifest in
        #    Python, base64-encode the token, write to a temp file
        #    (NamedTemporaryFile is mode 0600 by default on Unix),
        #    apply, and unlink.  The logged kubectl invocation is just
        #    ``kubectl apply -f /tmp/xxx.yaml``.
        token_b64 = base64.b64encode(hf_token.encode("utf-8")).decode("ascii")
        secret_manifest = {
            "apiVersion": "v1",
            "kind": "Secret",
            "type": "Opaque",
            "metadata": {
                "name": secret_name,
                "namespace": namespace,
            },
            "data": {
                secret_key: token_b64,
            },
        }

        tmp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".yaml",
                delete=False,
                prefix="llm-d-hf-token-",
            ) as tmp:
                yaml.safe_dump(secret_manifest, tmp)
                tmp_path = tmp.name

            result = cmd.kube(
                "apply",
                "-f",
                tmp_path,
                "--namespace",
                namespace,
                check=False,
            )
        finally:
            if tmp_path:
                Path(tmp_path).unlink(missing_ok=True)

        if result.success:
            context.logger.log_info(
                f"Created HF token secret '{secret_name}' in {namespace}"
            )
            return None

        # Scrub any accidental token echo from kubectl stderr before
        # surfacing it -- not expected for ``apply -f`` failures but
        # cheap insurance.
        stderr = (result.stderr or "")[:300]
        if hf_token and hf_token in stderr:
            stderr = stderr.replace(hf_token, "<redacted>")
        return (
            f"Failed to create HF token secret '{secret_name}' in "
            f"namespace {namespace}: {stderr}"
        )

    def _fail(
        self,
        errors: list[str],
        stack_path: Path,
        message: str,
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
    def _log_kustomize_authority(
        context: ExecutionContext,
        guide_name: str,
        repo_path: str,
        repo_ref: str,
    ) -> None:
        """Make it explicit that kustomize mode ignores scenario/CLI tuning.

        Under kustomize the deployment is whatever the guide's upstream
        manifests define; nothing from the scenario/CLI/experiment merge
        chain reaches it except the ``kustomize.*`` keys.
        """
        src = f"supplied repo '{repo_path}'" if repo_path else "upstream llm-d.git"
        # One call per row: the logger only decorates the first line of a
        # multi-line message, so per-row calls keep every row aligned under
        # the same prefix.
        w = context.logger.log_warning
        w("kustomize mode — scenario/CLI parameters are NOT applied:")
        w(f"source      : {src} (ref '{repo_ref}'), guide '{guide_name}'")
        w(
            "modifiable  : kustomize.patches, overlayPath, extraHelmValues, extraHelmSets, guideVariableOverrides"
        )
        w(
            "ignored     : -m/--models, model.*, replicas, parallelism, resources, gateway, all other tuning"
        )
        w(
            "experiments : DoE setup sweeps do NOT apply (use kustomize.patches); run/workload treatments do"
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
            yaml.dump(wrapper, default_flow_style=False),
            encoding="utf-8",
        )

        context.logger.log_info(f"Overlay wrapper written to {overlay_dir}")
        return overlay_dir

    def _propagate_standup_parameters(
        self,
        cmd: CommandExecutor,
        context: ExecutionContext,
        plan_config: dict,
        guide_name: str,
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

        create_args = (
            [
                "create",
                "configmap",
                cm_name,
                "--namespace",
                harness_ns,
            ]
            + literal_args
            + ["--dry-run=client", "-o", "yaml"]
        )

        result = cmd.kube(*create_args)
        if result.success:
            yaml_path = context.setup_yamls_dir() / "standup-parameters.yaml"
            yaml_path.write_text(result.stdout, encoding="utf-8")
            apply_result = cmd.kube("apply", "-f", str(yaml_path))
            if apply_result.success:
                context.logger.log_info(
                    f"Deployment metadata saved to configmap/{cm_name} in ns/{harness_ns}"
                )
