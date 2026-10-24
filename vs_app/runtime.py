"""Long-lived singletons shared by routers and the app lifespan."""

import asyncio

from vs_app import settings
from vs_app.orchestration.jobs import JobManager
from vs_app.storage.bus import EventBus
from vs_app.storage.store import JobStore

STORE = JobStore(settings.jobs_db_path())
BUS = EventBus()
JOBS = JobManager(STORE, BUS)

# Anomaly: background tasks are owned by the loop, but keeping a reference here
# makes shutdown and tests explicit about what is still running.
TASKS = set()


def launch(coro):
    """Run a job's coroutine in the background, tracked in TASKS until it ends."""
    task = asyncio.create_task(coro)
    TASKS.add(task)

    def done(finished):
        TASKS.discard(finished)
        if finished.cancelled():
            return
        error = finished.exception()
        if error is not None:
            settings.LOGGER.error('Background job crashed: %s', error)

    task.add_done_callback(done)
    return task
