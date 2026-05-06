"""Workspace queueing and local indexing for the result store."""

import json
from pathlib import Path
import yaml
from llmdbenchmark.result_store.store import StoreManager

class WorkspaceManager:
    """Manages tracking benchmark directories within the local store."""

    def __init__(self, staged_path: Path = None):
        if staged_path:
            self.staged_path = Path(staged_path)
        else:
            self.staged_path = StoreManager.find_store_root() / StoreManager.STORE_DIR_NAME / "staged.json"

    def _load(self) -> dict:
        if not self.staged_path.exists():
            return {"staged": []}
        with open(self.staged_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save(self, data: dict):
        with open(self.staged_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            
    def _parse_report(self, path: Path) -> dict:
        """Parse core ID values dynamically from files or directory name."""
        info = {
            "scenario": "missing",
            "model": "missing",
            "hardware": "missing",
            "run_uid": "missing"
        }

        dir_name = path.name
        if "_" in dir_name:
             info["run_uid"] = dir_name.rsplit("_", 1)[0]
        else:
             info["run_uid"] = dir_name

        # 1. Try run_metadata.yaml
        metadata_path = path / "run_metadata.yaml"
        if metadata_path.exists():
            try:
                with open(metadata_path, "r", encoding="utf-8") as f:
                    m = yaml.safe_load(f) or {}
                    info["model"] = m.get("model", info["model"])
                    info["scenario"] = m.get("harness_workload", info["scenario"])
            except Exception:
                pass

        ws_dir = path.parent.parent
        plan_dir = ws_dir / "plan"
        if plan_dir.exists() and plan_dir.is_dir():
            subdirs = [d for d in plan_dir.iterdir() if d.is_dir()]
            if subdirs:
                info["scenario"] = subdirs[0].name

        # 2. Try benchmark_report_v0.2*.yaml for run_uid
        for f in list(path.glob("benchmark_report_v0.2*.yaml")) + list(path.glob("report_v0.2*.yaml")):
            try:
                with open(f, "r", encoding="utf-8") as f_in:
                    r = yaml.safe_load(f_in) or {}
                    if "run" in r and "uid" in r["run"]:
                         info["run_uid"] = str(r["run"]["uid"])
                    
                    if "scenario" in r and "stack" in r["scenario"]:
                         stack = r["scenario"]["stack"]
                         if stack and isinstance(stack, list):
                             if info["model"] == "missing":
                                 info["model"] = stack[0].get("standardized", {}).get("model", {}).get("name", "missing")

                             acc = stack[0].get("standardized", {}).get("accelerator", {})
                             acc_model = acc.get("model", "")
                             acc_count = acc.get("count", "")
                             if acc_model or acc_count:
                                 info["hardware"] = f"{acc_model or 'missing'}-x{acc_count or '#'}"
                break
            except Exception:
                pass
                
        if info["run_uid"] == "-" and info["scenario"] == "-":
            return {}
            
        return info

    def add_workspace(self, path: str, overrides: dict = None) -> bool:
        """Add to staged queue. Returns bool indicating if added or updated."""
        data = self._load()
        abs_path = str(Path(path).resolve())
        changed = False
        
        staged_list = data.get("staged", [])
        if abs_path not in staged_list:
            staged_list.append(abs_path)
            data["staged"] = staged_list
            changed = True
            
        if overrides:
            cleaned_overrides = {k: str(v).replace("\\n", "").replace("\\r", "").strip() for k, v in overrides.items()}
            overrides_map = data.setdefault("overrides", {})
            if overrides_map.get(abs_path) != cleaned_overrides:
                overrides_map[abs_path] = cleaned_overrides
                changed = True
            
        if changed:
            self._save(data)
            
        return changed

    def remove_workspace(self, path: str) -> bool:
        """Remove from staged queue."""
        data = self._load()
        abs_path = str(Path(path).resolve())
        staged_list = data.get("staged", [])
        
        changed = False
        if abs_path in staged_list:
            staged_list.remove(abs_path)
            changed = True

        overrides_map = data.get("overrides", {})
        if abs_path in overrides_map:
            del overrides_map[abs_path]
            changed = True
            
        if changed:
            self._save(data)
            
        return changed

    def list_staged(self) -> list:
        """Returns metadata detailing contents and statuses for queued workspaces."""
        data = self._load()
        runs = []
        overrides_map = data.get("overrides", {})
        
        for path_str in data.get("staged", []):
            path = Path(path_str)
            base_info = {
                "path": str(path),
                "run_uid": "missing",
                "scenario": "missing",
                "model": "missing",
                "hardware": "missing", 
                "status": "missing report"
            }
            if path.exists():
                report_info = self._parse_report(path)
                if report_info:
                    base_info.update(report_info)
                    base_info["status"] = "staged"
            else:
                 base_info["status"] = "deleted"

            if path_str in overrides_map:
                base_info.update(overrides_map[path_str])
                 
            runs.append(base_info)
        return runs
