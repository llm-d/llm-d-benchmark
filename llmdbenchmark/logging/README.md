# Logging Module

Structured logger with stream separation and per-instance file logging. All phases and steps use the same `LLMDBenchmarkLogger` for consistent output formatting.

## Module Structure

```text
logging/
    __init__.py
    logger.py       Logger implementation, formatter, factory function
```

## Architecture

The logger separates output into two streams and four destinations:

| Destination | Content | Level |
|-------------|---------|-------|
| Console stdout | Info and debug messages | DEBUG (verbose) or INFO (default) |
| Console stderr | Warnings and errors | WARNING+ |
| Per-instance `-stdout.log` | Info and debug messages | DEBUG |
| Per-instance `-stderr.log` | Warnings and errors | WARNING+ |
| Combined `llmdbenchmark-stdout.log` | All instances' info/debug | DEBUG |
| Combined `llmdbenchmark-stderr.log` | All instances' warnings/errors | WARNING+ |

### Stream Separation

Console output routes INFO and below to `sys.stdout` and WARNING and above to `sys.stderr`. This means normal progress output can be piped independently from error output:

```bash
# Capture only errors
llmdbenchmark --spec spec.yaml.j2 standup 2> errors.log

# Capture everything
llmdbenchmark --spec spec.yaml.j2 standup > output.log 2>&1
```

### File Logging

Each logger instance creates two per-instance log files:

```text
logs/
    {user}-{timestamp}-{uuid4}-stdout.log    # This instance's info/debug
    {user}-{timestamp}-{uuid4}-stderr.log    # This instance's warnings/errors
    llmdbenchmark-stdout.log                 # Combined info/debug from all instances
    llmdbenchmark-stderr.log                 # Combined warnings/errors from all instances
```

Per-instance files always include full tracebacks regardless of verbose mode. Combined files aggregate output from all logger instances within the same run, useful for reviewing interleaved step output.

## Components

### EmojiFormatter

Custom `logging.Formatter` that prepends emoji indicators to log messages:

| Level | Default Emoji |
|-------|---------------|
| ERROR | :x: |
| WARNING | :warning: |
| INFO | :information_source: |
| DEBUG | :mag: |

Format: `{timestamp} - {level} - {emoji} {message}`

Timestamps use millisecond precision: `YYYY-MM-DD HH:MM:SS,mmm`

Steps can override the default emoji per-message via the `emoji` parameter on log methods.

### LLMDBenchmarkLogger

The main logger class. Each instance wraps a standard Python `logging.Logger` with a unique name (`{package}-{name}-{uuid4[:4]}`) to prevent handler conflicts.

**Log methods:**

| Method | Level | Notes |
|--------|-------|-------|
| `log_debug(msg, emoji=None)` | DEBUG | Only visible in console with `--verbose` |
| `log_info(msg, emoji=None)` | INFO | Always visible in console |
| `log_warning(msg, emoji=None)` | WARNING | Routes to stderr |
| `log_error(msg, emoji=None, exc_info=False)` | ERROR | Routes to stderr; `exc_info=True` appends traceback |
| `line_break()` | -- | Writes a blank line to all handlers (no timestamp) |

### Verbose Mode

| Behavior | `--verbose` off | `--verbose` on |
|----------|-----------------|----------------|
| Console minimum level | INFO | DEBUG |
| Console tracebacks | Hidden | Shown |
| File tracebacks | Always shown | Always shown |
| File minimum level | DEBUG | DEBUG |

## Usage

### Factory Function

```python
from llmdbenchmark.logging.logger import get_logger

logger = get_logger(
    log_dir=Path("/path/to/logs"),
    verbose=True,       # Optional, default False
    log_name="my_step", # Optional, auto-generated if omitted
)
```

If `log_name` is omitted, it auto-generates as `{username}-{YYYYMMDD-HHMMSS-mmm}`.

### In Steps

Steps receive the logger through `ExecutionContext`:

```python
def execute(self, context, stack_path=None):
    logger = context.logger
    logger.log_info("Deploying resources...", emoji="🚀")
    logger.log_debug(f"Using namespace: {namespace}")
```

### Custom Emoji

```python
logger.log_info("Model access verified", emoji="✅")
logger.log_info("Checking HuggingFace access...", emoji="🔑")
logger.log_warning("Retrying connection...", emoji="🔄")
```
