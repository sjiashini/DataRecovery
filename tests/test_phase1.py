"""Phase 1: free-space check, legacy routes gone, storage failures, migrations, job ids in logs."""

import asyncio
import logging
import os
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

from vs_app import main
from vs_app.config import JOB_ID, JobIdFilter
from vs_app.core import recovery as core
from vs_app.execution import bridge
from vs_app.orchestration.flows import media_flow
from vs_app.orchestration.gate import ENCODER
from vs_app.orchestration.jobs import Job, JobManager
from vs_app.storage import store as store_module
from vs_app.storage.store import JobStore, NewerSchemaError

GB = 1024 ** 3


class QuietManager:
    def publish(self, job_id, event):
        pass

    def enqueue_event(self, job_id, event):
        pass

    def enqueue_save(self, record):
        pass


class FreeSpaceTests(unittest.TestCase):
    def test_enough_space_passes(self):
        self.assertIsNone(core.space_problem(16 * GB, 0, 40 * GB))

    def test_too_little_space_is_refused_with_both_numbers(self):
        problem = core.space_problem(64 * GB, 0, 20 * GB)
        self.assertIn('Not enough free space', problem)
        self.assertIn('64.5 GB', problem)  # 64 GB plus the 0.5 GB margin
        self.assertIn('20.0 GB free', problem)

    def test_a_resumed_image_only_needs_the_rest(self):
        self.assertIsNone(core.space_problem(64 * GB, 60 * GB, 5 * GB))

    def test_unknown_size_never_blocks(self):
        self.assertIsNone(core.space_problem(None, 0, 1))

    def test_bridge_measures_the_drive_of_a_folder_not_created_yet(self):
        with tempfile.TemporaryDirectory() as temp:
            image = os.path.join(temp, 'not', 'yet', 'made', 'disk.img')
            huge = {'size': 10 ** 18}
            with mock.patch.object(bridge, 'device_identity', return_value=huge):
                self.assertIn('Not enough free space', bridge.image_space_problem(image, '/dev/sdb'))
            with mock.patch.object(bridge, 'device_identity', return_value={'size': 1024}):
                self.assertIsNone(bridge.image_space_problem(image, '/dev/sdb'))


class LegacyRouteTests(unittest.TestCase):
    def test_single_tool_routes_are_gone(self):
        paths = {getattr(route, 'path', '') for route in main.app.routes}
        self.assertNotIn('/api/ddrescue', paths)
        self.assertNotIn('/api/photorec', paths)
        self.assertIn('/api/recover_all', paths)


class FailingStore:
    def __init__(self):
        self.calls = 0

    async def append_event(self, job_id, event):
        self.calls += 1
        raise OSError('database or disk is full')

    async def save_job(self, record):
        self.calls += 1
        raise OSError('database or disk is full')


class StorageFailureTests(unittest.TestCase):
    def test_failure_is_shown_once_in_the_job_and_never_loops(self):
        store = FailingStore()
        manager = JobManager(store, bus=mock.Mock())
        job = manager.create('recovery')
        batch = [('event', job.id, {'id': 1, 'type': 'log', 'ts': '', 'data': {}}),
                 ('event', job.id, {'id': 2, 'type': 'log', 'ts': '', 'data': {}}),
                 ('save', job.id, job.record())]
        with self.assertLogs('video_studio', level='ERROR'):
            asyncio.run(manager._write_batch(batch))
        self.assertEqual(store.calls, 3, 'the warning itself must not trigger more writes')
        self.assertIn('disk is full', job.fields['storage_warning'])
        warnings = [line for line in job.logs if line.startswith('WARNING: job history')]
        self.assertEqual(len(warnings), 1)


def set_user_version(path, version):
    db = sqlite3.connect(path)  # closed explicitly: Windows can't delete an open file
    try:
        db.execute(f'PRAGMA user_version = {version}')
        db.commit()
    finally:
        db.close()


class MigrationTests(unittest.TestCase):
    def open(self, path):
        async def go():
            store = await JobStore(path).open()
            version = await store.schema_version()
            await store.close()
            return version
        return asyncio.run(go())

    def test_new_and_unversioned_databases_become_version_1(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, 'jobs.db')
            self.assertEqual(self.open(path), 1)
            set_user_version(path, 0)  # as written before versioning existed
            self.assertEqual(self.open(path), 1)

    def test_newer_database_is_refused_not_damaged(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, 'jobs.db')
            self.open(path)
            set_user_version(path, 99)
            with self.assertRaises(NewerSchemaError):
                self.open(path)

    def test_prune_keeps_newest_and_active_jobs_and_drops_orphan_events(self):
        async def go(path):
            store = await JobStore(path).open()
            for index in range(5):
                status = 'running' if index == 0 else 'completed'
                await store.save_job({'id': f'job{index}', 'kind': 'recovery', 'status': status,
                                      'stage': status, 'created': index, 'snapshot': {}})
                await store.append_event(f'job{index}', {'id': 1, 'ts': '', 'type': 'log', 'data': {}})
            removed = await store.prune(keep=2)
            jobs = {row['id'] for row in await store.recent(limit=10)}
            events = await store.events_since('job1')
            await store.close()
            return removed, jobs, events
        with tempfile.TemporaryDirectory() as temp:
            removed, jobs, events = asyncio.run(go(os.path.join(temp, 'jobs.db')))
        self.assertEqual(removed, 2)
        self.assertEqual(jobs, {'job4', 'job3', 'job0'})  # newest two, plus the running one
        self.assertEqual(events, [])

    def test_default_keep_is_generous(self):
        self.assertGreaterEqual(store_module.KEEP_JOBS, 100)


class JobIdLoggingTests(unittest.TestCase):
    def test_filter_adds_the_current_job_id(self):
        record = logging.LogRecord('video_studio', logging.INFO, __file__, 1, 'msg', (), None)
        token = JOB_ID.set('abcd1234')
        try:
            JobIdFilter().filter(record)
        finally:
            JOB_ID.reset(token)
        self.assertEqual(record.job_id, 'abcd1234')

    def test_media_job_threads_inherit_the_job_id(self):
        job = Job(QuietManager(), 'media')
        seen = {}

        def run(job=None, cancel_check=None):
            seen['id'], seen['thread'] = JOB_ID.get(), threading.current_thread().name
            return {'output_file': 'x', 'command': 'y'}

        ENCODER.try_acquire()
        asyncio.run(media_flow(job, 'cut', run, {}))
        self.assertEqual(seen['id'], job.id[:8])
        self.assertNotEqual(seen['thread'], 'MainThread')
        self.assertEqual(JOB_ID.get(), '-', 'the id must not leak outside the job')


if __name__ == '__main__':
    unittest.main()
