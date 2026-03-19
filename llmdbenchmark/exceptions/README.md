# llmdbenchmark.exceptions

Custom exception hierarchy for the llmdbenchmark package. All exceptions carry structured context (step name, timestamp, arbitrary key-value context dict) and support serialization to dict.

## Exception Classes

| Class | Description |
|-------|-------------|
| `LLMDBenchmarkError` | Base exception. Carries `message`, `step`, `context` dict, and `timestamp`. Provides `to_dict()` serialization. |
| `TemplateError` | Raised on Jinja2 template rendering failures (missing variables, bad syntax). Adds `template_file` and `missing_vars` to context. |
| `ConfigurationError` | Raised on post-render configuration errors (bad YAML, missing keys, invalid values). Adds `config_file` and `invalid_key` to context. |
| `ExecutionError` | Raised on command or runtime execution failures. Adds `command`, `exit_code`, `stdout`, and `stderr` to context. |

## Files

| File | Description |
|------|-------------|
| `__init__.py` | Re-exports all exception classes for convenient `from llmdbenchmark.exceptions import ...` imports |
| `exceptions.py` | Exception class definitions |
