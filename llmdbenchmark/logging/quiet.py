"""Console-quieting proxy around :class:`LLMDBenchmarkLogger`.

The plan-rendering pipeline (``RenderSpecification`` -> ``VersionResolver`` /
``ClusterResourceResolver`` -> ``RenderPlans`` -> Helm pre-render) narrates
itself at INFO: one line per rendered template, per image override, per stack.
That is the whole point of ``llmdbenchmark plan``, but on ``standup`` /
``smoketest`` / ``teardown`` / ``run`` / ``experiment`` the render is an
implicit prelude, and forty-odd lines of it push the output the user actually
cares about off the screen.

Wrapping the logger in :class:`QuietLogger` demotes those INFO lines to DEBUG.
The console handler drops them (unless ``--verbose``), while every file
handler keeps them at DEBUG -- so the full render narration is still in
``<workspace>/logs/`` for a post-mortem. Warnings and errors are delegated
untouched: a render problem must never be silenced.
"""


class QuietLogger:
    """Proxy that demotes INFO output to DEBUG, passing everything else through.

    Only the "chatty" methods are overridden. Any other attribute --
    ``log_warning``, ``log_error``, ``log_debug``, ``set_indent``, ``logger``
    -- resolves on the wrapped instance via ``__getattr__``, so this is a
    drop-in substitute for ``LLMDBenchmarkLogger`` anywhere a ``logger=``
    argument is accepted.
    """

    def __init__(self, logger):
        self._logger = logger

    @property
    def wrapped(self):
        """The underlying logger, for callers that need the un-quieted one."""
        return self._logger

    def log_info(self, msg, emoji=None):
        """Demote to DEBUG: file handlers keep it, console drops it."""
        self._logger.log_debug(msg, emoji=emoji)

    def log_plain_console(self, msg, emoji=None):
        """Demote to DEBUG -- the console half of this call is the noise."""
        self._logger.log_debug(msg, emoji=emoji)

    def log_plain(self, msg):
        """Demote to DEBUG. ``log_plain`` writes straight to every stream,
        which would bypass the level filtering this proxy exists to apply."""
        self._logger.log_debug(str(msg))

    def line_break(self):
        """No-op. Blank separators around suppressed sections are just gaps."""

    def __getattr__(self, name):
        return getattr(self._logger, name)


def plan_logger(logger, quiet: bool):
    """Return *logger*, wrapped in :class:`QuietLogger` when *quiet* is set."""
    return QuietLogger(logger) if quiet else logger
