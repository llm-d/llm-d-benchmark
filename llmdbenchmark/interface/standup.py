"""CLI definition for the ``standup`` subcommand."""

import argparse
from llmdbenchmark.interface.commands import Command
from llmdbenchmark.interface.env import env, env_int


def add_subcommands(
    parser: argparse._SubParsersAction, parents: list[argparse.ArgumentParser] = []
):
    """Register the ``standup`` subcommand and its arguments."""
    standup_parser = parser.add_parser(
        Command.STANDUP.value,
        parents=parents,
        description=(
            "The `standup` command provisions the model infrastructure for a given specification. "
            "It implicitly generates a plan (YAMLs) and then executes the provisioning steps."
        ),
        help="Standup model infrastructure based on given specification.",
    )
    standup_parser.add_argument(
        "-s",
        "--step",
        help="Step list (comma-separated values or ranges, e.g. 0,1,5 or 1-7).",
    )
    standup_parser.add_argument(
        "-c",
        "--scenario",
        default=env("LLMDBENCH_SCENARIO"),
        help="Scenario file to source environment variables from.",
    )
    standup_parser.add_argument(
        "-m",
        "--models",
        default=env("LLMDBENCH_MODELS"),
        help="List of models to be stood up.",
    )
    standup_parser.add_argument(
        "-p",
        "--namespace",
        default=env("LLMDBENCH_NAMESPACE"),
        help="Namespaces to use (deploy_namespace, benchmark_namespace).",
    )
    standup_parser.add_argument(
        "-t",
        "--methods",
        default=env("LLMDBENCH_METHODS"),
        help="Standup methods (standalone, modelservice, fma, kustomize).",
    )
    standup_parser.add_argument(
        "--gateway-class",
        default=env("LLMDBENCH_GATEWAY_CLASS"),
        help=(
            "Override the scenario's gateway.className. Supported values: "
            "epponly, istio, agentgateway, gke, data-science-gateway-class. "
            "Only takes effect on the modelservice deploy path -- ignored "
            "by kustomize/standalone/fma."
        ),
    )
    standup_parser.add_argument(
        "-a",
        "--affinity",
        default=env("LLMDBENCH_AFFINITY"),
        help="Kubernetes node affinity configuration.",
    )
    standup_parser.add_argument(
        "-b",
        "--annotations",
        default=env("LLMDBENCH_ANNOTATIONS"),
        help="Kubernetes pod annotations.",
    )
    standup_parser.add_argument(
        "-r",
        "--release",
        default=env("LLMDBENCH_RELEASE"),
        help="Modelservice Helm chart release name.",
    )
    standup_parser.add_argument(
        "-u",
        "--wva",
        action="store_true",
        default=False,
        help="Enable Workload Variant Autoscaler (WVA) for this standup.",
    )
    standup_parser.add_argument(
        "--monitoring",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable or disable monitoring. --monitoring creates PodMonitors and enables metrics scraping. --no-monitoring disables PodMonitor and GAIE ServiceMonitor creation (use when cluster lacks Prometheus CRDs). Omit to use scenario defaults.",
    )
    standup_parser.add_argument(
        "--parallel",
        type=int,
        default=env_int("LLMDBENCH_PARALLEL", 4),
        help="Max number of stacks to deploy in parallel (default: 4).",
    )
    standup_parser.add_argument(
        "--stack",
        default=env("LLMDBENCH_STACK"),
        help=(
            "Comma-separated list of stack names to restrict execution to. "
            "Default: unset, meaning 'deploy every stack of the scenario'. "
            "Useful for re-deploying a single pool in a multi-stack scenario "
            "without tearing down siblings."
        ),
    )
    standup_parser.add_argument(
        "--kubeconfig",
        "-k",
        default=env("LLMDBENCH_KUBECONFIG") or env("KUBECONFIG"),
        help="Path to kubeconfig file for kubectl/helm/helmfile commands.",
    )
    standup_parser.add_argument(
        "--skip-smoketest",
        action="store_true",
        default=False,
        help="Skip automatic smoketest after standup completes.",
    )
    standup_parser.add_argument(
        "--standalone-deploy-timeout",
        type=int,
        default=env_int("LLMDBENCH_STANDALONE_DEPLOY_TIMEOUT"),
        help="Seconds to wait for the vLLM pods to deploy during standup in standalone mode.",
    )
    standup_parser.add_argument(
        "--gateway-deploy-timeout",
        type=int,
        default=env_int("LLMDBENCH_GATEWAY_DEPLOY_TIMEOUT"),
        help="Seconds to wait for gateway infrastructure pods to deploy during standup with modelservice.",
    )
    standup_parser.add_argument(
        "--modelservice-deploy-timeout",
        type=int,
        default=env_int("LLMDBENCH_MODELSERVICE_DEPLOY_TIMEOUT"),
        help="Seconds to wait for decode, prefill and inference pool pods to deploy during standup with modelservice.",
    )
    standup_parser.add_argument(
        "--pvc-bind-timeout",
        type=int,
        default=env_int("LLMDBENCH_PVC_BIND_TIMEOUT"),
        help="Seconds to wait for each PVC (workload, model, extra) to reach "
        "the Bound phase during standup. A PVC that never binds (e.g. no "
        "default StorageClass on the cluster) fails fast instead of "
        "masquerading as a downstream pod/job timeout. Default: 240 "
        "(some dynamic provisioners take 1-3 minutes per volume).",
    )
    standup_parser.add_argument(
        "--llmd-repo-path",
        default=env("LLMDBENCH_LLMD_REPO_PATH"),
        help="Path to a local llm-d repository clone (used by the kustomize method).",
    )
    standup_parser.add_argument(
        "--kustomize-deploy-timeout",
        type=int,
        default=env_int("LLMDBENCH_KUSTOMIZE_DEPLOY_TIMEOUT"),
        help="Seconds to wait for pods to deploy during standup in kustomize mode.",
    )
    standup_parser.add_argument(
        "--full-infra",
        action="store_true",
        default=False,
        help=(
            "Run the full infrastructure setup (steps 2-5: admin prerequisites, "
            "monitoring validation, model namespace, harness namespace) even when "
            "using the kustomize deployment method. By default, kustomize mode "
            "skips these steps because the guide README handles its own "
            "prerequisites (CRDs, namespace). Use this flag when you need the "
            "benchmark harness infrastructure (PVCs, download jobs, data-access "
            "pods) alongside the kustomize-deployed model."
        ),
    )
