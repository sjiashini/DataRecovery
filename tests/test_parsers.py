import unittest

from vs_app.execution.parsers import (
    DdrescueParser,
    PhotorecParser,
    TermScreen,
    fmt_duration,
    hms_to_text,
)


class DdrescueParserTests(unittest.TestCase):
    def test_pct_errors_and_eta(self):
        updates = DdrescueParser().feed_line(
            '   pct rescued: 42.50%, read errors: 7, remaining time: 1m 20s')
        self.assertEqual(updates['pct'], 42.5)
        self.assertEqual(updates['errors'], 7)
        self.assertEqual(updates['eta'], '1m 20s')

    def test_zero_eta_is_reported_as_none(self):
        updates = DdrescueParser().feed_line(
            'pct rescued: 100.00%, read errors: 0, remaining time: 0s')
        self.assertEqual(updates['pct'], 100.0)
        self.assertIsNone(updates['eta'])

    def test_rescued_bad_areas_and_rate(self):
        parser = DdrescueParser()
        self.assertEqual(parser.feed_line('rescued: 1234 MB, bad areas: 2')['bad_areas'], 2)
        self.assertEqual(parser.feed_line('average rate: 45.5 MB/s')['rate'], '45.5 MB/s')
        self.assertEqual(parser.feed_line('rescued: 1234 MB, bad areas: 2')['rescued'], '1234 MB')

    def test_status_table_lines_are_quiet(self):
        parser = DdrescueParser()
        self.assertTrue(parser.quiet_line('     ipos:  1234 MB, non-tried: 0 B'))
        self.assertTrue(parser.quiet_line(''))
        self.assertFalse(parser.quiet_line('ddrescue: cannot open input file'))


class PhotorecParserTests(unittest.TestCase):
    def test_pass_line_updates_sector_and_percent(self):
        parser = PhotorecParser(image_size=512 * 1000)
        updates = parser.feed_bytes(b'Pass 1 - Reading sector 500/5000, 3 files found\n')
        self.assertEqual(updates['sector'], 500)
        self.assertEqual(updates['total_sectors'], 1000)
        self.assertEqual(updates['pct'], 50.0)
        self.assertEqual(updates['phase'], 'Pass 1 — carving')
        self.assertIn('sector 500 / 1,000', updates['detail'])

    def test_pass_zero_is_labelled_block_size_detection(self):
        parser = PhotorecParser()
        updates = parser.feed_bytes(b'Pass 0 - Reading sector 10/100\n')
        self.assertEqual(updates['phase'], 'Pass 0 — detecting block size')

    def test_eta_line_is_converted(self):
        parser = PhotorecParser()
        updates = parser.feed_bytes(
            b'Elapsed time 1h02m03s - Estimated time to completion 0h10m00s\n')
        self.assertEqual(updates['elapsed_tool'], '1h 02m')
        self.assertEqual(updates['eta'], '10m 00s')

    def test_eta_is_recomputed_from_sector_progress(self):
        parser = PhotorecParser(image_size=512 * 1000)
        updates = parser.feed_bytes(
            b'Pass 1 - Reading sector 500/1000\r\n'
            b'Elapsed time 0h00m10s - Estimated time to completion 9h59m59s\r\n')
        self.assertEqual(updates['eta'], '0m 10s')


class FormattingTests(unittest.TestCase):
    def test_fmt_duration(self):
        self.assertEqual(fmt_duration(None), 'n/a')
        self.assertEqual(fmt_duration(45), '45s')
        self.assertEqual(fmt_duration(90), '1m 30s')

    def test_hms_to_text(self):
        self.assertEqual(hms_to_text('0h05m09s'), '5m 09s')
        self.assertEqual(hms_to_text('2h00m00s'), '2h 00m')
        self.assertEqual(hms_to_text('unknown'), 'unknown')


class TermScreenTests(unittest.TestCase):
    def test_plain_text_and_carriage_return_overwrite(self):
        screen = TermScreen()
        screen.feed(b'Progress 10%\rProgress 20%\n')
        self.assertEqual(screen.lines()[0], 'Progress 20%')

    def test_erase_line_clears_stale_content(self):
        screen = TermScreen()
        screen.feed(b'old text')
        screen.feed(b'\x1b[2K')
        self.assertEqual(screen.lines()[0], '')


if __name__ == '__main__':
    unittest.main()
