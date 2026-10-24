"""The per-launch API token file: written privately at startup, removed at shutdown.

Kept out of vs_app/core, which holds only pure rules with no I/O.
"""

import os
import tempfile


def write_private_file(path, text):
    """Atomically write a small private file with owner-only permissions where supported."""
    parent = os.path.dirname(path) or '.'
    os.makedirs(parent, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix='.token-', dir=parent, text=True)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(text)
        try:
            os.chmod(temp_path, 0o600)
        except OSError:
            pass
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def read_token(path):
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            return handle.read().strip()
    except OSError:
        return ''


def remove_file(path):
    try:
        os.remove(path)
    except OSError:
        pass
