import unittest

from vs_app.core import recovery as core
from vs_app.execution import adapters


class LockPathTests(unittest.TestCase):
    def test_same_image_spelled_differently_shares_a_lock(self):
        first = core.image_lock_path('/mnt/c/PendriveRecovery/pendrive_backup.img')
        for other in ('/mnt/c/PendriveRecovery/./pendrive_backup.img',
                      '/mnt/c/pendriverecovery/PENDRIVE_BACKUP.IMG',
                      '/mnt/c/PendriveRecovery/x/../pendrive_backup.img'):
            self.assertEqual(core.image_lock_path(other), first, other)

    def test_different_images_get_different_locks(self):
        self.assertNotEqual(core.image_lock_path('/mnt/c/Recovery/a.img'),
                            core.image_lock_path('/mnt/c/Recovery/b.img'))

    def test_lock_lives_on_the_linux_filesystem(self):
        path = core.image_lock_path('/mnt/c/Recovery/a.img')
        self.assertTrue(path.startswith(core.IMAGE_LOCK_DIR + '/'))
        self.assertRegex(path, r'/[0-9a-f]{40}\.lock$')


class WrapperTests(unittest.TestCase):
    def test_locked_argv_passes_the_command_as_arguments(self):
        image = "/mnt/c/Recovery/it's here.img"
        argv = core.locked_argv(['ddrescue', '-b', '512', '/dev/sdb', image], image)
        self.assertEqual(argv[:2], ['bash', '-c'])
        self.assertEqual(argv[3:], ['video-studio-lock', 'ddrescue', '-b', '512',
                                    '/dev/sdb', image])
        self.assertNotIn(image, argv[2])
        self.assertIn('flock -n 9', argv[2])
        self.assertIn(f'exit {core.IMAGE_BUSY_EXIT}', argv[2])
        self.assertTrue(argv[2].endswith('exec "$@"'))

    def test_tracked_tool_takes_the_lock_before_recording_its_pid(self):
        image = '/mnt/c/Recovery/a.img'
        wrapper, pidfile = adapters.tracked_argv(['photorec', image], image)
        script = wrapper[-1]
        self.assertLess(script.index('flock -n 9'), script.index(pidfile))
        self.assertIn(core.image_lock_path(image), script)

    def test_untracked_image_means_no_lock(self):
        wrapper, _pidfile = adapters.tracked_argv(['testdisk', '/list', 'x.img'])
        self.assertNotIn('flock', wrapper[-1])


class BusyExitTests(unittest.TestCase):
    def test_busy_exit_becomes_a_clear_stage_failure(self):
        with self.assertRaises(adapters.StageFailed) as caught:
            adapters.raise_if_image_busy(core.IMAGE_BUSY_EXIT, '/mnt/c/Recovery/a.img')
        self.assertIn('/mnt/c/Recovery/a.img is in use', str(caught.exception))

    def test_other_exit_codes_pass_through(self):
        for code in (0, 1, 2, 3):
            adapters.raise_if_image_busy(code, '/mnt/c/Recovery/a.img')
        adapters.raise_if_image_busy(core.IMAGE_BUSY_EXIT, None)


if __name__ == '__main__':
    unittest.main()
