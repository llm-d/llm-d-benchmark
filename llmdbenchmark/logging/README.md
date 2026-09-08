# llmdbenchmark.logging

Logging utilities with emoji formatting, stream separation, per-instance file output, and shared combined log files.

## Files

```
logging/
├── __init__.py    -- Empty package marker
├── logger.py      -- LLMDBenchmarkLogger class and get_logger() factory
└── quiet.py       -- QuietLogger proxy that demotes INFO to DEBUG
```

## LLMDBenchmarkLogger

Each logger instance sets up six handlers:

| Handler | Level | Destination |
|---------|-------|-------------|
| Console stdout | INFO (DEBUG if verbose) | `sys.stdout`, filtered to INFO and below |
| Console stderr | WARNING | `sys.stderr`, WARNING and above |
| Per-instance stdout file | DEBUG | `{log_name}-{uuid}-stdout.log` (DEBUG and INFO only) |
| Per-instance stderr file | WARNING | `{log_name}-{uuid}-stderr.log` (WARNING and above) |
| Shared combined stdout | DEBUG | `llmdbenchmark-stdout.log` (all instances aggregate) |
| Shared combined stderr | WARNING | `llmdbenchmark-stderr.log` (all instances aggregate) |

The shared combined handlers are class-level singletons -- they are created once per log directory and reused across all logger instances in the same process.

### EmojiFormatter

Custom formatter that prepends level-specific emoji icons and uses millisecond-precision timestamps.

| Level | Emoji |
|-------|-------|
| ERROR | [x mark] |
| WARNING | [warning sign] |
| INFO | (none) |
| DEBUG | [magnifying glass] |

Timestamp format: `YYYY-MM-DD HH:MM:SS,mmm`

Output format: `{timestamp} - {LEVEL}   - {emoji} {message}`

### Methods

```python
class LLMDBenchmarkLogger:
    def log_debug(self, msg, emoji=None): ...
    def log_info(self, msg, emoji=None): ...
    def log_warning(self, msg, emoji=None): ...
    def log_error(self, msg, emoji=None, exc_info=False): ...
    def set_indent(self, level: int): ...
    def line_break(self): ...
```

- `set_indent(level)` -- Sets the indentation level for subsequent messages. Each indent level prepends `"    | "` to the message. Used by the step executor to visually nest step output under phase headers.
- `line_break()` -- Inserts a completely blank line across all handlers (no timestamp or level prefix). Writes directly to each handler's stream.
- `log_error(..., exc_info=False)` -- When `exc_info=True`, the formatted exception traceback is appended.

### Indentation

The indent system uses a visual tree prefix:

```
2025-01-15 10:30:00,123 - INFO    - >> [03] Workload monitoring
2025-01-15 10:30:01,456 - INFO    -     | PodMonitor created
2025-01-15 10:30:02,789 - INFO    -     | Metrics scraping enabled
2025-01-15 10:30:03,012 - INFO    - [checkmark] [03] Completed: workload_monitoring
```

## get_logger()

```python
def get_logger(
    log_dir: str | Path,
    verbose: bool = False,
    log_name: str | None = None,
) -> LLMDBenchmarkLogger:
```

Factory function that creates a configured logger. If `log_name` is not provided, generates one from `{username}-{YYYYMMDD-HHMMSS-mmm}`.

Raises `ConfigurationError` if `log_dir` is `None` or if file handler creation fails.

## QuietLogger

`quiet.py` provides a proxy that wraps an `LLMDBenchmarkLogger` and demotes its
INFO output to DEBUG. The console handler then drops those lines (unless
`--verbose` puts it at DEBUG), while every file handler still records them --
so the detail survives in `<workspace>/logs/` for a post-mortem.

```python
from llmdbenchmark.logging.quiet import plan_logger

render_logger = plan_logger(logger, config.quiet_plan)
```

| Method | Behavior |
|--------|----------|
| `log_info` | Demoted to `log_debug` (emoji preserved) |
| `log_plain_console` | Demoted to `log_debug` |
| `log_plain` | Demoted to `log_debug` -- it writes straight to every handler stream, so it would otherwise bypass level filtering |
| `line_break` | No-op -- blank separators around removed sections are just gaps |
| `log_warning` / `log_error` / `log_debug` / anything else | Delegated unchanged via `__getattr__` |

Warnings and errors are never suppressed: a display preference must not hide a
render failure.

Its one caller today is the plan-rendering pipeline, which narrates ~40 lines
per stack. That is the whole point of `llmdbenchmark plan`, but on `standup` /
`smoketest` / `teardown` / `run` / `experiment` the render is an implicit
prelude, so `cli._resolve_quiet_plan` turns quieting on there by default. See
`--quiet-plan` in the root README.
