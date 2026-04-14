"""CLI definition for the ``standup`` subcommand."""

import argparse
from llmdbenchmark.interface.commands import Command
from llmdbenchmark.interface.env import env, env_int


def add_subcommands(parser: argparse._SubParsersAction):
    """Register the ``standup`` subcommand and its arguments."""
    standup_parser = parser.add_parser(
        Command.STANDUP.value,
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
        help="Standup methods (standalone, modelservice).",
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
        default=env("LLMDBENCH_WVA"),
        help="Enable Workload Variant Autoscaler.",
    )
    standup_parser.add_argument(
        "-f",
        "--monitoring",
        action="store_true",
        default=False,
        help="Enable PodMonitor for Prometheus and vLLM /metrics scraping.",
    )
    standup_parser.add_argument(
        "--parallel",
        type=int,
        default=env_int("LLMDBENCH_PARALLEL", 4),
        help="Max number of stacks to deploy in parallel (default: 4).",
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
