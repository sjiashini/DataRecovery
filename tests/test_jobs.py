import unittest

from vs_app.orchestration import jobs as jr


class FakeManager:
    def __init__(self):
        self.published = []
        self.persisted = []
        self.saved = []

    def publish(self, job_id, event):
        self.published.append((job_id, event))

    def enqueue_event(self, job_id, event):
        self.persisted.append((job_id, event))

    def enqueue_save(self, record):
        self.saved.append(record)


def make_job(**fields):
    manager = FakeManager()
    return jr.Job(manager, 'recovery', fields), manager


class JobTests(unittest.TestCase):
    def test_stage_lifecycle_updates_stage_and_progress(self):
        job, manager = make_job(progress={'imaging': {'state': 'pending'}})
        job.add_stage('imaging', 'Imaging', 'abort')
        job.start()
        job.start_stage('imaging')
        self.assertEqual(job.stages[0]['state'], 'running')
        self.assertEqual(job.public()['stage'], 'imaging')

        job.finish_stage('imaging', 'completed', detail='done', seconds=12.345)
        self.assertEqual(job.stages[0]['state'], 'completed')
        self.assertEqual(job.stages[0]['seconds'], 12.3)
        self.assertEqual(job.progress['imaging']['state'], 'completed')
        self.assertEqual(job.progress['imaging']['seconds'], 12.3)
        types = [event['type'] for _, event in manager.published]
        self.assertIn('stage', types)
        self.assertIn('state', types)

    def test_explicit_off_progress_is_not_overwritten(self):
        job, _ = make_job(progress={'carving': {'state': 'off'}})
        job.add_stage('carving', 'Carving', 'report')
        job.finish_stage('carving', 'skipped')
        self.assertEqual(job.progress['carving']['state'], 'off')
        self.assertEqual(job.stages[0]['state'], 'skipped')

    def test_finish_marks_pending_stages_skipped_and_closes_job(self):
        job, manager = make_job()
        job.add_stage('imaging', 'Imaging', 'abort')
        job.add_stage('carving', 'Carving', 'report')
        job.start()
        job.start_stage('imaging')
        job.finish('cancelled')
        self.assertFalse(job.active)
        self.assertEqual(job.status, 'cancelled')
        self.assertEqual(job.stages[0]['state'], 'cancelled')
        self.assertEqual(job.stages[1]['state'], 'skipped')
        self.assertIsNotNone(job.finished)
        self.assertEqual(manager.persisted[-1][1]['type'], 'end')
        self.assertFalse(job.finish('completed'))

    def test_public_snapshot_has_legacy_keys_and_no_handles(self):
        job, _ = make_job(image='/mnt/c/r/disk.img')
        snapshot = job.public()
        for key in ('id', 'kind', 'active', 'done', 'status', 'stage', 'outcome',
                    'elapsed', 'progress', 'stages', 'stage_order', 'log', 'image'):
            self.assertIn(key, snapshot)
        self.assertNotIn('_proc', snapshot)
        self.assertNotIn('_lock', snapshot)
        self.assertEqual(snapshot['stage'], 'starting')
        self.assertEqual(job.record()['snapshot']['image'], '/mnt/c/r/disk.img')

    def test_cancel_flag_and_event(self):
        job, manager = make_job()
        self.assertFalse(job.cancelled)
        job.request_cancel()
        self.assertTrue(job.cancelled)
        self.assertTrue(manager.persisted[-1][1]['data']['cancel_requested'])

    def test_state_events_are_not_numbered(self):
        job, manager = make_job()
        job.start_stage('imaging')
        state_events = [event for _, event in manager.published if event['type'] == 'state']
        self.assertTrue(state_events)
        self.assertNotIn('id', state_events[-1])
        numbered = [event for _, event in manager.persisted if event['type'] == 'stage']
        self.assertIn('id', numbered[-1])


class FakeBus:
    def publish(self, job_id, event):
        pass


class JobManagerTests(unittest.TestCase):
    def test_only_one_active_job_per_kind(self):
        manager = jr.JobManager(store=None, bus=FakeBus())
        first = manager.create('recovery')
        self.assertIsNotNone(first)
        self.assertIsNone(manager.create('recovery'))
        self.assertIsNotNone(manager.create('repair'))
        first.finish('completed')
        second = manager.create('recovery')
        self.assertIsNotNone(second)
        self.assertIs(manager.get(second.id), second)
        self.assertEqual(manager.snapshot('recovery')['status'], 'queued')

    def test_missing_job_snapshot_is_none(self):
        manager = jr.JobManager(store=None, bus=FakeBus())
        self.assertIsNone(manager.get('nope'))
        self.assertIsNone(manager.snapshot('recovery'))


if __name__ == '__main__':
    unittest.main()
