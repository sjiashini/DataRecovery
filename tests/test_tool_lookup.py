import os
import unittest
from unittest import mock

from vs_app import main
from vs_app.execution import media


class MergePathTests(unittest.TestCase):
    def test_new_entries_are_appended_once_in_order(self):
        sep = os.pathsep
        merged = media.merge_path(sep.join(['/a', '/b']), ['/b', '/c', '/c', ' /d '])
        self.assertEqual(merged, sep.join(['/a', '/b', '/c', '/d']))

    def test_trailing_slashes_and_blanks_are_not_new_entries(self):
        current = os.pathsep.join(['/a', '/b'])
        self.assertEqual(media.merge_path(current, ['/a/', '', '  ']), current)


class WhichTests(unittest.TestCase):
    def test_tool_installed_after_start_is_found_after_refresh(self):
        found = {'ready': False}

        def fake_which(name):
            return '/tools/HandBrakeCLI' if found['ready'] and name == 'HandBrakeCLI' else None

        def fake_refresh():
            found['ready'] = True
            return True

        with mock.patch.object(media.shutil, 'which', side_effect=fake_which), \
                mock.patch.object(media, 'refresh_path', side_effect=fake_refresh):
            self.assertEqual(media.which('HandBrakeCLI', 'HandBrakeCLI.exe'),
                             '/tools/HandBrakeCLI')

    def test_no_retry_when_path_did_not_change(self):
        with mock.patch.object(media.shutil, 'which', return_value=None) as which, \
                mock.patch.object(media, 'refresh_path', return_value=False):
            self.assertIsNone(media.which('untrunc', 'untrunc.exe'))
        self.assertEqual(which.call_count, 2)

    @unittest.skipUnless(os.name == 'nt', 'reads the Windows registry PATH')
    def test_refresh_adds_registry_entries_to_this_process(self):
        original = os.environ['PATH']
        try:
            with mock.patch.object(media, '_registry_path_entries',
                                   return_value=[r'C:\NewTool\bin']):
                self.assertTrue(media.refresh_path())
                self.assertTrue(os.environ['PATH'].endswith(r'C:\NewTool\bin'))
                self.assertFalse(media.refresh_path())
        finally:
            os.environ['PATH'] = original


class RelaunchTests(unittest.TestCase):
    def test_second_launch_opens_the_running_instance_instead_of_starting(self):
        with mock.patch.object(main, 'server_is_running', return_value=True), \
                mock.patch.object(main.webbrowser, 'open') as browser, \
                mock.patch.object(main.uvicorn, 'run') as server, \
                mock.patch.object(main.asyncio, 'set_event_loop_policy'):
            self.assertEqual(main.run(port=18099, open_browser=True), 0)
        browser.assert_called_once_with('http://127.0.0.1:18099/')
        server.assert_not_called()


if __name__ == '__main__':
    unittest.main()
