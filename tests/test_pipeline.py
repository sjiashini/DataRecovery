import asyncio
import unittest

from vs_app.execution.adapters import StageFailed
from vs_app.orchestration.jobs import Job
from vs_app.orchestration.pipeline import Pipeline, Stage


class QuietManager:
    def publish(self, job_id, event):
        pass

    def enqueue_event(self, job_id, event):
        pass

    def enqueue_save(self, record):
        pass


class FakeAdapter:
    def __init__(self, result=None, error=None):
        self.result = result or {'state': 'completed', 'seconds': 1.5}
        self.error = error
        self.calls = 0

    async def run(self, job):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


def make_job():
    job = Job(QuietManager(), 'recovery')
    job.start()
    return job


def run(stages, job):
    status = asyncio.run(Pipeline(stages).run(job))
    if status in ('completed', 'partial'):
        job.finish(status)
    return status


class PipelineTests(unittest.TestCase):
    def test_successful_stages_complete_in_order(self):
        first, second = FakeAdapter(), FakeAdapter()
        job = make_job()
        job.add_stage('imaging', 'Imaging', 'abort')
        job.add_stage('carving', 'Carving', 'report')
        status = run([Stage('imaging', 'Imaging', first, 'abort'),
                      Stage('carving', 'Carving', second, 'report')], job)
        self.assertEqual(status, 'completed')
        self.assertEqual(job.status, 'completed')
        self.assertEqual((first.calls, second.calls), (1, 1))
        self.assertEqual(job.stages[0]['state'], 'completed')
        self.assertEqual(job.stages[0]['seconds'], 1.5)
        self.assertTrue(any('Stage 1/2' in line for line in job.logs))

    def test_abort_policy_stops_pipeline(self):
        failing = FakeAdapter(error=StageFailed('device vanished'))
        later = FakeAdapter()
        job = make_job()
        job.add_stage('imaging', 'Imaging', 'abort')
        job.add_stage('carving', 'Carving', 'report')
        status = run([Stage('imaging', 'Imaging', failing, 'abort'),
                      Stage('carving', 'Carving', later, 'report')], job)
        self.assertIsNone(status)
        self.assertEqual(job.status, 'failed')
        self.assertIn('device vanished', job.error)
        self.assertEqual(later.calls, 0)

    def test_continue_policy_keeps_going(self):
        failing = FakeAdapter(error=StageFailed('no partition table'))
        later = FakeAdapter()
        job = make_job()
        job.add_stage('testdisk', 'TestDisk', 'continue')
        job.add_stage('carving', 'Carving', 'report')
        status = run([Stage('testdisk', 'TestDisk', failing, 'continue'),
                      Stage('carving', 'Carving', later, 'report')], job)
        self.assertEqual(status, 'completed')
        self.assertEqual(job.status, 'completed')
        self.assertEqual(job.stages[0]['state'], 'failed')
        self.assertEqual(later.calls, 1)

    def test_report_policy_marks_partial(self):
        failing = FakeAdapter(error=StageFailed('carve aborted'))
        job = make_job()
        job.add_stage('carving', 'Carving', 'report')
        status = run([Stage('carving', 'Carving', failing, 'report')], job)
        self.assertEqual(status, 'partial')
        self.assertEqual(job.status, 'partial')

    def test_cancel_before_next_stage(self):
        adapter = FakeAdapter()
        job = make_job()
        job.add_stage('imaging', 'Imaging', 'abort')
        job.request_cancel()
        status = run([Stage('imaging', 'Imaging', adapter, 'abort')], job)
        self.assertIsNone(status)
        self.assertEqual(job.status, 'cancelled')
        self.assertEqual(adapter.calls, 0)

    def test_disabled_stage_is_skipped(self):
        job = make_job()
        job.add_stage('testdisk', 'TestDisk', 'continue')
        status = run([Stage('testdisk', 'TestDisk', None, 'continue', 'disabled')], job)
        self.assertEqual(status, 'completed')
        self.assertEqual(job.stages[0]['state'], 'skipped')
        self.assertEqual(job.stages[0]['detail'], 'disabled')


if __name__ == '__main__':
    unittest.main()
