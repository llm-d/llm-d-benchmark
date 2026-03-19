# llmdbenchmark.logging

Logging utilities with emoji formatting, stream separation, per-instance file output, and shared combined log files.

## Files

| File | Description |
|------|-------------|
| `__init__.py` | Empty package marker |
| `logger.py` | `LLMDBenchmarkLogger` class and `get_logger()` factory function |

## Architecture

`LLMDBenchmarkLogger` sets up multiple handlers per logger instance:

- **Console stdout** -- INFO and below to stdout (DEBUG if verbose)
- **Console stderr** -- WARNING and above to stderr
- **Per-instance stdout file** -- `{log_name}-{uuid}-stdout.log` (DEBUG and INFO)
- **Per-instance stderr file** -- `{log_name}-{uuid}-stderr.log` (WARNING and above)
- **Shared combined stdout** -- `llmdbenchmark-stdout.log` (all instances aggregate)
- **Shared combined stderr** -- `llmdbenchmark-stderr.log` (all instances aggregate)

The `EmojiFormatter` prepends level-specific emoji icons and uses millisecond timestamp formatting.

## Key Features

- **Indentation support** -- `set_indent(level)` prepends a visual tree prefix to messages, used by the step executor to visually nest step output under phase headers.
- **`line_break()`** -- Inserts a blank line across all handlers (no timestamp or level prefix).
- **Auto-generated log names** -- `get_logger()` generates names from the current username and timestamp if no explicit name is provided.

## Usage

```python
from llmdbenchmark.logging.logger import get_logger

logger = get_logger(log_dir="/path/to/logs", verbose=True)
logger.log_info("Starting phase")
logger.set_indent(1)
logger.log_info("Step detail")
logger.set_indent(0)
```
