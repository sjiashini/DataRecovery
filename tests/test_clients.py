import importlib.util
import os
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name):
    path = os.path.join(ROOT, 'clients', name + '.py')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WatchAndCarveTests(unittest.TestCase):
    def test_importing_does_not_contact_the_backend(self):
        with mock.patch('urllib.request.urlopen', side_effect=AssertionError('network call')) as call, \
                mock.patch('builtins.print') as printed:
            module = load('watch_and_carve')
        call.assert_not_called()
        printed.assert_not_called()
        self.assertTrue(callable(module.main))

    def test_token_is_read_from_the_project_runtime_folder(self):
        with mock.patch.dict(os.environ, {'VS_RUNTIME_DIR': ''}):
            module = load('watch_and_carve')
        self.assertEqual(module.TOKEN_PATH, os.path.join(ROOT, '.runtime', 'api_token'))


if __name__ == '__main__':
    unittest.main()
