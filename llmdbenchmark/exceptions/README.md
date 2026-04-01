# llmdbenchmark.exceptions

Custom exception hierarchy for the llmdbenchmark package. All exceptions carry structured context (step name, timestamp, arbitrary key-value context dict) and support serialization to dict.

## Files

```
exceptions/
├── __init__.py       -- Re-exports all exception classes
└── exceptions.py     -- Exception class definitions
```

## Exception Hierarchy

```
LLMDBenchmarkError (base)
├── TemplateError
├── ConfigurationError
└── ExecutionError
```

### LLMDBenchmarkError

Base exception for all llmdbenchmark errors.

```python
class LLMDBenchmarkError(Exception):
    def __init__(self, message: str, step: str | None = None, context: dict | None = None):
        self.message = message
        self.step = step            # Pipeline step where the error occurred
        self.context = context      # Arbitrary key-value context
        self.timestamp = datetime.now()

    def to_dict(self) -> dict: ...  # Serialize to {"error_type", "message", "step", "timestamp", "context"}
```

String representation: `[step] message (Context: key=value, ...)`

### TemplateError

Raised on Jinja2 template rendering failures -- missing variables, bad syntax, file I/O errors.

```python
class TemplateError(LLMDBenchmarkError):
    def __init__(self, message, template_file=None, missing_vars=None, **kwargs): ...
```

Additional context fields: `template_file`, `missing_vars`.

Raised by: `RenderSpecification` (specification template rendering), `RenderPlans` (plan template rendering).

### ConfigurationError

Raised on post-render configuration errors -- bad YAML, missing keys, invalid values, filesystem path failures.

```python
class ConfigurationError(LLMDBenchmarkError):
    def __init__(self, message, config_file=None, invalid_key=None, **kwargs): ...
```

Additional context fields: `config_file`, `invalid_key`.

Raised by: `RenderSpecification` (YAML parse errors, missing paths), `LLMDBenchmarkLogger` (log directory failures), and various configuration validation points.

### ExecutionError

Raised on command or runtime execution failures.

```python
class ExecutionError(LLMDBenchmarkError):
    def __init__(self, message, command=None, exit_code=None, stdout=None, stderr=None, **kwargs): ...
```

Additional context fields: `command`, `exit_code`, `stdout`, `stderr`.

Raised by: `CommandExecutor.execute()` when `fatal=True` and the command fails.

## Usage

All exception classes are re-exported from `llmdbenchmark.exceptions` for convenient imports:

```python
from llmdbenchmark.exceptions import TemplateError, ConfigurationError, ExecutionError
```
