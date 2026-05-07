"""Command to list staged and untracked benchmark runs."""

import sys
from pathlib import Path
from llmdbenchmark.result_store.store import StoreManager, StoreNotFound
from llmdbenchmark.result_store.workspace import WorkspaceManager
from llmdbenchmark.result_store.utils import color_pad
from llmdbenchmark.result_store.commands import register_command

@register_command("status")
def execute(args, logger):
    try:
        store_root = StoreManager.find_store_root()
    except StoreNotFound as exception:
        logger.log_error(str(exception))
        sys.exit(1)

    workspace_manager = WorkspaceManager()
    staged_runs = workspace_manager.list_staged()
    staged_paths = {run["path"] for run in staged_runs}

    workspaces_dir = store_root / "workspaces"
    staged_by_workspace = {}
    for staged_run in staged_runs:
        path = Path(staged_run['path'])
        workspace_name = path.parent.parent.name if len(path.parts) >= 3 else "unknown"
        staged_by_workspace.setdefault(workspace_name, []).append(staged_run)

    untracked_by_workspace = {}
    if workspaces_dir.exists():
        for workspace in workspaces_dir.iterdir():
            if not workspace.is_dir():
                continue
            results_dir = workspace / "results"
            if not results_dir.exists() or not results_dir.is_dir():
                continue
            
            for exp_dir in results_dir.iterdir():
                if not exp_dir.is_dir():
                    continue
                
                path_str = str(exp_dir.resolve())
                if path_str not in staged_paths:
                    report_info = workspace_manager._parse_report(exp_dir)
                    report_info.update({"path": path_str, "status": "untracked"})
                    if report_info.get("run_uid") == "missing" and report_info.get("scenario") == "missing":
                        logger.log_warning(f"Malformed or empty report at {exp_dir}. Skipping.")
                        continue
                    
                    workspace_name = workspace.name
                    untracked_by_workspace.setdefault(workspace_name, []).append(report_info)

    if not staged_runs and not untracked_by_workspace:
        logger.log_plain(f"No benchmark runs found in {workspaces_dir}.")
        return

    if staged_by_workspace:
        logger.log_plain("Changes to be pushed (staged):")
        logger.log_plain(f"{'Run UID':<10} | {'Scenario':<25} | {'Model':<30} | {'Hardware':<15}")
        logger.log_plain("-" * 90)
        for workspace_name in sorted(staged_by_workspace.keys()):
            logger.log_plain(f"[{workspace_name}]")
            for staged_run in staged_by_workspace[workspace_name]:
                short_uid = staged_run['run_uid'][:8] if len(staged_run['run_uid']) > 8 else staged_run['run_uid']
                logger.log_plain(f"  {color_pad(short_uid, 8)} | {color_pad(staged_run['scenario'], 25)} | {color_pad(staged_run['model'], 30)} | {color_pad(staged_run['hardware'], 15)}")
        logger.log_plain("")

    if untracked_by_workspace:
        logger.log_plain("Untracked results:")
        logger.log_plain(f"{'Run UID':<10} | {'Scenario':<25} | {'Model':<30} | {'Hardware':<15}")
        logger.log_plain("-" * 90)
        for workspace_name in sorted(untracked_by_workspace.keys()):
            logger.log_plain(f"[{workspace_name}]")
            for untracked_run in untracked_by_workspace[workspace_name]:
                short_uid = untracked_run['run_uid'][:8] if len(untracked_run['run_uid']) > 8 else untracked_run['run_uid']
                logger.log_plain(f"  {color_pad(short_uid, 8)} | {color_pad(untracked_run['scenario'], 25)} | {color_pad(untracked_run['model'], 30)} | {color_pad(untracked_run['hardware'], 15)}")
