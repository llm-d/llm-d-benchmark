"""Command to unstage tracked benchmark runs."""

import sys
import fnmatch
from pathlib import Path
from llmdbenchmark.result_store.workspace import WorkspaceManager
from llmdbenchmark.result_store.commands import register_command

@register_command("rm")
def execute(args, logger):
    workspace_manager = WorkspaceManager()
    
    for input_path in args.paths:
        target_path = input_path
        
        path = Path(input_path)
        if not path.exists() or not path.is_dir():
            staged_runs = workspace_manager.list_staged()
            matches = []
            for staged_run in staged_runs:
                full_uid = staged_run.get("run_uid", "-")
                if full_uid != "-":
                    if '*' in input_path or '?' in input_path:
                        if fnmatch.fnmatch(full_uid, input_path):
                            matches.append(staged_run["path"])
                    elif full_uid == input_path or full_uid.startswith(input_path):
                        matches.append(staged_run["path"])
                    
            if len(matches) == 0:
                logger.log_error(f"Workspace '{input_path}' was not staged.")
                continue
            elif len(matches) > 1:
                logger.log_error(f"Ambiguous UID '{input_path}'. Found multiple matches in staged runs:")
                for match in matches:
                    logger.log_error(f"  {match}")
                continue
            else:
                target_path = matches[0]
                logger.log_debug(f"Resolved UID '{input_path}' to staged path {target_path}")

        if workspace_manager.remove_workspace(target_path):
            report_info = workspace_manager._parse_report(Path(target_path))
            uid = report_info.get("run_uid", "-")
            short_uid = uid[:8] if len(uid) > 8 else uid
            logger.log_plain(f"Unstaged '{short_uid}'")
        else:
            logger.log_error(f"Workspace '{target_path}' was not staged.")
            continue
