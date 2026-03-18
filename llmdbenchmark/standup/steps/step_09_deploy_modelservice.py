"""Step 09 -- Deploy the model via the llm-d modelservice Helm chart."""

import base64
import random
import tempfile
from pathlib import Path

import yaml

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.executor.command import CommandExecutor


# Known GPU accelerator prefixes for WVA auto-detection
_ACCELERATOR_PREFIXES = ["G2", "A100", "H100", "L40S", "MI300X"]


class DeployModelserviceStep(Step):
    """Deploy the model via the llm-d modelservice Helm chart."""

    def __init__(self):
        super().__init__(
            number=9,
            name="deploy_modelservice",
            description="Deploy model via modelservice Helm chart",
            phase=Phase.STANDUP,
            per_stack=True,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        return "modelservice" not in context.deployed_methods

    def execute(  # pylint: disable=too-many-branches,too-many-locals,too-many-statements
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

        errors = []
        cmd = context.require_cmd()

        namespace = context.require_namespace()
        stack_name = stack_path.name

        plan_config = self._load_stack_config(stack_path)
        release = self._require_config(plan_config, "release")
        model_short = self._require_config(plan_config, "model", "shortName")
        inference_port = self._require_config(plan_config, "vllmCommon", "inferencePort")

        if not context.dry_run:
            pc_error = self._check_priority_class(cmd, plan_config, context)
            if pc_error:
                errors.append(pc_error)
                return StepResult(
                    step_number=self.number,
                    step_name=self.name,
                    success=False,
                    message="PriorityClass validation failed",
                    errors=errors,
                    stack_name=stack_name,
                )

        if context.is_openshift and not context.non_admin:
            self._manage_sccs(cmd, context, plan_config, namespace)

        ms_values = self._find_yaml(stack_path, "13_ms-values")
        if not ms_values:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message="No modelservice values found, skipping",
                stack_name=stack_name,
            )

        helm_dir = context.setup_helm_dir() / stack_name
        helmfile_work = helm_dir / "helmfile.yaml"

        if helmfile_work.exists():
            result = cmd.helmfile(
                "--namespace",
                namespace,
                "--selector",
                f"name={model_short}-ms",
                "apply",
                "-f",
                str(helmfile_work),
                "--skip-diff-on-install",
                "--skip-schema-validation",
            )
            if not result.success:
                errors.append(f"Failed to deploy modelservice: {result.stderr}")
        else:
            main_helmfile = self._find_yaml(stack_path, "10_helmfile-main")
            if main_helmfile:
                result = cmd.helmfile(
                    "--namespace",
                    namespace,
                    "--selector",
                    f"name={model_short}-ms",
                    "apply",
                    "-f",
                    str(main_helmfile),
                    "--skip-diff-on-install",
                    "--skip-schema-validation",
                )
                if not result.success:
                    errors.append(f"Failed to deploy modelservice: {result.stderr}")

        httproute_yaml = self._find_yaml(stack_path, "08_httproute")
        if httproute_yaml and self._has_yaml_content(httproute_yaml):
            result = cmd.kube("apply", "-f", str(httproute_yaml))
            if not result.success:
                errors.append(f"Failed to apply HTTPRoute: {result.stderr}")

        if not errors:
            decode_wait = cmd.wait_for_pods(
                label="llm-d.ai/role=decode",
                namespace=namespace,
                timeout=900,
                poll_interval=10,
                description="decode pods",
            )
            if not decode_wait.success:
                errors.append(f"Decode pods not ready: {decode_wait.stderr}")

            decode_cfg = plan_config.get("decode", {})
            expected_replicas = int(self._require_config(plan_config, "decode", "replicas"))
            is_multinode = plan_config.get("multinode", {}).get("enabled", False)
            if is_multinode:
                workers = int(self._require_config(plan_config, "decode", "parallelism", "workers"))
                expected_replicas = expected_replicas * workers
            if expected_replicas > 1 and not context.dry_run:
                pod_count_result = cmd.kube(
                    "get",
                    "pods",
                    "-l",
                    "llm-d.ai/role=decode",
                    "--namespace",
                    namespace,
                    "-o",
                    "jsonpath={.items[*].metadata.name}",
                )
                if pod_count_result.success:
                    actual_count = (
                        len(pod_count_result.stdout.strip().split())
                        if pod_count_result.stdout.strip()
                        else 0
                    )
                    if actual_count < expected_replicas:
                        context.logger.log_warning(
                            f"⚠️  Expected {expected_replicas} decode pods "
                            f"but found {actual_count}"
                        )
                    else:
                        context.logger.log_info(
                            f"✅ Decode pod count: {actual_count}/{expected_replicas}"
                        )

            prefill_enabled = self._require_config(plan_config, "prefill", "enabled")
            prefill_replicas = int(self._require_config(plan_config, "prefill", "replicas"))

            if prefill_enabled and prefill_replicas > 0:
                prefill_wait = cmd.wait_for_pods(
                    label="llm-d.ai/role=prefill",
                    namespace=namespace,
                    timeout=900,
                    poll_interval=10,
                    description="prefill pods",
                )
                if not prefill_wait.success:
                    errors.append(f"Prefill pods not ready: {prefill_wait.stderr}")

            pool_wait = cmd.wait_for_pods(
                label=f"inferencepool={model_short}-gaie-epp",
                namespace=namespace,
                timeout=300,
                poll_interval=10,
                description="inference pool",
            )
            if not pool_wait.success:
                stderr_lower = pool_wait.stderr.lower()
                if (
                    "no matching resources found" not in stderr_lower
                    and "no pods found" not in stderr_lower
                ):
                    errors.append(f"Inference pool not ready: {pool_wait.stderr}")

        if not errors and not context.dry_run:
            self._collect_logs(cmd, context, namespace)

        if context.non_admin:
            context.logger.log_info("ℹ️  Non-admin: skipping PodMonitor creation")
        else:
            podmonitor_yaml = self._find_yaml(stack_path, "17_podmonitor")
            if not podmonitor_yaml:
                podmonitor_yaml = self._find_yaml(stack_path, "18_podmonitor")
            if podmonitor_yaml and self._has_yaml_content(podmonitor_yaml):
                result = cmd.kube("apply", "-f", str(podmonitor_yaml))
                if not result.success:
                    context.logger.log_warning(
                        f"PodMonitor apply failed (non-fatal): {result.stderr}"
                    )
                else:
                    context.logger.log_info(
                        "PodMonitor created for Prometheus scraping"
                    )
            else:
                context.logger.log_info(
                    "PodMonitor skipped (template not rendered for this configuration)"
                )

        gateway_class = self._require_config(plan_config, "gateway", "className")

        if gateway_class == "kgateway":
            service_name = f"infra-{release}-inference-gateway"
        else:
            service_name = f"{model_short}-gaie-epp"

        context.deployed_endpoints[stack_name] = service_name

        username = context.username or "unknown"
        cmd.kube(
            "label",
            f"gateway/infra-{release}-inference-gateway",
            f"stood-up-by={username}",
            "stood-up-from=llm-d-benchmark",
            "stood-up-via=modelservice",
            "--namespace",
            namespace,
            "--overwrite",
        )

        # GAIE Helm chart creates a route to the EPP gRPC port (wrong for
        # inference). We replace it with one targeting the gateway on port 80.
        # data-science-gateway-class manages its own route.
        if context.is_openshift and gateway_class != "data-science-gateway-class":
            route_name = f"{release}-inference-gateway-route"

            if gateway_class == "kgateway":
                route_service = f"infra-{release}-inference-gateway"
            else:  # istio
                route_service = f"infra-{release}-inference-gateway-istio"

            cmd.kube(
                "delete",
                "route",
                route_name,
                "-n",
                namespace,
                "--ignore-not-found",
                check=False,
            )

            cmd.kube(
                "expose",
                f"service/{route_service}",
                f"--name={route_name}",
                "--target-port=80",
                "-n",
                namespace,
            )
            context.logger.log_info(
                f"OpenShift route '{route_name}' created to "
                f"service/{route_service}:80"
            )

        wva_config = plan_config.get("wva", {})
        if wva_config.get("enabled", False) and context.is_openshift:
            self._install_wva(cmd, context, plan_config, stack_path, errors)
        elif wva_config.get("enabled", False) and not context.is_openshift:
            context.logger.log_info(
                "ℹ️  WVA is enabled but platform is not OpenShift — "
                "skipping WVA installation (not yet verified on non-OCP)"
            )

        self._propagate_standup_parameters(cmd, context, plan_config)

        if not errors:
            resource_types = "deployment,service,pods,gateway,httproute"
            if context.is_openshift:
                resource_types += ",route"
            cmd.kube(
                "get",
                resource_types,
                "--namespace",
                namespace,
            )

        if errors:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="Modelservice deployment had errors",
                errors=errors,
                stack_name=stack_name,
            )

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=f"Modelservice deployed for {stack_name}",
            stack_name=stack_name,
        )

    def _check_priority_class(
        self,
        cmd: CommandExecutor,
        plan_config: dict,
        context: ExecutionContext,
    ) -> str | None:
        """Validate that the configured priorityClassName exists on the cluster."""
        vllm_common_pc = plan_config.get("vllmCommon", {}).get("priorityClassName", "")

        classes_to_check = set()
        for section in ["decode", "prefill"]:
            pc = plan_config.get(section, {}).get("priorityClassName") or vllm_common_pc
            if pc and pc.lower() != "none":
                classes_to_check.add(pc)

        if not classes_to_check:
            return None

        for priority_class in classes_to_check:
            result = cmd.kube(
                "get",
                "priorityclass",
                priority_class,
                "--ignore-not-found",
                "-o",
                "jsonpath={.metadata.name}",
                check=False,
            )
            if result.success and result.stdout.strip() == priority_class:
                context.logger.log_info(
                    f'PriorityClass "{priority_class}" found on cluster'
                )
                continue

            list_result = cmd.kube(
                "get",
                "priorityclass",
                "-o",
                "jsonpath={.items[*].metadata.name}",
                check=False,
            )
            available = (
                list_result.stdout.strip()
                if list_result.success
                else "(unable to list)"
            )
            return (
                f'PriorityClass "{priority_class}" does not exist on this '
                f"cluster. Available priority classes: {available}"
            )

        return None

    def _manage_sccs(
        self,
        cmd: CommandExecutor,
        context: ExecutionContext,
        plan_config: dict,
        namespace: str,
    ):
        """Add anyuid/privileged SCCs when ``runAsUser: 0`` or ``runAsGroup: 0``."""
        needs_elevated = False

        sections_to_check = [
            plan_config.get("standalone", {}),
            plan_config.get("vllmCommon", {}),
            plan_config.get("decode", {}),
            plan_config.get("prefill", {}),
        ]
        for role in ["Decode", "Prefill"]:
            sections_to_check.append(plan_config.get(f"vllmModelservice{role}", {}))

        for section in sections_to_check:
            # Check top-level securityContext
            sc = section.get("securityContext", {})
            if sc.get("runAsUser") == 0 or sc.get("runAsGroup") == 0:
                needs_elevated = True
                break
            # Check securityContext inside extraContainerConfig (used by
            # modelservice Helm chart for container-level security settings)
            extra_sc = (
                section.get("extraContainerConfig", {})
                .get("securityContext", {})
            )
            if extra_sc.get("runAsUser") == 0 or extra_sc.get("runAsGroup") == 0:
                needs_elevated = True
                break

        if not needs_elevated:
            context.logger.log_info(
                "ℹ️  No runAsUser:0 detected — skipping SCC assignment"
            )
            return

        # The Helm chart creates a SA named after fullnameOverride (= model.shortName).
        # If serviceAccountOverride is set, the chart uses that instead.
        sa_override = plan_config.get("serviceAccountOverride", "")
        if sa_override:
            sa_name = sa_override
        else:
            sa_name = self._require_config(plan_config, "model", "shortName")

        context.logger.log_info(
            f"Assigning anyuid/privileged SCCs to SA '{sa_name}' "
            f"in namespace {namespace}"
        )
        for scc in ["anyuid", "privileged"]:
            cmd.kube(
                "adm",
                "policy",
                "add-scc-to-user",
                scc,
                "-z",
                sa_name,
                "-n",
                namespace,
            )

    def _collect_logs(
        self, cmd: CommandExecutor, context: ExecutionContext, namespace: str
    ):
        """Collect decode and prefill pod logs after deployment."""
        logs_dir = context.setup_logs_dir()
        for role in ["decode", "prefill"]:
            result = cmd.kube(
                "get",
                "pods",
                "-l",
                f"llm-d.ai/role={role}",
                "--namespace",
                namespace,
                "-o",
                "jsonpath={.items[*].metadata.name}",
            )
            if result.success and result.stdout.strip():
                pod_names = result.stdout.strip().split()
                for pod_name in pod_names:
                    log_result = cmd.kube(
                        "logs",
                        pod_name,
                        "--namespace",
                        namespace,
                        "--tail=-1",
                    )
                    if log_result.success:
                        log_file = logs_dir / f"{pod_name}.log"
                        log_file.write_text(log_result.stdout, encoding="utf-8")

    def _install_wva(  # pylint: disable=too-many-arguments,too-many-locals
        self,
        cmd: CommandExecutor,
        context: ExecutionContext,
        plan_config: dict,
        stack_path: Path,
        errors: list,
    ):
        """Install WVA and prometheus-adapter, patching runtime values into rendered templates."""
        namespace = context.require_namespace()
        wva_cfg = plan_config.get("wva", {})

        wva_namespace = context.wva_namespace or wva_cfg.get("namespace") or namespace

        monitoring_ns = self._require_config(
            plan_config, "openshiftMonitoring", "userWorkloadMonitoringNamespace"
        )

        ns_yaml = self._find_yaml(stack_path, "23_wva-namespace")
        if ns_yaml and self._has_yaml_content(ns_yaml):
            cmd.kube("apply", "-f", str(ns_yaml), check=False)
        else:
            context.logger.log_warning(
                "WVA namespace template (23_wva-namespace) not found — "
                "creating namespace inline"
            )
            cmd.kube(
                "create",
                "namespace",
                wva_namespace,
                check=False,
            )

        wva_values_yaml = self._find_yaml(stack_path, "19_wva-values")
        if not wva_values_yaml:
            errors.append(
                "WVA values template (19_wva-values) not found — " "cannot install WVA"
            )
            return

        wva_config = yaml.safe_load(wva_values_yaml.read_text(encoding="utf-8"))
        if not wva_config:
            errors.append("WVA values template rendered empty — is wva.enabled set?")
            return

        prom_ca_cert = self._extract_prometheus_ca_cert(cmd, context)
        if not prom_ca_cert:
            context.logger.log_warning(
                "Could not extract Prometheus CA cert from "
                "thanos-querier-tls secret — WVA may not connect to Prometheus"
            )
        if prom_ca_cert and "wva" in wva_config and "prometheus" in wva_config["wva"]:
            wva_config["wva"]["prometheus"]["caCert"] = prom_ca_cert

        affinity_str = ""
        decode_cfg = plan_config.get("decode", {})
        accel_types = decode_cfg.get("acceleratorType", {})
        if accel_types:
            label_values = accel_types.get("labelValues", [])
            if label_values:
                affinity_str = str(label_values[0])
            elif accel_types.get("labelValue"):
                affinity_str = str(accel_types["labelValue"])
        accelerator_type = self._find_accelerator_prefix(affinity_str) or ""
        if "va" in wva_config:
            wva_config["va"]["accelerator"] = accelerator_type

        vllm_svc_cfg = wva_cfg.get("vllmService", {})
        node_port_min = int(self._require_config(plan_config, "wva", "vllmService", "nodePortMin"))
        node_port_max = int(self._require_config(plan_config, "wva", "vllmService", "nodePortMax"))
        node_port = self._get_random_node_port(
            cmd, namespace, node_port_min, node_port_max
        )
        if "vllmService" in wva_config:
            wva_config["vllmService"]["nodePort"] = node_port

        tmp_dir = Path(tempfile.mkdtemp())
        wva_values_path = tmp_dir / "wva_config.yaml"
        wva_values_path.write_text(
            yaml.dump(wva_config, sort_keys=False), encoding="utf-8"
        )

        wva_chart = plan_config.get("helmRepositories", {}).get("wva", {})
        chart_url = wva_chart.get("url", "")
        chart_version = plan_config.get("chartVersions", {}).get("wva", "")

        if chart_url and chart_version:
            result = cmd.helm(
                "upgrade",
                "--install",
                "workload-variant-autoscaler",
                chart_url,
                "--version",
                chart_version,
                "--namespace",
                wva_namespace,
                "-f",
                str(wva_values_path),
            )
            if not result.success:
                errors.append(f"Failed to install WVA: {result.stderr}")
        else:
            errors.append(
                "WVA chart URL or version not configured — "
                "check helmRepositories.wva and chartVersions.wva"
            )

        if prom_ca_cert:
            self._install_prometheus_adapters(
                cmd,
                context,
                stack_path=stack_path,
                monitoring_ns=monitoring_ns,
                prom_ca_cert=prom_ca_cert,
                tmp_dir=tmp_dir,
                errors=errors,
            )
        else:
            context.logger.log_warning(
                "Skipping prometheus-adapter install — no CA cert available"
            )

    def _install_prometheus_adapters(
        self,
        cmd: CommandExecutor,
        context: ExecutionContext,
        stack_path: Path,
        monitoring_ns: str,
        prom_ca_cert: str,
        tmp_dir: Path,
        errors: list,
    ):
        """Install prometheus-adapter using pre-rendered templates."""
        cert_path = tmp_dir / "prometheus-ca.crt"
        cert_path.write_text(prom_ca_cert, encoding="utf-8")

        result = cmd.kube(
            "create",
            "configmap",
            "prometheus-ca",
            f"--from-file=ca.crt={cert_path}",
            "--dry-run=client",
            "-o",
            "yaml",
            namespace=monitoring_ns,
            check=False,
        )
        if result.success and result.stdout.strip():
            cm_yaml_path = tmp_dir / "prometheus-ca-configmap.yaml"
            cm_yaml_path.write_text(result.stdout, encoding="utf-8")
            apply_result = cmd.kube(
                "apply",
                "-f",
                str(cm_yaml_path),
                namespace=monitoring_ns,
                check=False,
            )
            if not apply_result.success:
                context.logger.log_warning(
                    f"prometheus-ca ConfigMap apply failed: {apply_result.stderr}"
                )
        elif not result.success:
            context.logger.log_warning(
                f"prometheus-ca ConfigMap creation failed: {result.stderr}"
            )

        cmd.helm(
            "repo",
            "add",
            "prometheus-community",
            "https://prometheus-community.github.io/helm-charts",
            check=False,
        )
        cmd.helm("repo", "update", check=False)

        adapter_values = self._find_yaml(stack_path, "21_prometheus-adapter-values")
        if adapter_values:
            result = cmd.helm(
                "upgrade",
                "--install",
                "prometheus-adapter",
                "prometheus-community/prometheus-adapter",
                "--namespace",
                monitoring_ns,
                "-f",
                str(adapter_values),
            )
            if not result.success:
                errors.append(f"Failed to install prometheus-adapter: {result.stderr}")
        else:
            errors.append(
                "prometheus-adapter values template (21_prometheus-adapter-values) "
                "not found"
            )

        rbac_yaml = self._find_yaml(stack_path, "22_prometheus-rbac")
        if rbac_yaml and self._has_yaml_content(rbac_yaml):
            result = cmd.kube("apply", "-f", str(rbac_yaml), check=False)
            if not result.success:
                context.logger.log_warning(
                    f"ClusterRole creation failed (non-fatal): {result.stderr}"
                )
        else:
            context.logger.log_warning(
                "prometheus RBAC template (22_prometheus-rbac) not found"
            )

    def _extract_prometheus_ca_cert(
        self, cmd: CommandExecutor, context: ExecutionContext
    ) -> str | None:
        """Extract Prometheus CA cert from thanos-querier-tls secret."""
        result = cmd.kube(
            "get",
            "secret",
            "thanos-querier-tls",
            "--namespace",
            "openshift-monitoring",
            "-o",
            "jsonpath={.data.tls\\.crt}",
            check=False,
        )
        if not result.success or not result.stdout.strip():
            return None

        try:
            cert_bytes = base64.b64decode(result.stdout.strip())
            cert_str = cert_bytes.decode("utf-8")
            if not cert_str.endswith("\n"):
                cert_str += "\n"
            return cert_str
        except Exception as exc:
            context.logger.log_warning(f"Failed to decode CA cert: {exc}")
            return None

    @staticmethod
    def _find_accelerator_prefix(affinity_string: str) -> str | None:
        """Find the first known accelerator prefix in the affinity string."""
        if not affinity_string:
            return None
        for prefix in _ACCELERATOR_PREFIXES:
            if prefix in affinity_string:
                return prefix
        return None

    @staticmethod
    def _get_random_node_port(
        cmd: CommandExecutor,
        namespace: str,
        min_port: int = 30000,
        max_port: int = 32767,
    ) -> int:
        """Return a random available NodePort in the given range."""
        existing_ports: set[int] = set()
        result = cmd.kube(
            "get",
            "services",
            "--all-namespaces",
            "-o",
            "jsonpath={.items[*].spec.ports[*].nodePort}",
            check=False,
        )
        if result.success and result.stdout.strip():
            for port_str in result.stdout.strip().split():
                try:
                    existing_ports.add(int(port_str))
                except ValueError:
                    continue

        for _ in range(100):
            candidate = random.randint(min_port, max_port)
            if candidate not in existing_ports:
                return candidate

        return random.randint(min_port, max_port)

    def _propagate_standup_parameters(
        self, cmd: CommandExecutor, context: ExecutionContext, plan_config: dict
    ):
        """Persist deploy metadata as a ConfigMap so run-phase steps can read it."""
        from datetime import datetime, timezone
        from llmdbenchmark import __version__

        harness_ns = context.harness_namespace or context.require_namespace()
        cm_name = "llm-d-benchmark-standup-parameters"

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
        }

        if plan_config:
            params["model_name"] = self._require_config(plan_config, "model", "name")
            params["model_short_name"] = self._require_config(plan_config, "model", "shortName")
            params["model_huggingface_id"] = plan_config.get("model", {}).get("huggingfaceId", "")
            params["inference_port"] = str(
                self._require_config(plan_config, "vllmCommon", "inferencePort")
            )
            params["release"] = self._require_config(plan_config, "release")
            params["decode_replicas"] = str(
                self._require_config(plan_config, "decode", "replicas")
            )
            params["prefill_enabled"] = str(
                self._require_config(plan_config, "prefill", "enabled")
            ).lower()
            params["prefill_replicas"] = str(
                self._require_config(plan_config, "prefill", "replicas")
            )
            chart_versions = plan_config.get("chartVersions", {})
            if chart_versions:
                params["chart_version_modelservice"] = chart_versions.get(
                    "llmDModelservice", ""
                )
                params["chart_version_inference_pool"] = chart_versions.get(
                    "inferencePool", ""
                )
                params["chart_version_gaie"] = chart_versions.get("gaie", "")
                params["chart_version_llm_d_infra"] = chart_versions.get(
                    "llmDInfra", ""
                )

            # Container images used in this deployment
            images = plan_config.get("images", {})
            vllm_img = images.get("vllm", {})
            if vllm_img:
                repo = vllm_img.get("repository", "")
                tag = vllm_img.get("tag", "")
                params["image_vllm"] = f"{repo}:{tag}" if repo else ""

            decode_img = plan_config.get("decode", {}).get("image", {})
            if decode_img and decode_img.get("repository"):
                params["image_decode"] = (
                    f"{decode_img['repository']}:{decode_img.get('tag', 'latest')}"
                )

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
                    f"📋 Deployment metadata → configmap/{cm_name} in ns/{harness_ns}"
                )
                context.logger.log_info(
                    f"   oc get configmap {cm_name} -n {harness_ns} -o yaml"
                )
