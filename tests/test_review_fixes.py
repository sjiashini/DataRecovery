"""Regression tests for the recovery-path review findings (F1-F8)."""

import asyncio
import dataclasses
import json
import os
import tempfile
import unittest
from unittest import mock

from vs_app import settings
from vs_app.api import policy
from vs_app.api.routes import recovery as recovery_routes
from vs_app.api.routes import system as system_routes
from vs_app.api.schemas import RecoverAllRequest
from vs_app.core import recovery as core
from vs_app.execution import adapters, bridge
from vs_app.orchestration.gate import ENCODER, RECOVERY
from vs_app.orchestration.jobs import Job, JobManager

ROOT = '/mnt/x/Recovery'
MAPFILE = """# Mapfile. Created by GNU ddrescue version 1.27
# current_pos  current_status  current_pass
0x07A20000     +               1
#      pos        size  status
0x00000000  0x07A20000  +
0x07A20000  0x00010000  -
0x07A30000  0x005D0000  ?
"""


class QuietManager:
    def publish(self, job_id, event):
        pass

    def enqueue_event(self, job_id, event):
        pass

    def enqueue_save(self, record):
        pass


def with_roots(recovery=None, media=None):
    original = settings.SETTINGS
    replaced = dataclasses.replace(
        original,
        recovery_roots=tuple(recovery if recovery is not None else original.recovery_roots),
        media_roots=tuple(media if media is not None else original.media_roots))
    return mock.patch.object(settings, 'SETTINGS', replaced)


class ImageSourceTests(unittest.TestCase):
    """F1: never resume an image from a different disc or drive."""

    def test_mapfile_extent_is_the_source_size(self):
        self.assertEqual(core.mapfile_extent(MAPFILE), 0x08000000)
        self.assertIsNone(core.mapfile_extent('# only comments\n'))

    def test_different_size_or_serial_is_refused(self):
        saved = {'size': 1000, 'serial': 'AAA', 'uuid': '', 'label': ''}
        self.assertIn('bytes', core.source_mismatch(saved, {'size': 2000, 'serial': 'AAA'}))
        self.assertIn('serial', core.source_mismatch(saved, {'size': 1000, 'serial': 'BBB'}))
        self.assertIn('mapfile', core.source_mismatch({}, {'size': 5}, mapfile_size=6))

    def test_same_source_or_unknown_details_may_resume(self):
        saved = {'size': 1000, 'serial': 'AAA', 'uuid': 'U1', 'label': 'DISC'}
        self.assertIsNone(core.source_mismatch(saved, dict(saved)))
        self.assertIsNone(core.source_mismatch(saved, {'size': 1000, 'serial': ''}))
        self.assertIsNone(core.source_mismatch({}, {'size': 1000}, mapfile_size=1000))

    def test_existing_image_from_another_disc_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            image = os.path.join(temp, 'pendrive_backup.img')
            with open(image + '.log', 'w', encoding='utf-8') as handle:
                handle.write(MAPFILE)
            with open(image + '.source.json', 'w', encoding='utf-8') as handle:
                json.dump({'size': 0x08000000, 'serial': 'DISC-A'}, handle)
            other = {'size': 0x08000000, 'serial': 'DISC-B', 'uuid': '', 'label': ''}
            with mock.patch.object(bridge, 'device_identity', return_value=other):
                problem = bridge.image_source_problem(image, '/dev/sdb')
            self.assertIn('different disc or drive', problem)
            same = dict(other, serial='DISC-A')
            with mock.patch.object(bridge, 'device_identity', return_value=same):
                self.assertIsNone(bridge.image_source_problem(image, '/dev/sdb'))

    def test_new_image_needs_no_check(self):
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(bridge, 'device_identity') as identity:
            self.assertIsNone(bridge.image_source_problem(os.path.join(temp, 'new.img'), '/dev/sdb'))
        identity.assert_not_called()


class MediaOutputTests(unittest.TestCase):
    """F2: a cut/join/transcode output never replaces recovered data."""

    def test_non_video_output_is_refused(self):
        with tempfile.TemporaryDirectory() as temp, with_roots(media=[temp]):
            with self.assertRaises(policy.PolicyError):
                policy.require_media_output(os.path.join(temp, 'pendrive_backup.img'))
            policy.require_media_output(os.path.join(temp, 'clip.mp4'))

    def test_existing_file_in_a_recovery_folder_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            recovered = os.path.join(temp, 'carved.1', 'f0001.mp4')
            os.makedirs(os.path.dirname(recovered))
            with open(recovered, 'wb') as handle:
                handle.write(b'\0')
            with with_roots(recovery=[temp], media=[temp]):
                with self.assertRaises(policy.PolicyError) as caught:
                    policy.require_media_output(recovered)
                self.assertIn('recovered data', str(caught.exception))
                policy.require_media_output(os.path.join(temp, 'carved.1', 'new.mp4'))


class RecoverAllTests(unittest.TestCase):
    """F3 and F7 at the route: the real run gets the shown sector size; paths stay inside."""

    def call(self, **fields):
        drives = [{'device': '/dev/sdb', 'type': 'usb', 'sector_size': 4096}]
        captured = {}

        def fake_flow(job, spec):
            captured['spec'] = spec

        manager = JobManager(store=None, bus=mock.Mock())
        patches = [
            mock.patch.object(bridge, 'ensure_wsl_alive'),
            mock.patch.object(bridge, 'scan_recovery_drives', return_value=drives),
            mock.patch.object(bridge, 'check_device_readable', return_value=None),
            mock.patch.object(bridge, 'image_source_problem', return_value=None),
            mock.patch.object(bridge, 'image_busy', return_value=False),
            mock.patch.object(bridge, 'linux_path_exists', return_value=True),
            mock.patch.object(bridge, 'read_source_record', return_value={'size': 1}),
            mock.patch.object(recovery_routes, 'recovery_flow', fake_flow),
            mock.patch.object(recovery_routes, '_launch'),
            mock.patch.object(recovery_routes.runtime, 'JOBS', manager),
            with_roots(recovery=[ROOT]),
        ]
        for patch in patches:
            patch.start()
        try:
            result = asyncio.run(recovery_routes.recover_all(RecoverAllRequest(**fields)))
        finally:
            for patch in reversed(patches):
                patch.stop()
            RECOVERY.release()
        return result, captured.get('spec')

    def test_detected_sector_size_reaches_the_real_run(self):
        result, spec = self.call(device='/dev/sdb', output=ROOT + '/run1/disk.img')
        self.assertTrue(result['success'], result)
        self.assertIn('-b 4096', result['commands']['ddrescue'])
        self.assertEqual(spec.sector_size, 4096)
        self.assertEqual(core.build_ddrescue_argv(spec.device, spec.image, spec.mode,
                                                  spec.sector_size)[1:3], ['-b', '4096'])

    def test_imaging_can_start_while_a_transcode_runs(self):
        ENCODER.try_acquire()  # a cut/join/transcode is running
        try:
            result, spec = self.call(device='/dev/sdb', output=ROOT + '/run2/disk.img')
        finally:
            ENCODER.release()
        self.assertTrue(result['success'], result)
        self.assertIsNotNone(spec)

    def test_output_equal_to_a_root_is_refused(self):
        result, spec = self.call(device='/dev/sdb', output=ROOT)
        self.assertFalse(result['success'])
        self.assertIn('not the root folder itself', result['error'])
        self.assertIsNone(spec)

    def test_carve_folder_equal_to_a_root_is_refused(self):
        result, spec = self.call(output=ROOT + '/run1/disk.img', recup_dir=ROOT,
                                 skip_imaging=True)
        self.assertFalse(result['success'])
        self.assertIn('Carve folder must be a file or folder inside', result['error'])
        self.assertIsNone(spec)


class PathConversionTests(unittest.TestCase):
    """F8: the converted path is the one that was checked."""

    def test_quotes_are_stripped_like_the_checks_do(self):
        self.assertEqual(core.win_to_wsl_path('"/mnt/c/PendriveRecovery/x.img"'),
                         '/mnt/c/PendriveRecovery/x.img')
        self.assertEqual(core.win_to_wsl_path('"C:\\PendriveRecovery\\x.img"'),
                         '/mnt/c/PendriveRecovery/x.img')


class CancelTests(unittest.TestCase):
    """F4 and F6: a cancel is never lost, and shutdown stops the tool."""

    def test_cancel_before_start_never_creates_the_process(self):
        job = Job(QuietManager(), 'recovery')
        job.request_cancel()
        with mock.patch.object(adapters.asyncio, 'create_subprocess_exec') as create:
            with self.assertRaises(adapters.StageCancelled):
                asyncio.run(adapters.spawn(job, ['ddrescue'], '/mnt/x/a.img'))
        create.assert_not_called()

    def test_cancel_during_start_is_delivered(self):
        job = Job(QuietManager(), 'recovery')

        async def create(*_args, **_kwargs):
            job.request_cancel()  # arrives while the process is being created
            return mock.Mock(returncode=None)

        cancel = mock.AsyncMock()
        with mock.patch.object(adapters.asyncio, 'create_subprocess_exec', side_effect=create), \
                mock.patch.object(adapters, 'cancel_process', cancel):
            asyncio.run(adapters.spawn(job, ['ddrescue'], '/mnt/x/a.img'))
        cancel.assert_awaited_once_with(job)

    def test_backend_shutdown_stops_a_running_ddrescue(self):
        job = Job(QuietManager(), 'recovery')

        class Forever:
            async def readline(self):
                await asyncio.sleep(3600)

        proc = mock.Mock(stdout=Forever())
        cancel = mock.AsyncMock()

        async def scenario():
            adapter = adapters.DdrescueAdapter('/dev/sdb', '/mnt/x/a.img')
            task = asyncio.create_task(adapter.run(job))
            await asyncio.sleep(0.05)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        with mock.patch.object(adapters, 'async_mkdir', mock.AsyncMock()), \
                mock.patch.object(adapters, 'spawn', mock.AsyncMock(return_value=(proc, 'pid'))), \
                mock.patch.object(adapters, '_stream_exit', mock.AsyncMock(return_value=0)), \
                mock.patch.object(adapters, 'cancel_process', cancel):
            asyncio.run(scenario())
        cancel.assert_awaited_once_with(job)


class UsbDuringRecoveryTests(unittest.TestCase):
    """F5: Share/Release can't pull the source out from under a running ddrescue."""

    def test_share_and_release_are_refused_while_recovering(self):
        active = mock.Mock(active=True)
        with mock.patch.object(system_routes, 'IS_WINDOWS', True), \
                mock.patch.object(system_routes.runtime.JOBS, 'current', return_value=active):
            for call in (system_routes._usbipd_bind, system_routes._usbipd_release):
                result = call('2-1')
                self.assertFalse(result['success'])
                self.assertIn('recovery is running', result['error'].lower())


class SetAsideTests(unittest.TestCase):
    """F7 follow-up: moving unselected Office files never overwrites one."""

    def test_free_path_never_reuses_a_name(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, 'f0001.zip')
            self.assertEqual(bridge.free_path(path), path)
            for name in ('f0001.zip', 'f0001 (2).zip'):
                open(os.path.join(temp, name), 'w').close()
            self.assertEqual(bridge.free_path(path), os.path.join(temp, 'f0001 (3).zip'))


if __name__ == '__main__':
    unittest.main()
