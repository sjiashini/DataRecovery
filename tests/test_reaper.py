import subprocess
import sys
import unittest
from unittest import mock

from vs_app.execution import adapters, bridge

OWN = 4242
UUID = '0123456789abcdef0123456789abcdef'


def pidfile(owner):
    return f'/tmp/video-studio-{owner}-{UUID}.pid'


class OrphanSelectionTests(unittest.TestCase):
    def test_only_pidfiles_of_dead_backends_are_orphans(self):
        paths = [pidfile(OWN), pidfile(100), pidfile(200)]
        orphans = adapters.orphan_pidfiles(paths, lambda pid: pid == 100, own_pid=OWN)
        self.assertEqual(orphans, [pidfile(200)])

    def test_malformed_names_are_ignored(self):
        paths = ['/tmp/video-studio-x-abc.pid', '/tmp/other.pid',
                 f'/tmp/video-studio-1-{UUID}.pid; rm -rf /', '']
        self.assertEqual(adapters.orphan_pidfiles(paths, lambda pid: False, own_pid=OWN), [])

    def test_names_written_by_tracked_argv_are_recognised(self):
        _wrapper, path = adapters.tracked_argv(['ddrescue', '/dev/sdb', 'x.img'])
        self.assertRegex(path, adapters.PIDFILE_RE)


class ReapTests(unittest.TestCase):
    def run_reaper(self, listing, alive=lambda pid: False, reap_stdout=''):
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            stdout = listing if len(calls) == 1 else reap_stdout
            return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr='')

        with mock.patch.object(adapters.subprocess, 'run', side_effect=fake_run), \
                mock.patch.object(adapters.bridge, 'process_alive', side_effect=alive):
            reaped = adapters.reap_orphan_tools()
        return reaped, calls

    def test_nothing_to_reap_runs_only_the_scan(self):
        reaped, calls = self.run_reaper('')
        self.assertEqual(reaped, [])
        self.assertEqual(len(calls), 1)

    def test_orphans_are_passed_as_arguments_not_interpolated(self):
        reaped, calls = self.run_reaper(pidfile(200) + '\n',
                                        reap_stdout='reaped 31337 ddrescue\n')
        self.assertEqual(reaped, ['reaped 31337 ddrescue'])
        reap_argv = calls[1]
        self.assertEqual(reap_argv[-1], pidfile(200))
        self.assertNotIn(pidfile(200), adapters.REAP_SCRIPT)
        self.assertIn('video-studio-reaper', reap_argv)

    def test_live_backend_pidfiles_are_left_alone(self):
        reaped, calls = self.run_reaper(pidfile(200) + '\n', alive=lambda pid: True)
        self.assertEqual(reaped, [])
        self.assertEqual(len(calls), 1)

    def test_scan_failure_is_not_fatal(self):
        with mock.patch.object(adapters.subprocess, 'run', side_effect=OSError('no wsl')):
            self.assertEqual(adapters.reap_orphan_tools(), [])


class ProcessAliveTests(unittest.TestCase):
    def test_current_process_is_alive(self):
        self.assertTrue(bridge.process_alive(bridge.os.getpid()))

    def test_exited_process_is_not_alive(self):
        proc = subprocess.Popen([sys.executable, '-c', 'pass'])
        proc.wait()
        self.assertFalse(bridge.process_alive(proc.pid))

    def test_invalid_pid_is_not_alive(self):
        self.assertFalse(bridge.process_alive(0))


if __name__ == '__main__':
    unittest.main()
