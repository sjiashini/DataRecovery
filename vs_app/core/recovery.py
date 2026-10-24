"""Pure recovery policy shared by the web backend and the WSL desktop GUI.

This module deliberately has no side effects and only uses path libraries that
work on both Windows and Linux, so every rule here can be unit-tested.
"""

import hashlib
import ntpath
import posixpath
import re
import shlex

DEVICE_RE = re.compile(
    r'^/dev/(?:sd[a-z]+\d*|sr\d+|cdrom|mmcblk\d+(?:p\d+)?|nvme\d+n\d+(?:p\d+)?)$'
)
OPTICAL_RE = re.compile(r'^/dev/(?:sr\d+|cdrom)$')

# One tool at a time per image: ddrescue writes the image and its mapfile, and
# TestDisk/PhotoRec must not read an image another process is still writing.
IMAGE_LOCK_DIR = '/tmp/video-studio-locks'
IMAGE_BUSY_EXIT = 75  # EX_TEMPFAIL
IMAGE_BUSY_MESSAGE = 'video-studio: image is in use by another recovery process'

# PhotoRec scripted run: keep corrupted files, paranoid mode, every file format
# enabled, and scan the whole space (not just free space) - required to carve
# data from a formatted drive whose filesystem metadata was reset.
PHOTOREC_SCRIPT = (
    'options,keep_corrupted_file,paranoid,fileopt,everything,enable,wholespace,search'
)

# PhotoRec 7.1 family names (each verified against the installed binary — an
# unknown name makes PhotoRec silently carve nothing). Several families emit
# more than one extension: tif -> dng/nef/cr2/arw, mov -> mp4/3gp/m4v,
# riff -> avi/wav, asf -> wmv, doc (OLE) -> doc/xls/ppt, zip -> docx/xlsx/pptx.
CARVE_CATEGORIES = {
    'images': {
        'label': 'Images',
        'types': ['jpg', 'png', 'gif', 'bmp', 'tif', 'psd', 'ico', 'pcx', 'xcf',
                  'emf', 'wmf', 'crw', 'orf', 'raf', 'rw2', 'mrw', 'x3f', 'raw'],
    },
    'videos': {
        'label': 'Videos',
        'types': ['mov', 'riff', 'mkv', 'mpg', 'asf', 'flv', 'm2ts', 'wtv', 'dv'],
    },
    'pdf': {'label': 'PDF', 'types': ['pdf']},
    'word': {
        'label': 'Word', 'types': ['doc', 'zip'],
        'keep': {'doc', 'docx', 'docm', 'dot', 'dotx', 'dotm'},
    },
    'excel': {
        'label': 'Excel', 'types': ['doc', 'zip'],
        'keep': {'xls', 'xlsx', 'xlsm', 'xlt', 'xltx', 'xltm'},
    },
    'powerpoint': {
        'label': 'PowerPoint', 'types': ['doc', 'zip'],
        'keep': {'ppt', 'pptx', 'pptm', 'pps', 'ppsx', 'pot', 'potx'},
    },
}

# Extensions the shared OLE/zip containers can produce; unselected ones are set aside.
OFFICE_CONTAINER_EXTS = {
    'doc', 'docx', 'docm', 'dot', 'dotx', 'dotm', 'xls', 'xlsx', 'xlsm', 'xlt', 'xltx', 'xltm',
    'ppt', 'pptx', 'pptm', 'pps', 'ppsx', 'pot', 'potx', 'msi', 'msg', 'pub', 'vsd', 'mpp', 'wps',
    'zip', 'jar', 'apk', 'xpi', 'kmz', 'epub', 'odt', 'ods', 'odp', 'odg', 'sxw', 'sxc', 'sxi',
}

VIDEO_EXTS = {
    'mp4', 'mov', 'm4v', '3gp', 'avi', 'mkv', 'mpg', 'mpeg', 'vob',
    'wmv', 'asf', 'flv', 'mts', 'm2ts', 'ts', 'webm', 'dv',
}

HANDBRAKE_CODECS = {
    'nvenc_h265_10bit', 'nvenc_h265', 'nvenc_h264', 'x264', 'x265',
}
HANDBRAKE_PRESETS = {
    'ultrafast', 'superfast', 'veryfast', 'faster', 'fast',
    'medium', 'slow', 'slower', 'veryslow',
}

TIMECODE_RE = re.compile(r'^(?:(?P<h>\d{1,2}):)?(?P<m>\d{1,2}):(?P<s>\d{2}(?:\.\d+)?)$')
SECONDS_RE = re.compile(r'^\d+(?:\.\d+)?$')


def is_optical_device(device):
    return bool(OPTICAL_RE.match(device or ''))


def win_to_wsl_path(path):
    """C:\\Recovery\\x.img -> /mnt/c/Recovery/x.img ; /mnt/... passes through."""
    if not path:
        return path
    # Strip quotes exactly like the path checks do, so the checked and the used path agree.
    path = str(path).strip().strip('"')
    if path.startswith('/'):
        return path
    absolute = ntpath.abspath(path)
    drive, tail = ntpath.splitdrive(absolute)
    if not drive:
        return absolute.replace('\\', '/')
    tail = tail.replace('\\', '/')  # outside the f-string: Python < 3.12 forbids it there
    return f"/mnt/{drive.rstrip(':').lower()}{tail}"


def wsl_to_win_path(path):
    """/mnt/c/Recovery/x -> C:\\Recovery\\x ; Windows paths pass through."""
    if not path:
        return path
    match = re.match(r'^/mnt/([a-zA-Z])/(.*)$', str(path))
    if match:
        return f"{match.group(1).upper()}:\\{match.group(2).replace('/', chr(92))}"
    return path


def normalize_file_types(raw):
    """Return (categories or None for 'everything', error)."""
    if not raw:
        return None, None
    if isinstance(raw, str):
        raw = [raw]
    cats = []
    for item in raw:
        key = str(item).strip().lower()
        if key == 'all':
            return None, None
        if key not in CARVE_CATEGORIES:
            return None, f"Unknown file type '{item}'. Choose from: all, {', '.join(CARVE_CATEGORIES)}."
        if key not in cats:
            cats.append(key)
    return (cats or None), None


def photorec_script(categories):
    if not categories:
        return PHOTOREC_SCRIPT
    types = []
    for cat in categories:
        for item in CARVE_CATEGORIES[cat]['types']:
            if item not in types:
                types.append(item)
    enabled = ','.join(f'{item},enable' for item in types)
    # With few enabled types PhotoRec mis-guesses the cluster size and skips headers.
    return (
        f'options,keep_corrupted_file,paranoid,fileopt,everything,disable,{enabled},'
        'blocksize,512,wholespace,search'
    )


def default_recup_dir(image_wsl, categories, carve_dir='carved'):
    base = (posixpath.dirname(image_wsl) or '/mnt/c/PendriveRecovery') + '/' + carve_dir
    return base + '_' + '-'.join(categories) if categories else base


def build_ddrescue_argv(device, output_wsl, mode='normal', sector_size=None):
    """Build a ddrescue argv list. No shell parsing is ever involved."""
    optical = is_optical_device(device)
    sector = None
    if sector_size:
        try:
            sector = int(sector_size)
        except (TypeError, ValueError):
            sector = None
    if sector not in (512, 2048, 4096):
        sector = 2048 if optical else 512

    if optical:
        flags = ['-b', '2048', '-d', '-r', '5', '-R', '-v'] if mode == 'aggressive' else ['-b', '2048', '-n', '-v']
    else:
        flags = ['-b', str(sector), '-d', '-r', '5', '-v'] if mode == 'aggressive' else ['-b', str(sector), '-r', '3', '-v']
    return ['ddrescue'] + flags + [device, output_wsl, output_wsl + '.log']


def image_lock_path(image_wsl):
    """Lock file for one image, on WSL's own filesystem (flock is unreliable on /mnt/c).

    Keyed by the normalized path, so /mnt/c/X/a.img and /mnt/c/x/A.IMG share a lock.
    """
    digest = hashlib.sha1(normalized_path(image_wsl).encode('utf-8')).hexdigest()
    return f'{IMAGE_LOCK_DIR}/{digest}.lock'


def image_lock_prelude(image_wsl):
    """Shell lines that take the image's lock on fd 9 or exit with IMAGE_BUSY_EXIT.

    The lock belongs to the open file, so a following `exec` hands it to the tool:
    it is held exactly as long as the tool runs, and the kernel drops it if the
    tool dies, so there is never a stale lock to clean up.
    """
    lock = image_lock_path(image_wsl)
    return (f'mkdir -p {shlex.quote(IMAGE_LOCK_DIR)}; '
            f'exec 9>>{shlex.quote(lock)}; '
            f'flock -n 9 || {{ echo {shlex.quote(IMAGE_BUSY_MESSAGE)}; exit {IMAGE_BUSY_EXIT}; }}')


def image_busy_error(image_wsl):
    return (f'{image_wsl} is in use by another recovery process (another Video Studio '
            'backend, the desktop GUI, or a TestDisk window). Wait for it to finish '
            'or close it, then retry.')


def locked_argv(argv, image_wsl):
    """Run argv (inside WSL/Linux) only while holding the image's lock."""
    script = image_lock_prelude(image_wsl) + '; exec "$@"'
    return ['bash', '-c', script, 'video-studio-lock'] + [str(item) for item in argv]


IDENTITY_FIELDS = ('serial', 'uuid', 'label')


def mapfile_extent(text):
    """Size of the source a ddrescue mapfile describes (end of its last block), or None.

    ddrescue maps the whole input on its first pass, so this is the source's size.
    """
    extent = None
    for line in (text or '').splitlines():
        parts = line.split()
        if len(parts) != 3 or line.lstrip().startswith('#'):
            continue
        try:
            pos, size = int(parts[0], 0), int(parts[1], 0)
        except ValueError:
            continue  # the status line ("pos status pass") has no number in its middle field
        extent = max(extent or 0, pos + size)
    return extent


def source_mismatch(saved, current, mapfile_size=None):
    """Why an existing image must not be resumed from `current`, or None if it may be.

    `saved` is the identity recorded when the image was started (may be empty for
    images made before records existed); `current` is the device plugged in now.
    Resuming from a different source would write its sectors into the first source's
    unrescued areas, corrupting the image for good.
    """
    saved = saved or {}
    size = current.get('size')
    if size and mapfile_size and mapfile_size != size:
        return (f'its mapfile describes a {mapfile_size}-byte source, '
                f'but the device now plugged in has {size} bytes')
    if size and saved.get('size') and saved['size'] != size:
        return (f'it was made from a {saved["size"]}-byte source, '
                f'but the device now plugged in has {size} bytes')
    for field in IDENTITY_FIELDS:
        before, now = saved.get(field), current.get(field)
        if before and now and before != now:
            return f'it was made from a source with {field} {before!r}, not {now!r}'
    return None


SPACE_MARGIN = 512 * 1024 * 1024  # room for the mapfile, logs and filesystem overhead


def format_bytes(count):
    return f'{count / 1024 ** 3:.1f} GB'


def space_problem(source_size, image_bytes, free_bytes, margin=SPACE_MARGIN):
    """Why the destination can't hold the rest of the image, or None.

    ddrescue grows the image to the source's full size, so imaging that runs out of
    space fails hours in. `image_bytes` is what a resumed image already has on disk.
    Unknown sizes skip the check rather than block a recovery.
    """
    if not source_size or free_bytes is None:
        return None
    needed = max(source_size - (image_bytes or 0), 0) + margin
    if free_bytes >= needed:
        return None
    return (f'Not enough free space for the image: it needs about {format_bytes(needed)} more, '
            f'but the destination drive has {format_bytes(free_bytes)} free. Choose a folder '
            'on a drive with more space, or free some up, then retry.')


def source_mismatch_error(image_wsl, reason):
    return (f'{image_wsl} already holds an image from a different disc or drive: {reason}. '
            'Choose a new output file, or put the original disc or drive back to resume it.')


def build_photorec_argv(image_wsl, recup_wsl, categories=None):
    return [
        'photorec', '/log', '/logname', f'{recup_wsl}/photorec.log',
        '/d', recup_wsl, '/cmd', image_wsl, photorec_script(categories),
    ]


def parse_lsblk_pairs(output):
    """Parse `lsblk -P` key="value" output into a list of dicts."""
    devices = []
    for line in output.splitlines():
        props = dict(re.findall(r'([A-Z0-9\-]+)="([^"]*)"', line))
        if props.get('NAME'):
            devices.append(props)
    return devices


def parse_lsblk_drives(output):
    """Return recovery-capable optical/USB devices described by `lsblk -P`."""
    drives = []
    for props in parse_lsblk_pairs(output):
        name = props.get('NAME', '')
        dtype = props.get('TYPE', '')
        optical = dtype == 'rom' or name.startswith('sr')
        usb = dtype == 'disk' and (
            props.get('TRAN', '').lower() == 'usb' or props.get('RM', '') == '1'
        )
        if not (optical or usb):
            continue
        if optical:
            sector = 2048
        else:
            try:
                sector = int(props.get('LOG-SEC') or 512)
            except ValueError:
                sector = 512
        model = props.get('MODEL', '').strip() or name
        drives.append({
            'device': f'/dev/{name}',
            'type': 'optical' if optical else 'usb',
            'size': props.get('SIZE', ''),
            'model': model,
            'sector_size': sector,
            'removable': props.get('RM', '') == '1',
            'label': f"/dev/{name} — {'Optical' if optical else 'USB'} {model} {props.get('SIZE', '')}".strip(),
        })
    return drives


def device_is_allowed(device, drives):
    """Only devices currently enumerated as optical/USB storage may be opened."""
    return any(device == d.get('device') for d in drives)


def _is_posix_style(path):
    return bool(path) and path.startswith('/') and not re.match(r'^[A-Za-z]:[\\/]', path)


def normalized_path(path):
    """Normalize a Windows or WSL path without assuming the host OS."""
    if not path:
        return ''
    path = str(path).strip().strip('"')
    if _is_posix_style(path):
        return posixpath.normpath(path).casefold()
    return ntpath.normcase(ntpath.normpath(ntpath.abspath(path)))


def is_within_roots(path, roots):
    """True when path is equal to or below one of the supplied roots."""
    if not path:
        return False
    style_posix = _is_posix_style(path)
    candidate = normalized_path(path)
    for root in roots or ():
        if not root or _is_posix_style(root) != style_posix:
            continue
        normalized_root = normalized_path(root)
        if candidate == normalized_root:
            return True
        if style_posix:
            if candidate.startswith(normalized_root.rstrip('/') + '/'):
                return True
        else:
            try:
                if ntpath.commonpath([candidate, normalized_root]) == normalized_root:
                    return True
            except ValueError:
                continue
    return False


def normalize_timecode(value, default='00:00:00'):
    """Validate an ffmpeg time value and return a canonical HH:MM:SS[.mmm] string."""
    if value is None or str(value).strip() == '':
        return default
    text = str(value).strip()
    if SECONDS_RE.match(text):
        seconds = float(text)
        if seconds < 0 or seconds > 359999:
            return None
        return f'{seconds:.3f}'
    match = TIMECODE_RE.match(text)
    if not match:
        return None
    hours = int(match.group('h') or 0)
    minutes = int(match.group('m'))
    seconds = float(match.group('s'))
    if minutes > 59 or seconds >= 60 or hours > 99:
        return None
    suffix = f'{seconds:06.3f}' if seconds != int(seconds) else f'{int(seconds):02d}'
    return f'{hours:02d}:{minutes:02d}:{suffix}'


def normalize_quality(value, default='18'):
    try:
        quality = int(str(value).strip())
    except (TypeError, ValueError):
        quality = int(default)
    return max(0, min(51, quality))


def normalize_codec(value, default='nvenc_h265_10bit'):
    return value if value in HANDBRAKE_CODECS else default


def normalize_preset(value, default='slow'):
    return value if value in HANDBRAKE_PRESETS else default


def join_strategy(files, requested_mode=None):
    """Return ('bitwise'|'concat', error). Bitwise join is exact for VOB chunks."""
    if not isinstance(files, list) or len(files) < 2:
        return None, 'At least two files are required for joining.'
    mode = (requested_mode or '').strip().lower()
    all_vob = all(str(f).lower().endswith('.vob') for f in files)
    if mode in ('vob_copy', 'bitwise', 'copy'):
        if not all_vob:
            return None, 'VOB copy mode requires every input file to end with .vob.'
        return 'bitwise', None
    if mode in ('ffmpeg', 'concat', ''):
        return ('bitwise' if all_vob else 'concat'), None
    if all_vob:
        return 'bitwise', None
    return 'concat', None
