"""In-process event fan-out: one bounded asyncio.Queue per SSE subscriber."""

import asyncio


class EventBus:
    def __init__(self, maxsize=400):
        self._maxsize = maxsize
        self._subscribers = {}

    def subscribe(self, job_id):
        queue = asyncio.Queue(maxsize=self._maxsize)
        self._subscribers.setdefault(job_id, set()).add(queue)
        return queue

    def unsubscribe(self, job_id, queue):
        subscribers = self._subscribers.get(job_id)
        if not subscribers:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(job_id, None)

    def publish(self, job_id, event):
        for queue in list(self._subscribers.get(job_id, ())):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def subscriber_count(self, job_id):
        return len(self._subscribers.get(job_id, ()))
