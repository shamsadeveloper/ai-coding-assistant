"""Runs fix sessions as background tasks in the server's event loop, with a hard concurrency limit
and a stop switch per session.

Stopping sets a thread-safe flag (checked before every LLM call and twice a second while tests run,
which kills the test process) and cancels the task (which interrupts a waiting LLM call).
"""

import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable

from app.services.fixing.errors import FixSessionBusyError

logger = logging.getLogger(__name__)

Job = Callable[[threading.Event], Awaitable[object]]


class FixSessionRunner:
    def __init__(self) -> None:
        self._jobs: dict[int, tuple[asyncio.Task, threading.Event]] = {}

    def running(self) -> list[int]:
        return [session_id for session_id, (task, _) in self._jobs.items() if not task.done()]

    def ensure_capacity(self, max_concurrent: int) -> None:
        if len(self.running()) >= max_concurrent:
            raise FixSessionBusyError(
                f"{max_concurrent} fix session(s) already running; wait for it to finish or stop it first"
            )

    def start(self, session_id: int, job: Job, max_concurrent: int) -> None:
        self.ensure_capacity(max_concurrent)
        stop = threading.Event()
        task = asyncio.get_running_loop().create_task(self._guard(session_id, job, stop), name=f"fix-session-{session_id}")
        self._jobs[session_id] = (task, stop)
        task.add_done_callback(lambda _t: self._jobs.pop(session_id, None))

    def stop(self, session_id: int) -> bool:
        """Request a stop. Returns False when no task is running for this session."""
        job = self._jobs.get(session_id)
        if job is None or job[0].done():
            return False
        task, stop = job
        stop.set()
        task.cancel()
        return True

    async def wait(self, session_id: int) -> None:
        job = self._jobs.get(session_id)
        if job is not None:
            await asyncio.gather(job[0], return_exceptions=True)

    async def shutdown(self) -> None:
        tasks = [task for task, _ in self._jobs.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    async def _guard(session_id: int, job: Job, stop: threading.Event) -> None:
        try:
            await job(stop)
        except asyncio.CancelledError:
            pass  # the session records its own cancelled/interrupted state
        except Exception:
            logger.exception("Fix session %s task crashed", session_id)
