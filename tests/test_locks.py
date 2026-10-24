"""Separate RECOVERY and ENCODER locks: imaging and media work may overlap safely."""

import asyncio
import os
import tempfile
import unittest
from unittest import mock

from vs_app.api import policy
from vs_app.api.routes import media as media_routes
from vs_app.core import recovery as core
from vs_app.orchestration.flows import repair_flow
from vs_app.orchestration.gate import ENCODER, RECOVERY, release_all, try_acquire_all
from vs_app.orchestration.jobs import JobManager


class GateTests(unittest.TestCase):
    def tearDown(self):
        release_all(RECOVERY, ENCODER)

    def test_acquire_all_takes_everything_or_nothing(self):
        ENCODER.try_acquire()
        self.assertFalse(try_acquire_all(RECOVERY, ENCODER))
        self.assertTrue(RECOVERY.try_acquire(), 'RECOVERY must not stay taken after a failed claim')

    def test_repair_flow_releases_both_locks(self):
        self.assertTrue(try_acquire_all(RECOVERY, ENCODER))
        with mock.patch('vs_app.orchestration.flows.repair_worker'):
            asyncio.run(repair_flow(mock.Mock(), [], 'ffmpeg', 'ffprobe', 'out'))
        self.assertTrue(try_acquire_all(RECOVERY, ENCODER))


class MediaBesideRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.manager = JobManager(store=None, bus=mock.Mock())
        self.patch = mock.patch.object(media_routes.runtime, 'JOBS', self.manager)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        release_all(RECOVERY, ENCODER)

    def test_media_job_runs_while_a_recovery_holds_its_lock(self):
        RECOVERY.try_acquire()  # imaging is running
        run = mock.Mock(return_value={'output_file': 'out.mp4', 'command': 'ffmpeg'})
        result = asyncio.run(media_routes._run_media_job(
            'transcode', 'Transcode', run, {'output_path': 'out.mp4'}, lambda value: 'done'))
        self.assertTrue(result['success'], result)

    def test_output_inside_the_running_recovery_folder_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = os.path.join(temp, 'run')
            image_wsl = core.win_to_wsl_path(os.path.join(run_dir, 'pendrive_backup.img'))
            self.manager.create('recovery', fields={'image': image_wsl})  # active job
            with self.assertRaises(policy.PolicyError) as caught:
                media_routes._refuse_inside_active_recovery(os.path.join(run_dir, 'carved.1', 'x.mp4'))
            self.assertIn('recovery running now', str(caught.exception))
            media_routes._refuse_inside_active_recovery(os.path.join(temp, 'elsewhere', 'x.mp4'))

    def test_no_recovery_means_no_restriction(self):
        media_routes._refuse_inside_active_recovery(os.path.join(tempfile.gettempdir(), 'x.mp4'))


if __name__ == '__main__':
    unittest.main()
