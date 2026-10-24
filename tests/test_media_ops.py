import os
import tempfile
import unittest

from vs_app.execution import media


class TimeRangeTests(unittest.TestCase):
    def test_valid_range_returns_duration(self):
        start, end, duration = media.validate_time_range('00:00:10', '00:01:00')
        self.assertEqual((start, end), ('00:00:10', '00:01:00'))
        self.assertEqual(duration, 50)

    def test_missing_values_use_defaults(self):
        start, end, duration = media.validate_time_range('', '')
        self.assertEqual((start, end), ('00:00:00', '00:01:00'))
        self.assertEqual(duration, 60)

    def test_end_must_be_after_start(self):
        with self.assertRaises(media.MediaError):
            media.validate_time_range('00:01:00', '00:01:00')
        with self.assertRaises(media.MediaError):
            media.validate_time_range('00:02:00', '00:01:00')

    def test_bad_timecode_is_rejected(self):
        with self.assertRaises(media.MediaError):
            media.validate_time_range('not-a-time', '00:01:00')


class BitwiseJoinTests(unittest.TestCase):
    def test_join_is_byte_exact(self):
        with tempfile.TemporaryDirectory() as temp:
            first = os.path.join(temp, 'a.vob')
            second = os.path.join(temp, 'b.vob')
            output = os.path.join(temp, 'out.vob')
            with open(first, 'wb') as handle:
                handle.write(b'VOB-ONE\x00' * 1000)
            with open(second, 'wb') as handle:
                handle.write(b'VOB-TWO\xff' * 500)
            result = media.run_bitwise_join([first, second], output)
            with open(first, 'rb') as a, open(second, 'rb') as b, open(output, 'rb') as out:
                self.assertEqual(out.read(), a.read() + b.read())
            self.assertEqual(result['bytes'], os.path.getsize(output))
            self.assertFalse([name for name in os.listdir(temp) if '__vs_partial__' in name])

    def test_cancel_leaves_no_output(self):
        with tempfile.TemporaryDirectory() as temp:
            source = os.path.join(temp, 'a.vob')
            output = os.path.join(temp, 'out.vob')
            with open(source, 'wb') as handle:
                handle.write(b'x' * 100)
            with self.assertRaises(media.MediaCancelled):
                media.run_bitwise_join([source, source], output,
                                           cancel_check=lambda: True)
            self.assertFalse(os.path.exists(output))
            self.assertFalse([name for name in os.listdir(temp) if '__vs_partial__' in name])

    def test_output_must_not_overwrite_input(self):
        with tempfile.TemporaryDirectory() as temp:
            source = os.path.join(temp, 'a.vob')
            with open(source, 'wb') as handle:
                handle.write(b'x')
            with self.assertRaises(media.MediaError):
                media.run_bitwise_join([source, source], source)

    def test_missing_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(media.MediaError):
                media.run_bitwise_join(
                    [os.path.join(temp, 'missing.mp4'), os.path.join(temp, 'missing2.mp4')],
                    os.path.join(temp, 'out.mp4'))


class CommandBuilderTests(unittest.TestCase):
    def test_concat_uses_unique_list_file(self):
        if not media.find_ffmpeg():
            self.skipTest('ffmpeg not installed')
        with tempfile.TemporaryDirectory() as temp:
            inputs = []
            for index in range(2):
                path = os.path.join(temp, f'clip{index}.mp4')
                with open(path, 'wb') as handle:
                    handle.write(b'\x00' * 16)
                inputs.append(path)
            runtime = os.path.join(temp, 'runtime')
            output = os.path.join(temp, 'joined.mp4')
            cmd, resolved, placeholder, list_path = media.build_concat_command(
                inputs, output, runtime)
            try:
                self.assertEqual(cmd[0], media.find_ffmpeg())
                self.assertIn('-f', cmd)
                self.assertEqual(cmd[cmd.index('-f') + 1], 'concat')
                self.assertTrue(os.path.exists(list_path))
                with open(list_path, 'r', encoding='utf-8') as handle:
                    lines = handle.read().splitlines()
                self.assertEqual(len(lines), 2)
                self.assertTrue(lines[0].startswith("file '"))
                self.assertIn('__vs_partial__', placeholder)
                self.assertNotEqual(resolved, placeholder)
            finally:
                os.remove(list_path)

    def test_cut_command_uses_copy_and_partial_output(self):
        if not media.find_ffmpeg():
            self.skipTest('ffmpeg not installed')
        with tempfile.TemporaryDirectory() as temp:
            source = os.path.join(temp, 'in.mp4')
            output = os.path.join(temp, 'out.mp4')
            with open(source, 'wb') as handle:
                handle.write(b'\x00' * 16)
            cmd, resolved, placeholder = media.build_cut_command(
                source, output, '00:00:05', '00:00:20')
            self.assertEqual(cmd[cmd.index('-ss') + 1], '00:00:05')
            self.assertEqual(cmd[cmd.index('-t') + 1], '15.000')
            self.assertEqual(cmd[cmd.index('-c') + 1], 'copy')
            self.assertIn('__vs_partial__', placeholder)
            self.assertEqual(resolved, os.path.abspath(output))

    def test_cut_refuses_to_overwrite_input(self):
        if not media.find_ffmpeg():
            self.skipTest('ffmpeg not installed')
        with tempfile.TemporaryDirectory() as temp:
            source = os.path.join(temp, 'in.mp4')
            with open(source, 'wb') as handle:
                handle.write(b'\x00')
            with self.assertRaises(media.MediaError):
                media.build_cut_command(source, source, '0', '10')


if __name__ == '__main__':
    unittest.main()
