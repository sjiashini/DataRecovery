"""Cut, join and transcode run as cancellable `media` jobs (review finding F9)."""

import asyncio
import sys
import time
import unittest
from unittest import mock

from vs_app.api.routes import media as media_routes
from vs_app.execution import adapters, media
from vs_app.orchestration.flows import media_flow
from vs_app.orchestration.gate import ENCODER
from vs_app.orchestration.jobs import Job, JobManager


class QuietManager:
    def publish(self, job_id, event):
        pass

    def enqueue_event(self, job_id, event):
        pass

    def enqueue_save(self, record):
        pass


def new_job():
    return Job(QuietManager(), 'media')


def run_flow(job, run, kwargs=None):
    ENCODER.try_acquire()  # the route holds ENCODER before it launches the flow
    return asyncio.run(media_flow(job, 'cut', run, kwargs or {}))


class MediaFlowTests(unittest.TestCase):
    def tearDown(self):
        ENCODER.release()

    def test_success_finishes_the_job_and_frees_the_gate(self):
        job = new_job()
        outcome, result = run_flow(job, lambda **_kw: {'output_file': 'out.mp4', 'command': 'ffmpeg'})
        self.assertEqual((outcome, result['output_file']), ('completed', 'out.mp4'))
        self.assertEqual(job.status, 'completed')
        self.assertTrue(ENCODER.try_acquire(), 'ENCODER must be free after the job')

    def test_media_error_fails_the_job(self):
        def run(**_kw):
            raise media.MediaError('ffmpeg exited with code 1')

        job = new_job()
        self.assertEqual(run_flow(job, run), ('failed', 'ffmpeg exited with code 1'))
        self.assertEqual(job.status, 'failed')
        self.assertTrue(ENCODER.try_acquire())

    def test_cancel_stops_a_real_running_process(self):
        """Cancel goes through the job and ends the actual child process quickly."""
        job = new_job()
        sleeper = [sys.executable, '-c', 'import time; time.sleep(60)']

        def run(job=None, cancel_check=None):
            media.capture(sleeper, job=job, cancel_check=cancel_check)
            return {'output_file': 'never', 'command': 'sleep'}

        async def scenario():
            ENCODER.try_acquire()
            task = asyncio.create_task(media_flow(job, 'transcode', run, {}))
            for _ in range(100):  # wait until the child process is attached
                if job.process_state()[0] is not None:
                    break
                await asyncio.sleep(0.05)
            started = time.time()
            job.request_cancel()
            await adapters.cancel_process(job)
            outcome = await asyncio.wait_for(task, 20)
            return outcome, time.time() - started

        (outcome, _value), seconds = asyncio.run(scenario())
        self.assertEqual(outcome, 'cancelled')
        self.assertEqual(job.status, 'cancelled')
        self.assertLess(seconds, 15)
        self.assertTrue(ENCODER.try_acquire())

    def test_cancel_before_start_never_launches_the_tool(self):
        with mock.patch.object(media.subprocess, 'Popen') as popen:
            with self.assertRaises(media.MediaCancelled):
                media.capture(['ffmpeg'], job=new_job(), cancel_check=lambda: True)
        popen.assert_not_called()

    def test_backend_shutdown_stops_the_media_tool(self):
        job = new_job()
        cancel = mock.AsyncMock()

        def run(**_kw):
            time.sleep(0.5)
            return {'output_file': 'x', 'command': 'y'}

        async def scenario():
            ENCODER.try_acquire()
            task = asyncio.create_task(media_flow(job, 'cut', run, {}))
            await asyncio.sleep(0.05)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        with mock.patch('vs_app.orchestration.flows.cancel_process', cancel):
            asyncio.run(scenario())
        cancel.assert_awaited_once_with(job)
        self.assertTrue(job.cancelled)
        self.assertTrue(ENCODER.try_acquire())


class MediaRouteTests(unittest.TestCase):
    def setUp(self):
        self.jobs = mock.patch.object(media_routes.runtime, 'JOBS', JobManager(store=None, bus=mock.Mock()))
        self.jobs.start()

    def tearDown(self):
        self.jobs.stop()
        ENCODER.release()

    def test_response_keeps_its_shape_and_adds_the_job_id(self):
        run = mock.Mock(return_value={'output_file': 'C:/out.mp4', 'command': 'ffmpeg -i in'})
        result = asyncio.run(media_routes._run_media_job(
            'cut', 'Cut', run, {'output_path': 'C:/out.mp4'}, lambda value: 'done'))
        self.assertTrue(result['success'])
        self.assertEqual(result['output_file'], 'C:/out.mp4')
        self.assertEqual(result['message'], 'done')
        self.assertIn('job_id', result)
        self.assertEqual(media_routes.runtime.JOBS.current('media').status, 'completed')

    def test_busy_gate_is_reported(self):
        ENCODER.try_acquire()
        result = asyncio.run(media_routes._run_media_job(
            'cut', 'Cut', mock.Mock(), {'output_path': 'x.mp4'}, str))
        self.assertFalse(result['success'])
        self.assertIn('Another cut, join, transcode or repair', result['error'])

    def test_cancel_with_nothing_running(self):
        result = asyncio.run(media_routes.media_cancel())
        self.assertFalse(result['success'])


if __name__ == '__main__':
    unittest.main()
