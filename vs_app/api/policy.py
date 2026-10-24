"""Security policy: device allowlist, output sandbox, input validation."""

import os

from vs_app import settings
from vs_app.core import recovery as core
from vs_app.execution import bridge, media


class PolicyError(RuntimeError):
    pass


def format_roots(roots):
    return '; '.join(roots)


def within(path, roots):
    if not path:
        return False
    local = media.local_path(str(path))
    candidates = {str(path), local}
    if not any(core.is_within_roots(candidate, roots)
               for candidate in candidates if candidate):
        return False
    # The lexical check above can be escaped through a junction or symlink inside a
    # root, so the resolved path must also land inside a resolved root.
    resolved = os.path.realpath(local)
    if resolved == os.path.abspath(local):
        return True
    resolved_roots = [os.path.realpath(media.local_path(root)) for root in roots if root]
    return core.is_within_roots(resolved, resolved_roots)


def require_within(path, roots, label):
    if not path:
        raise PolicyError(f'{label} path is required.')
    if not within(path, roots):
        raise PolicyError(
            f'{label} must be inside a configured root: {format_roots(roots)}. '
            'Set RECOVERY_ROOTS / MEDIA_ROOTS in .env to allow another location.')


def is_a_root(path, roots):
    """True when path is one of the roots itself (lexically or after resolving links)."""
    local = media.local_path(str(path))
    for root in roots:
        if not root:
            continue
        for candidate in {str(path), local}:
            if (core._is_posix_style(candidate) == core._is_posix_style(root)
                    and core.normalized_path(candidate) == core.normalized_path(root)):
                return True
        resolved_root = os.path.realpath(media.local_path(root))
        if os.path.normcase(os.path.realpath(local)) == os.path.normcase(resolved_root):
            return True
    return False


def require_inside(path, roots, label):
    """Like require_within, but the root folder itself is refused.

    Outputs derive sibling paths (image.log, testdisk.log, carved.1, carved_other), so
    an output equal to a root would put those siblings outside every root.
    """
    require_within(path, roots, label)
    if is_a_root(path, roots):
        raise PolicyError(
            f'{label} must be a file or folder inside a configured root, '
            f'not the root folder itself: {path}')


def require_recovery_output(path, label='Output image'):
    require_inside(path, settings.SETTINGS.recovery_roots, label)


def require_media_output(path, label='Output'):
    """A cut/join/transcode output: a video file that never replaces recovered data."""
    require_inside(path, settings.SETTINGS.media_roots, label)
    local = media.local_path(str(path).strip())
    extension = os.path.splitext(local)[1].lstrip('.').lower()
    if extension not in core.VIDEO_EXTS:
        raise PolicyError(
            f'{label} must be a video file ({", ".join(sorted(core.VIDEO_EXTS))}).')
    if os.path.exists(local) and within(local, settings.SETTINGS.recovery_roots):
        raise PolicyError(
            f'{local} already exists inside a recovery folder, where recovered data lives. '
            'Choose a new file name.')


def media_input(path, roots=None):
    path = media.local_path(str(path or '').strip())
    if not path:
        raise PolicyError('Input path is required.')
    require_within(path, settings.SETTINGS.media_roots if roots is None else roots, 'Input')
    extension = os.path.splitext(path)[1].lstrip('.').lower()
    if extension not in core.VIDEO_EXTS:
        raise PolicyError(
            f'Input must be a video file ({", ".join(sorted(core.VIDEO_EXTS))}).')
    if not os.path.isfile(path):
        raise PolicyError(f'Input file not found: {path}')
    return path


def validate_device(device, drives=None):
    device = str(device or '').strip()
    if not core.DEVICE_RE.match(device):
        raise PolicyError(
            f"Invalid device '{device}'. Expected a detected optical/USB device "
            'such as /dev/sr0 or /dev/sdb.')
    drives = drives if drives is not None else bridge.scan_recovery_drives()
    if not core.device_is_allowed(device, drives):
        found = ', '.join(item['device'] for item in drives) or 'none'
        raise PolicyError(
            f'{device} is not a currently detected optical/USB device. '
            f'Detected: {found}. Click Detect Drives and retry.')
    return device


def carve_categories(raw):
    categories, error = core.normalize_file_types(raw)
    if error:
        raise PolicyError(error)
    return categories


def carve_folder_name(raw, default='carved'):
    base = str(raw or default).strip()
    if not bridge.SAFE_NAME_RE.match(base) or '..' in base:
        raise PolicyError('Carve folder name is invalid.')
    return base
