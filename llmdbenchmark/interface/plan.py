"""CLI definition for the ``plan`` subcommand."""

import argparse
from llmdbenchmark.interface.commands import Command
from llmdbenchmark.interface.env import env


def add_subcommands(
    parser: argparse._SubParsersAction, parents: list[argparse.ArgumentParser] = []
):
    """Register the ``plan`` subcommand and its arguments.

    The plan subcommand renders templates without applying anything to the
    cluster. Flags that influence rendering (namespace, model, deploy method,
    monitoring) are accepted here so the rendered output matches what standup
    would produce with the same flags.
    """
    plan_parser = parser.add_parser(
        Command.PLAN.value,
        parents=parents,
        description=(
            "The `plan` command generates a complete plan for a model infrastructure. "
            "It produces YAML and Helm manifests required for provisioning, "
            "but does not execute any actions on the cluster. "
            "This is useful for reviewing and validating the plan before deployment."
        ),
        help="Generate only the plan for the model infrastructure.",
    )
    plan_parser.add_argument(
        "-p",
        "--namespace",
        default=env("LLMDBENCH_NAMESPACE"),
        help="Namespaces to use (deploy_namespace, benchmark_namespace).",
    )
    plan_parser.add_argument(
        "-m",
        "--models",
        default=env("LLMDBENCH_MODELS"),
        help="Model to render the plan for.",
    )
    plan_parser.add_argument(
        "-t",
        "--methods",
        default=env("LLMDBENCH_METHODS"),
        help="Deployment method (standalone, modelservice, fma).",
    )
    plan_parser.add_argument(
        "--gateway-class",
        default=env("LLMDBENCH_GATEWAY_CLASS"),
        help=(
            "Override the scenario's gateway.className. Supported values: "
            "none, epponly (alias: standalone), istio, agentgateway, gke, "
            "data-science-gateway-class. "
            "Only takes effect on the modelservice deploy path."
        ),
    )
    plan_parser.add_argument(
        "--hostpath",
        metavar="PATH",
        default=env("LLMDBENCH_HOSTPATH"),
        help="Directory on the worker nodes that holds the model weights, "
        "mounted directly into the serving pods as a hostPath volume (no "
        "model PV/PVC); the download DaemonSet fills it on every target "
        "node. Sets storage.hostPath.enabled=true and storage.hostPath.path. "
        "'none' sets storage.hostPath.enabled=false. Wins over --set and "
        "DoE treatments for these keys. Omitted: the "
        "scenario's storage.hostPath is used (disabled by default) "
        "(env: LLMDBENCH_HOSTPATH). Default: none.",
    )
    plan_parser.add_argument(
        "--network",
        metavar="NETWORKS",
        default=env("LLMDBENCH_NETWORK"),
        help="Value for the k8s.v1.cni.cncf.io/networks (Multus) pod "
        "annotation on every serving pod: modelservice decode and prefill, "
        "standalone, and the FMA launcher. Shortcut for --set of "
        "annotations.decode.pod, annotations.prefill.pod, "
        "standalone.podAnnotations and "
        "fma.launcher.podTemplate.metadata.annotations. 'none' removes the "
        "annotation. Brace groups expand like bash: quote the value, e.g. "
        "'compute-p{1..4}v0' -> compute-p1v0,...,compute-p4v0 and "
        "'net-{a,b}' -> net-a,net-b. "
        "Wins over --set and DoE treatments for these keys. "
        "Omitted: the scenario's annotations are used "
        "(env: LLMDBENCH_NETWORK). Default: none.",
    )
    plan_parser.add_argument(
        "-f",
        "--monitoring",
        action="store_true",
        default=False,
        help="Enable monitoring in rendered templates (PodMonitor, EPP verbosity).",
    )
    plan_parser.add_argument(
        "--kubeconfig",
        "-k",
        default=env("LLMDBENCH_KUBECONFIG") or env("KUBECONFIG"),
        help="Path to kubeconfig file (used for cluster resource auto-detection).",
    )
