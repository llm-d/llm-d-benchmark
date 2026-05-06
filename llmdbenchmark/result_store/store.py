"""Core store logic providing Git-native project anchoring."""

import json
from pathlib import Path

class StoreNotFound(Exception):
    """Raised when the .result_store directory cannot be found."""
    pass

class StoreManager:
    """Manages the creation and discovery of local .result_store environments."""
    
    STORE_DIR_NAME = ".result_store"
    
    @classmethod
    def find_store_root(cls, start_path: Path = None, silent: bool = False) -> Path:
        """Recursively walks up looking for .result_store/"""
        current = Path(start_path or Path.cwd()).resolve()
        while current != current.parent:
            if (current / cls.STORE_DIR_NAME).is_dir():
                return current
            current = current.parent
        if (current / cls.STORE_DIR_NAME).is_dir():
            return current
            
        if silent:
            return None
        raise StoreNotFound("Not a results store repository (or any of the parent directories): .result_store")

    @classmethod
    def init_store(cls, target_dir: Path = None) -> tuple[Path, bool]:
        """Initializes a new result store in the target directory."""
        target = Path(target_dir or Path.cwd()).resolve()
        store_dir = target / cls.STORE_DIR_NAME
        
        if store_dir.exists():
            return store_dir, False
            
        store_dir.mkdir(parents=True)
        (target / "workspaces").mkdir(exist_ok=True)
        from llmdbenchmark.result_store.config import ConfigManager
        config = ConfigManager(config_path=store_dir / "config.json")
        config.init_config()
            
        staged_data = {"staged": []}
        with open(store_dir / "staged.json", "w", encoding="utf-8") as f:
            json.dump(staged_data, f, indent=2)
            
        return store_dir, True
