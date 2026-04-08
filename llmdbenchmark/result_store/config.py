"""Configuration management specific to the local result store."""
import json
from pathlib import Path
from llmdbenchmark.result_store.store import StoreManager, StoreNotFound

DEFAULT_REMOTES = {
    "prod": "gs://llm-d-benchmarks",
    "staging": "gs://llm-d-benchmarks-staging"
}

class ConfigManager:
    def __init__(self, config_path: Path = None):
        if config_path:
            self.config_path = Path(config_path)
        else:
            try:
                self.config_path = StoreManager.find_store_root() / StoreManager.STORE_DIR_NAME / "config.json"
            except StoreNotFound:
                self.config_path = None
            
    def _load(self) -> dict:
        if not self.config_path.exists():
            return {}
        with open(self.config_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save(self, data: dict):
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def init_config(self):
        if self.config_path and not self.config_path.exists():
            self._save({"remotes": DEFAULT_REMOTES.copy()})

    def get_remote(self, name: str) -> str:
        if self.config_path and self.config_path.exists():
            data = self._load()
            remotes = data.get("remotes", {})
            if name in remotes:
                return remotes[name]
                
        if name in DEFAULT_REMOTES:
            return DEFAULT_REMOTES[name]
        raise ValueError(f"Remote '{name}' not found.")

    def add_remote(self, name: str, uri: str):
        data = self._load()
        if "remotes" not in data:
            data["remotes"] = {}
        data["remotes"][name] = uri
        self._save(data)

    def remove_remote(self, name: str):
        data = self._load()
        remotes = data.get("remotes", {})
        if name in remotes:
            del remotes[name]
            self._save(data)
        else:
            raise ValueError(f"Remote '{name}' not found.")

    def list_remotes(self) -> dict:
        data = self._load()
        return data.get("remotes", {})
