"""Shared utilities: logging, timing, and response helpers."""

import json
import logging
import time
from contextlib import contextmanager


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("pr_review")


@contextmanager
def timed_operation():
    """Context manager that tracks operation timing.

    Yields a callable that returns elapsed milliseconds since context entry.
    Use for external API calls and storage operations only.

    Example:
        with timed_operation() as elapsed:
            response = requests.get(url)
            logger.info(f"Request completed in {elapsed():.0f}ms")
    """
    start_time = time.time()
    yield lambda: (time.time() - start_time) * 1000


def make_response(data: dict, status: int = 200) -> tuple:
    """Create a JSON response tuple."""
    return (json.dumps(data), status, {"Content-Type": "application/json"})
