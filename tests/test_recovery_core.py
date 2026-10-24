import unittest

from vs_app.core import recovery as core


class DevicePolicyTests(unittest.TestCase):
    def test_device_regex_accepts_real_nodes(self):
        for device in ('/dev/sda', '/dev/sdb1', '/dev/sr0', '/dev/cdrom',
                       '/dev/mmcblk0', '/dev/mmcblk0p1', '/dev/nvme0n1', '/dev/nvme0n1p2'):
            self.assertTrue(core.DEVICE_RE.match(device), device)

    def test_device_regex_rejects_traversal_and_shell_text(self):
        for device in ('/dev/../etc/passwd', '/dev/sda; rm -rf /', 'sda', '/dev/sd', '',
                       '/dev/sda1&&whoami'):
            self.assertFalse(core.DEVICE_RE.match(device), device)

    def test_optical_detection(self):
        self.assertTrue(core.is_optical_device('/dev/sr0'))
        self.assertTrue(core.is_optical_device('/dev/cdrom'))
        self.assertFalse(core.is_optical_device('/dev/sdb'))

    def test_device_must_be_enumerated(self):
        drives = [{'device': '/dev/sdb'}]
        self.assertTrue(core.device_is_allowed('/dev/sdb', drives))
        self.assertFalse(core.device_is_allowed('/dev/sdc', drives))


class LsblkParsingTests(unittest.TestCase):
    SAMPLE = (
        'NAME="sda" TYPE="disk" SIZE="931.5G" TRAN="sata" RM="0" MODEL="SSD" LOG-SEC="512"\n'
        'NAME="sda1" TYPE="part" SIZE="931G" TRAN="" RM="0" MODEL="" LOG-SEC="512"\n'
        'NAME="sdb" TYPE="disk" SIZE="29.7G" TRAN="usb" RM="1" MODEL="Flash Disk" LOG-SEC="512"\n'
        'NAME="sr0" TYPE="rom" SIZE="4.4G" TRAN="sata" RM="1" MODEL="DVD-RW" LOG-SEC="2048"\n'
    )

    def test_only_full_optical_and_usb_disks_are_recovery_targets(self):
        drives = core.parse_lsblk_drives(self.SAMPLE)
        self.assertEqual([d['device'] for d in drives], ['/dev/sdb', '/dev/sr0'])
        self.assertEqual(drives[0]['sector_size'], 512)
        self.assertEqual(drives[1]['sector_size'], 2048)
        self.assertEqual(drives[0]['type'], 'usb')
        self.assertEqual(drives[1]['type'], 'optical')

    def test_quoted_values_with_equals_signs_survive(self):
        pairs = core.parse_lsblk_pairs('NAME="sdb" MODEL="A=B USB" TYPE="disk"\n')
        self.assertEqual(pairs[0]['MODEL'], 'A=B USB')


class PathPolicyTests(unittest.TestCase):
    def test_windows_to_wsl(self):
        self.assertEqual(core.win_to_wsl_path(r'C:\Recovery\run\disk.img'),
                         '/mnt/c/Recovery/run/disk.img')
        self.assertEqual(core.win_to_wsl_path('/mnt/d/Recovery/x.img'),
                         '/mnt/d/Recovery/x.img')

    def test_wsl_to_windows(self):
        self.assertEqual(core.wsl_to_win_path('/mnt/c/Recovery/run/disk.img'),
                         r'C:\Recovery\run\disk.img')

    def test_windows_root_containment(self):
        roots = (r'C:\PendriveRecovery',)
        self.assertTrue(core.is_within_roots(r'C:\PendriveRecovery\2026\disk.img', roots))
        self.assertTrue(core.is_within_roots(r'c:\pendriverecovery\2026\disk.img', roots))
        self.assertFalse(core.is_within_roots(r'C:\Windows\disk.img', roots))
        self.assertFalse(core.is_within_roots(r'C:\PendriveRecoveryEvil\disk.img', roots))

    def test_wsl_root_containment(self):
        roots = ('/mnt/c/PendriveRecovery',)
        self.assertTrue(core.is_within_roots('/mnt/c/PendriveRecovery/run/disk.img', roots))
        self.assertFalse(core.is_within_roots('/mnt/c/PendriveRecovery/../Windows/x', roots))
        self.assertFalse(core.is_within_roots('/mnt/d/Recovery/x.img', roots))

    def test_styles_do_not_mix(self):
        self.assertFalse(core.is_within_roots(r'C:\PendriveRecovery\x.img',
                                              ('/mnt/c/PendriveRecovery',)))


class CommandBuilderTests(unittest.TestCase):
    def test_ddrescue_usb_normal(self):
        argv = core.build_ddrescue_argv('/dev/sdb', '/mnt/c/r/disk.img', 'normal', 512)
        self.assertEqual(argv, ['ddrescue', '-b', '512', '-r', '3', '-v',
                                '/dev/sdb', '/mnt/c/r/disk.img', '/mnt/c/r/disk.img.log'])

    def test_ddrescue_optical_forces_2048(self):
        argv = core.build_ddrescue_argv('/dev/sr0', '/mnt/c/r/cd.iso', 'normal', 512)
        self.assertEqual(argv[:5], ['ddrescue', '-b', '2048', '-n', '-v'])

    def test_ddrescue_aggressive_flags(self):
        argv = core.build_ddrescue_argv('/dev/sdb', '/mnt/c/r/disk.img', 'aggressive', 4096)
        self.assertEqual(argv[1:7], ['-b', '4096', '-d', '-r', '5', '-v'])
        argv = core.build_ddrescue_argv('/dev/sr0', '/mnt/c/r/cd.iso', 'aggressive')
        self.assertIn('-R', argv)

    def test_ddrescue_rejects_unknown_sector_size(self):
        argv = core.build_ddrescue_argv('/dev/sdb', '/mnt/c/r/disk.img', 'normal', 'bad')
        self.assertEqual(argv[2], '512')

    def test_photorec_all_files(self):
        argv = core.build_photorec_argv('/mnt/c/r/disk.img', '/mnt/c/r/carved')
        self.assertEqual(argv[:7], ['photorec', '/log', '/logname',
                                    '/mnt/c/r/carved/photorec.log', '/d', '/mnt/c/r/carved', '/cmd'])
        self.assertEqual(argv[-2], '/mnt/c/r/disk.img')
        self.assertIn('wholespace', argv[-1])
        self.assertIn('fileopt,everything,enable', argv[-1])

    def test_photorec_selected_types_disable_others(self):
        argv = core.build_photorec_argv('/x.img', '/carved_videos', ['videos'])
        script = argv[-1]
        self.assertIn('everything,disable', script)
        self.assertIn('mov,enable', script)
        self.assertIn('blocksize,512', script)
        self.assertNotIn('pdf,enable', script)


class CarveCategoryTests(unittest.TestCase):
    def test_empty_and_all_mean_everything(self):
        self.assertEqual(core.normalize_file_types(None), (None, None))
        self.assertEqual(core.normalize_file_types(''), (None, None))
        self.assertEqual(core.normalize_file_types(['all', 'videos']), (None, None))

    def test_categories_are_deduplicated_in_order(self):
        cats, error = core.normalize_file_types(['videos', 'images', 'videos'])
        self.assertIsNone(error)
        self.assertEqual(cats, ['videos', 'images'])

    def test_unknown_category_is_rejected(self):
        cats, error = core.normalize_file_types(['videos', 'bogus'])
        self.assertIsNone(cats)
        self.assertIn('bogus', error)

    def test_default_recup_dir(self):
        self.assertEqual(core.default_recup_dir('/mnt/c/r/disk.img', None),
                         '/mnt/c/r/carved')
        self.assertEqual(core.default_recup_dir('/mnt/c/r/disk.img', ['videos', 'pdf']),
                         '/mnt/c/r/carved_videos-pdf')


class JoinStrategyTests(unittest.TestCase):
    def test_vob_inputs_use_bitwise_copy(self):
        self.assertIn('vob', core.VIDEO_EXTS)
        strategy, error = core.join_strategy(['a.VOB', 'b.vob'])
        self.assertIsNone(error)
        self.assertEqual(strategy, 'bitwise')

    def test_mixed_inputs_use_concat(self):
        self.assertEqual(core.join_strategy(['a.mp4', 'b.mp4'])[0], 'concat')
        self.assertEqual(core.join_strategy(['a.vob', 'b.mp4'], 'vob_copy')[0], None)

    def test_fewer_than_two_files_is_rejected(self):
        self.assertIsNone(core.join_strategy(['a.mp4'])[0])


class ValueNormalizationTests(unittest.TestCase):
    def test_timecodes(self):
        self.assertEqual(core.normalize_timecode('90'), '90.000')
        self.assertEqual(core.normalize_timecode('01:02:03'), '01:02:03')
        self.assertEqual(core.normalize_timecode('1:02'), '00:01:02')
        self.assertIsNone(core.normalize_timecode('01:99'))
        self.assertIsNone(core.normalize_timecode('-5'))
        self.assertEqual(core.normalize_timecode(''), '00:00:00')

    def test_quality_codec_preset_clamp_to_known_values(self):
        self.assertEqual(core.normalize_quality('99'), 51)
        self.assertEqual(core.normalize_quality('-1'), 0)
        self.assertEqual(core.normalize_quality('x'), 18)
        self.assertEqual(core.normalize_codec('bogus'), 'nvenc_h265_10bit')
        self.assertEqual(core.normalize_preset('bogus'), 'slow')


if __name__ == '__main__':
    unittest.main()
