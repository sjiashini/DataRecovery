import dataclasses
import os
import tempfile
import unittest

from vs_app.api import policy


class SandboxTests(unittest.TestCase):
    def test_path_inside_root_is_allowed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, 'disk.img')
            self.assertTrue(policy.within(path, [temp]))
            self.assertTrue(policy.within(temp, [temp]))
            policy.require_within(path, [temp], 'Output image')

    def test_path_outside_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(policy.PolicyError):
                policy.require_within(os.path.join(temp, 'a', 'disk.img'),
                                      [os.path.join(temp, 'b')], 'Output image')
            with self.assertRaises(policy.PolicyError):
                policy.require_within(os.path.join(temp, '..', 'elsewhere.img'),
                                      [temp], 'Output image')

    def test_empty_path_is_rejected(self):
        with self.assertRaises(policy.PolicyError):
            policy.require_within('', [os.getcwd()], 'Output image')
        self.assertFalse(policy.within('', [os.getcwd()]))


def make_dir_link(link, target):
    """Directory junction on Windows (no admin needed), symlink elsewhere."""
    if os.name == 'nt':
        import _winapi
        _winapi.CreateJunction(target, link)
    else:
        os.symlink(target, link, target_is_directory=True)


class LinkEscapeTests(unittest.TestCase):
    def test_link_inside_root_pointing_outside_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = os.path.join(temp, 'root')
            outside = os.path.join(temp, 'outside')
            os.makedirs(root)
            os.makedirs(outside)
            link = os.path.join(root, 'link')
            try:
                make_dir_link(link, outside)
            except OSError as exc:
                self.skipTest(f'cannot create a directory link here: {exc}')
            self.assertFalse(policy.within(os.path.join(link, 'disk.img'), [root]))
            self.assertTrue(policy.within(os.path.join(root, 'disk.img'), [root]))

    def test_root_that_is_itself_a_link_still_works(self):
        with tempfile.TemporaryDirectory() as temp:
            real = os.path.join(temp, 'real')
            os.makedirs(real)
            link_root = os.path.join(temp, 'root-link')
            try:
                make_dir_link(link_root, real)
            except OSError as exc:
                self.skipTest(f'cannot create a directory link here: {exc}')
            self.assertTrue(policy.within(os.path.join(link_root, 'disk.img'), [link_root]))


class MediaInputTests(unittest.TestCase):
    def test_input_outside_media_roots_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            allowed = os.path.join(temp, 'allowed')
            other = os.path.join(temp, 'other')
            os.makedirs(allowed)
            os.makedirs(other)
            for folder in (allowed, other):
                with open(os.path.join(folder, 'clip.mp4'), 'wb') as handle:
                    handle.write(b'\0')
            self.assertEqual(
                policy.media_input(os.path.join(allowed, 'clip.mp4'), roots=[allowed]),
                os.path.join(allowed, 'clip.mp4'))
            with self.assertRaises(policy.PolicyError) as caught:
                policy.media_input(os.path.join(other, 'clip.mp4'), roots=[allowed])
            self.assertIn('MEDIA_ROOTS', str(caught.exception))

    def test_default_roots_come_from_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, 'clip.mp4')
            with open(path, 'wb') as handle:
                handle.write(b'\0')
            original = policy.settings.SETTINGS
            try:
                policy.settings.SETTINGS = dataclasses.replace(
                    original, media_roots=(os.path.join(temp, 'nope'),))
                with self.assertRaises(policy.PolicyError):
                    policy.media_input(path)
            finally:
                policy.settings.SETTINGS = original


class CarveOptionTests(unittest.TestCase):
    def test_default_and_all_mean_everything(self):
        self.assertIsNone(policy.carve_categories(None))
        self.assertIsNone(policy.carve_categories('all'))
        self.assertIsNone(policy.carve_categories(['all']))

    def test_categories_are_deduplicated(self):
        self.assertEqual(policy.carve_categories(['images', 'images', 'PDF']),
                         ['images', 'pdf'])

    def test_unknown_category_is_rejected(self):
        with self.assertRaises(policy.PolicyError):
            policy.carve_categories(['executables'])

    def test_carve_folder_name_must_be_safe(self):
        self.assertEqual(policy.carve_folder_name(''), 'carved')
        self.assertEqual(policy.carve_folder_name('carved_videos'), 'carved_videos')
        for bad in ('../escape', 'bad name!', '.hidden', 'x' * 80):
            with self.assertRaises(policy.PolicyError):
                policy.carve_folder_name(bad)


if __name__ == '__main__':
    unittest.main()
