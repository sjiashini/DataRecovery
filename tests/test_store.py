import os
import tempfile
import unittest

from vs_app.storage.store import JobStore


def make_record(job_id='job-1', kind='recovery', status='running', progress=None):
    snapshot = {
        'id': job_id,
        'kind': kind,
        'status': status,
        'stage': 'imaging' if status == 'running' else status,
        'active': status in ('queued', 'running'),
        'done': status not in ('queued', 'running'),
        'progress': progress or {'imaging': {'state': 'running', 'pct': 10.0}},
    }
    return {
        'id': job_id, 'kind': kind, 'status': status, 'stage': snapshot['stage'],
        'created': 1.0, 'started': 2.0,
        'finished': None if status == 'running' else 3.0,
        'error': None, 'snapshot': snapshot,
    }


class JobStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = await JobStore(os.path.join(self.temp.name, 'jobs.db')).open()

    async def asyncTearDown(self):
        await self.store.close()
        self.temp.cleanup()

    async def test_save_and_load_snapshot(self):
        await self.store.save_job(make_record())
        snapshot = await self.store.get_job('job-1')
        self.assertEqual(snapshot['status'], 'running')
        await self.store.save_job(make_record(status='completed'))
        self.assertEqual((await self.store.get_job('job-1'))['status'], 'completed')
        self.assertEqual(await self.store.get_job('missing'), None)

    async def test_latest_for_kind_skips_running_jobs(self):
        await self.store.save_job(make_record('old', status='completed'))
        await self.store.save_job(make_record('live', status='running'))
        latest = await self.store.latest_for_kind('recovery')
        self.assertEqual(latest['id'], 'old')
        self.assertIsNone(await self.store.latest_for_kind('repair'))

    async def test_events_are_replayable_in_order(self):
        for seq in (1, 2, 3):
            await self.store.append_event('job-1', {
                'id': seq, 'type': 'log', 'ts': '2026-10-20T10:00:00',
                'data': {'message': f'line {seq}'},
            })
        events = await self.store.events_since('job-1', after=0)
        self.assertEqual([event['id'] for event in events], [1, 2, 3])
        self.assertEqual(events[1]['data']['message'], 'line 2')
        self.assertEqual([event['id'] for event in await self.store.events_since('job-1', after=2)], [3])
        self.assertEqual(len(await self.store.events_since('job-1', after=0, limit=2)), 2)

    async def test_mark_interrupted_fails_open_jobs(self):
        await self.store.save_job(make_record('live', status='running'))
        await self.store.save_job(make_record('done', status='completed',
                                             progress={'imaging': {'state': 'completed'}}))
        self.assertEqual(await self.store.mark_interrupted(), 1)
        live = await self.store.get_job('live')
        self.assertEqual(live['status'], 'failed')
        self.assertEqual(live['stage'], 'failed')
        self.assertEqual(live['outcome'], 'failed')
        self.assertFalse(live['active'])
        self.assertIn('restarted', live['error'])
        self.assertEqual(live['progress']['imaging']['state'], 'failed')
        self.assertEqual((await self.store.get_job('done'))['status'], 'completed')
        self.assertEqual(await self.store.mark_interrupted(), 0)


if __name__ == '__main__':
    unittest.main()
