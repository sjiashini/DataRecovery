"""Job state machine: thread-safe in-memory state, SQLite snapshots, SSE fan-out."""

import asyncio
import threading
import time
import uuid
from collections import deque

from vs_app import settings

LOGGER = settings.LOGGER

ACTIVE_STATES = ('queued', 'running')
TERMINAL_STATES = ('completed', 'partial', 'failed', 'cancelled')
STATE_EVENT_INTERVAL = 0.5
SAVE_INTERVAL = 1.0


def wall_clock():
    return time.strftime('%Y-%m-%dT%H:%M:%S')


class Job:
    """One unit of work. Every mutator is safe to call from worker threads."""

    def __init__(self, manager, kind, fields=None):
        fields = dict(fields or {})
        self.manager = manager
        self.id = uuid.uuid4().hex
        self.kind = kind
        self.status = 'queued'
        self.error = None
        self.created = time.time()
        self.started = self.created
        self.finished = None
        self.cancel_requested = False
        self.stages = []
        self.progress = fields.pop('progress', {})
        self.commands = fields.pop('commands', {})
        self.fields = fields
        self.logs = deque(maxlen=400)
        self._current_stage = None
        self._proc = None
        self._pidfile = None
        self._seq = 0
        self._last_state = 0.0
        self._last_saved = 0.0
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ state
    def add_stage(self, name, label, policy='report'):
        with self._lock:
            self.stages.append({'name': name, 'label': label, 'policy': policy,
                                'state': 'pending', 'detail': '', 'seconds': None})

    def start(self):
        with self._lock:
            if self.status == 'queued':
                self.status = 'running'
        LOGGER.info('%s job %s started', self.kind, self.id[:8])
        self.persist(force=True)
        self._emit('status', {'status': self.status}, persist=True)
        self._publish_state(force=True)

    def start_stage(self, name):
        with self._lock:
            self._current_stage = name
            self.status = 'running'
            for stage in self.stages:
                if stage['name'] == name:
                    stage['state'] = 'running'
        self._emit('stage', {'name': name, 'state': 'running'}, persist=True)
        self._publish_state(force=True)
        self.persist(force=True)

    def finish_stage(self, name, state, detail=None, seconds=None):
        with self._lock:
            for stage in self.stages:
                if stage['name'] == name:
                    stage['state'] = state
                    if detail is not None:
                        stage['detail'] = detail
                    if seconds is not None:
                        stage['seconds'] = round(seconds, 1)
            progress = self.progress.get(name)
            if progress is not None and progress.get('state') != 'off':
                progress['state'] = state
                if seconds is not None:
                    progress['seconds'] = round(seconds, 1)
        self._emit('stage', {'name': name, 'state': state, 'detail': detail},
                   persist=True)
        self._publish_state(force=True)
        self.persist(force=True)

    def finish(self, status, error=None):
        if status not in TERMINAL_STATES:
            status = 'failed'
        with self._lock:
            if self.status in TERMINAL_STATES:
                return
            self.status = status
            self.error = error
            self.finished = time.time()
            self._proc = None
            self._pidfile = None
            for stage in self.stages:
                if stage['state'] == 'running':
                    stage['state'] = status
                elif stage['state'] == 'pending':
                    stage['state'] = 'skipped'
            for progress in self.progress.values():
                if progress.get('state') in ('running', 'pending'):
                    progress['state'] = status
        LOGGER.info('%s job %s %s%s', self.kind, self.id[:8], status,
                    f': {error}' if error else '')
        self._emit('status', {'status': status, 'error': error}, persist=True)
        self._publish_state(force=True)
        self._emit('end', {'status': status, 'error': error}, persist=True)
        self.persist(force=True)

    def set_fields(self, **values):
        with self._lock:
            self.fields.update(values)
        self._publish_state()

    def update_progress(self, stage, **values):
        with self._lock:
            progress = self.progress.setdefault(stage, {})
            progress.update(values)
        self._publish_state()

    # ------------------------------------------------------------------- logs
    def log(self, message):
        text = str(message)
        with self._lock:
            self.logs.append(text)
        self._emit('log', {'message': text}, persist=True)
        self.persist()

    def storage_problem(self, reason):
        """Tell the user, once, that this job's history can't be saved.

        Published to live viewers only, never persisted: persisting it would fail the
        same way and loop.
        """
        text = (f'WARNING: job history could not be saved ({reason}). The job keeps '
                'running, but its log may be incomplete after a restart. Check free '
                'space on the drive that holds the .runtime folder.')
        with self._lock:
            if self.fields.get('storage_warning'):
                return
            self.fields['storage_warning'] = text
            self.logs.append(text)
        self._emit('log', {'message': text}, numbered=False)
        self._publish_state(force=True)

    # --------------------------------------------------------------- processes
    def attach_process(self, proc, pidfile=None):
        with self._lock:
            self._proc, self._pidfile = proc, pidfile

    def detach_process(self):
        with self._lock:
            self._proc, self._pidfile = None, None

    def process_state(self):
        with self._lock:
            return self._proc, self._pidfile

    def request_cancel(self):
        with self._lock:
            self.cancel_requested = True
        self._emit('status', {'status': self.status, 'cancel_requested': True},
                   persist=True)
        self.persist(force=True)

    @property
    def cancelled(self):
        with self._lock:
            return self.cancel_requested

    @property
    def active(self):
        with self._lock:
            return self.status in ACTIVE_STATES

    # -------------------------------------------------------------- snapshots
    def _legacy_stage(self):
        with self._lock:
            if self.status in TERMINAL_STATES:
                return self.status
            return self._current_stage or 'starting'

    def public(self, include_log=True, log_limit=120):
        with self._lock:
            snapshot = {
                'id': self.id,
                'kind': self.kind,
                'active': self.status in ACTIVE_STATES,
                'done': self.status in TERMINAL_STATES,
                'status': self.status,
                'stage': self._legacy_stage(),
                'outcome': self.status if self.status in TERMINAL_STATES else None,
                'error': self.error,
                'started': round(self.started, 3),
                'finished': round(self.finished, 3) if self.finished else None,
                'elapsed': round((self.finished or time.time()) - self.started, 1),
                'cancel_requested': self.cancel_requested,
                'progress': {name: dict(values) for name, values in self.progress.items()},
                'commands': dict(self.commands),
                'stages': [dict(stage) for stage in self.stages],
                'stage_order': [stage['name'] for stage in self.stages],
            }
            snapshot.update(self.fields)
            if include_log:
                snapshot['log'] = list(self.logs)[-log_limit:]
        return snapshot

    def record(self):
        snapshot = self.public()
        return {
            'id': self.id,
            'kind': self.kind,
            'status': self.status,
            'stage': snapshot['stage'],
            'created': self.created,
            'started': self.started,
            'finished': self.finished,
            'error': self.error,
            'snapshot': snapshot,
        }

    # ------------------------------------------------------------- event output
    def _emit(self, etype, data, persist=False, numbered=True):
        """State snapshots carry no id: only persisted events consume SSE sequence ids."""
        with self._lock:
            if numbered:
                self._seq += 1
                event = {'id': self._seq, 'type': etype, 'ts': wall_clock(), 'data': data}
            else:
                event = {'type': etype, 'ts': wall_clock(), 'data': data}
        self.manager.publish(self.id, event)
        if persist:
            self.manager.enqueue_event(self.id, event)

    def _publish_state(self, force=False):
        now = time.time()
        with self._lock:
            if not force and now - self._last_state < STATE_EVENT_INTERVAL:
                return
            self._last_state = now
        self._emit('state', self.public(include_log=False), numbered=False)

    def persist(self, force=False):
        now = time.time()
        with self._lock:
            if not force and now - self._last_saved < SAVE_INTERVAL:
                return
            self._last_saved = now
        self.manager.enqueue_save(self.record())


class JobManager:
    """Creates jobs, coalesces persistence writes, and fans events out to SSE."""

    def __init__(self, store, bus):
        self.store = store
        self.bus = bus
        self._jobs = {}
        self._by_id = {}
        self._lock = threading.RLock()
        self._loop = None
        self._queue = None
        self._pump_task = None

    async def start(self):
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        self._pump_task = asyncio.create_task(self._pump())

    async def stop(self):
        if self._pump_task is not None:
            self._pump_task.cancel()
            try:
                await self._pump_task
            except (asyncio.CancelledError, Exception):
                pass
            self._pump_task = None
        await self._drain()

    async def _drain(self):
        if self._queue is None:
            return
        pending = []
        while True:
            try:
                pending.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        await self._write_batch(pending)

    async def _pump(self):
        while True:
            batch = [await self._queue.get()]
            while True:
                try:
                    batch.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            await self._write_batch(batch)

    async def _write_batch(self, batch):
        """Write queued events, then the latest snapshot per job; report failures to the job."""
        saves = {}
        for item in batch:
            if item[0] != 'event':
                saves[item[1]] = item[2]
                continue
            try:
                await self.store.append_event(item[1], item[2])
            except Exception as exc:
                LOGGER.exception('Job event write failed (job %s)', item[1][:8])
                self._storage_failed(item[1], exc)
        for job_id, record in saves.items():
            try:
                await self.store.save_job(record)
            except Exception as exc:
                LOGGER.exception('Job snapshot write failed (job %s)', job_id[:8])
                self._storage_failed(job_id, exc)

    def _storage_failed(self, job_id, exc):
        job = self.get(job_id)
        if job is not None:
            job.storage_problem(str(exc) or exc.__class__.__name__)

    def _submit(self, item):
        if self._loop is None or self._queue is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, item)
        except RuntimeError:
            pass

    def publish(self, job_id, event):
        self.bus.publish(job_id, event)

    def enqueue_event(self, job_id, event):
        self._submit(('event', job_id, event))

    def enqueue_save(self, record):
        self._submit(('save', record['id'], record))

    # ---------------------------------------------------------------- registry
    def create(self, kind, fields=None):
        with self._lock:
            current = self._jobs.get(kind)
            if current is not None and current.active:
                return None
            job = Job(self, kind, fields)
            self._jobs[kind] = job
            self._by_id[job.id] = job
            return job

    def get(self, job_id):
        with self._lock:
            return self._by_id.get(job_id)

    def current(self, kind):
        with self._lock:
            return self._jobs.get(kind)

    def snapshot(self, kind):
        job = self.current(kind)
        return job.public() if job else None
