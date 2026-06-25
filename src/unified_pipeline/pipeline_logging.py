"""Logging bootstrap for standalone pipeline CLI runs.

When a pipeline module is imported by the web app, logging is already
configured (app.logging_config) and this is a no-op. When a module is run
standalone (python -m / direct invocation), no handler exists on the root
logger, so logger.* output would be swallowed -- call ensure_logging() at the
top of a __main__ block to install a basic stdout handler.
"""
import logging
import os


def ensure_logging() -> None:
    """Idempotently configure root logging for standalone runs.

    No-op if the root logger already has handlers (i.e. the app configured it).
    """
    root = logging.getLogger()
    if root.handlers:
        return
    level = os.environ.get("CVICHE_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
