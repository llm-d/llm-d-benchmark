"""Command to stage untracked benchmark runs."""

import sys
import fnmatch
from pathlib import Path
from llmdbenchmark.result_store.store import StoreManager, StoreNotFound
from llmdbenchmark.result_store.workspace import WorkspaceManager
from llmdbenchmark.result_store.utils import color_pad
from llmdbenchmark.result_store.commands import register_command

@register_command("add")
def execute(args, logger):
    workspace_manager = WorkspaceManager()
    
    for input_path in args.paths:
        target_path = input_path
        
        path = Path(input_path)
        if not path.exists() or not path.is_dir():
            logger.log_debug(f"'{input_path}' is not a directory. Searching for matching Run UID...")
            
            try:
                store_root = StoreManager.find_store_root()
            except StoreNotFound as exception:
                logger.log_error(str(exception))
                continue
                
            workspaces_dir = store_root / "workspaces"
            matches = []
            
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
                            
                        report_info = workspace_manager._parse_report(exp_dir)
                        full_uid = report_info.get("run_uid", "-")
                        
                        if full_uid != "-":
                            if '*' in input_path or '?' in input_path:
                                if fnmatch.fnmatch(full_uid, input_path):
                                    matches.append(exp_dir.resolve())
                            elif full_uid == input_path or full_uid.startswith(input_path):
                                    matches.append(exp_dir.resolve())
            
            if len(matches) == 0:
                logger.log_error(f"Could not find matching Run UID or directory for '{input_path}'")
                continue
            elif len(matches) > 1:
                logger.log_error(f"Ambiguous UID '{input_path}'. Found multiple matches:")
                for match in matches:
                    logger.log_error(f"  {match}")
                continue
            else:
                target_path = str(matches[0])
                logger.log_debug(f"Resolved '{input_path}' to {target_path}")

        report_info = workspace_manager._parse_report(Path(target_path))
        overrides = {}
        
        missing_fields = []
        for field in ["scenario", "model", "hardware"]:
            value = report_info.get(field, "missing")
            if value == "missing" or "missing" in value or "#" in value:
                missing_fields.append(field)
                
        if missing_fields:
            if sys.stdout.isatty():
                uid = report_info.get("run_uid", "missing")
                short_uid = uid[:8] if len(uid) > 8 else uid
                logger.log_plain(f"\033[33mMissing metadata detected for {short_uid}. Please fill in required fields:\033[0m")
                field_error = False
                for field in missing_fields:
                    while True:
                        value = input(f"  {field.capitalize()}: ").strip().replace("\n", "").replace("\r", "")
                        if not value:
                            logger.log_plain(f"\033[31mError: {field} is required to add results.\033[0m")
                            field_error = True
                            break
                        
                        if field == "hardware":
                            original_value = report_info.get("hardware", "missing")
                            if "-x" in original_value and "-x" not in value:
                                count_part = original_value.split("-x", 1)[1]
                                value = f"{value}-x{count_part}"
                            
                            if "-x" not in value:
                                logger.log_plain(f"\033[31mError: Hardware count could not be inferred. Please specify count (e.g., l4-x1).\033[0m")
                                continue
                                
                        overrides[field] = value
                        break
                    
                    if field_error:
                        break
                if field_error:
                    continue
            else:
                logger.log_plain(f"\033[31mError: Missing metadata for fields: {', '.join(missing_fields)} in {input_path}. Skipping.\033[0m")
                continue

        if workspace_manager.add_workspace(target_path, overrides=overrides):
            report_info.update(overrides)
            uid = report_info.get("run_uid", "missing")
            short_uid = uid[:8] if len(uid) > 8 else uid
            logger.log_plain(f"Staged '{short_uid}'")
        else:
            logger.log_plain(f"Workspace '{target_path}' is already staged.")
