"""Commands package for result store.
Handles self-registration of commands to avoid central mapping.
"""

import os
import importlib
from pathlib import Path

COMMAND_MAP = {}

def register_command(name):
    """Decorator to register a command in the map."""
    def decorator(func):
        COMMAND_MAP[name] = func
        return func
    return decorator

# Auto-discover and import all modules in this directory to trigger registration
commands_dir = Path(__file__).parent
for file_path in commands_dir.iterdir():
    if file_path.is_file() and file_path.suffix == ".py" and file_path.name != "__init__.py":
        module_name = f"llmdbenchmark.result_store.commands.{file_path.stem}"
        importlib.import_module(module_name)
