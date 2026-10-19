"""Load the embedding model and search index in the background at server start.

The first schema search in a process loads the embedding model, which can take 20 to
25 seconds and would otherwise count against the first visitor's 60-second budget.
"""

import logging
import threading

log = logging.getLogger(__name__)

_lock = threading.Lock()
_started = False


def _run() -> None:
    try:
        from census_compass.tools.schema import search_schema

        search_schema("population", 2020)
        log.info("Search index warm-up finished")
    except Exception as e:  # warm-up is best effort; the first real search just loads lazily
        log.warning("Search index warm-up failed: %s", type(e).__name__)


def start_warm_up() -> threading.Thread | None:
    """Start the warm-up once per process. Returns the thread, or None if already started."""
    global _started
    with _lock:
        if _started:
            return None
        _started = True
    thread = threading.Thread(target=_run, name="census-warm-up", daemon=True)
    thread.start()
    return thread
