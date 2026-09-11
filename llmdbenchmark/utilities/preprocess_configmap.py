"""Bundle preprocess scripts into a ConfigMap and apply it per namespace.

Shared by standup step 04 (model namespace -- serving pods' preprocess
containers mount it) and run step 02 (harness namespace -- harness pods
mount it). Extracted from the former standup step 05.
"""

from llmdbenchmark.executor.command import CommandExecutor
from llmdbenchmark.executor.context import ExecutionContext


def create_preprocess_configmap(
    cmd: CommandExecutor, context: ExecutionContext, namespaces: list[str]
) -> None:
    """Bundle preprocess scripts into a ConfigMap and apply to each namespace."""
    preprocess_dir = context.preprocess_dir()
    config_map_name = "llm-d-benchmark-preprocesses"

    context.logger.log_info("🚚 Creating configmap with preprocess scripts...")

    if not preprocess_dir or not preprocess_dir.is_dir():
        context.logger.log_warning(
            "Preprocess directory not found -- creating empty ConfigMap"
        )
        for ns in namespaces:
            result = cmd.kube(
                "create",
                "configmap",
                config_map_name,
                "--namespace",
                ns,
                "--dry-run=client",
                "-o",
                "yaml",
            )
            if result.success:
                yaml_path = (
                    context.setup_yamls_dir()
                    / f"preprocesses-configmap-empty-{ns}.yaml"
                )
                yaml_path.write_text(result.stdout, encoding="utf-8")
                cmd.kube("apply", "-f", str(yaml_path))
        return

    from_file_args = []
    file_paths = []
    try:
        file_paths = sorted(p for p in preprocess_dir.rglob("*") if p.is_file())
        for path in file_paths:
            from_file_args.extend(
                [
                    f"--from-file={path.name}={path}",
                ]
            )
    except OSError as exc:
        context.logger.log_warning(f"Error reading preprocess directory: {exc}")

    if not from_file_args:
        context.logger.log_info("No preprocess files found -- creating empty ConfigMap")
        return

    for ns in namespaces:
        create_args = (
            [
                "create",
                "configmap",
                config_map_name,
                "--namespace",
                ns,
            ]
            + from_file_args
            + ["--dry-run=client", "-o", "yaml"]
        )

        result = cmd.kube(*create_args)
        if result.success:
            yaml_path = context.setup_yamls_dir() / f"preprocesses-configmap-{ns}.yaml"
            yaml_path.write_text(result.stdout, encoding="utf-8")
            apply_result = cmd.kube("apply", "-f", str(yaml_path))
            if not apply_result.success:
                context.logger.log_warning(
                    f"Failed to apply preprocesses configmap in ns/{ns}: "
                    f"{apply_result.stderr}"
                )
            else:
                context.logger.log_info(
                    f'📦 ConfigMap "{config_map_name}" created in ns/{ns} '
                    f"with {len(file_paths)} file(s):"
                )
                for path in file_paths:
                    size_kb = path.stat().st_size / 1024
                    context.logger.log_info(f"    │ {path.name} ({size_kb:.1f} KB)")
        else:
            context.logger.log_warning(
                f"Failed to generate preprocesses configmap for ns/{ns}: {result.stderr}"
            )
