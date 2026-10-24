"""Runtime configuration, resource resolution, and rotating file logging."""

import contextvars
import logging
import ntpath
import os
import re
import sys
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler

IS_WINDOWS = os.name == 'nt'
DEFAULT_PORT = 8080


PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_HTML = 'video_studio_app.html'


def resource_dir():
    """The workspace (project root), or PyInstaller's _internal folder when frozen.

    Holds .env, .runtime and uploads in a source checkout, and is a media root.
    """
    if getattr(sys, 'frozen', False):
        return getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    return os.path.dirname(PACKAGE_DIR)


def web_dir():
    """Where the SPA lives: vs_app/web in a checkout, web/ inside the frozen bundle."""
    if getattr(sys, 'frozen', False):
        return os.path.join(resource_dir(), 'web')
    return os.path.join(PACKAGE_DIR, 'web')


def env_file_path():
    """The .env to read: the workspace copy, or the one next to the packaged exe.

    The exe never bundles .env (it holds machine-specific paths), so a frozen build
    looks beside video_studio_backend.exe rather than inside its resources.
    """
    if getattr(sys, 'frozen', False):
        return os.path.join(os.path.dirname(os.path.abspath(sys.executable)), '.env')
    return os.path.join(resource_dir(), '.env')


def runtime_dir():
    override = os.environ.get('VS_RUNTIME_DIR')
    if override:
        return os.path.abspath(override)
    if getattr(sys, 'frozen', False):
        if IS_WINDOWS:
            base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
            return os.path.join(base, 'VideoStudio')
        return os.path.expanduser('~/.local/state/video-studio')
    return os.path.join(resource_dir(), '.runtime')


def load_env_file(path):
    """Load KEY=VALUE lines without overriding variables already in the process."""
    values = {}
    try:
        with open(path, 'r', encoding='utf-8-sig') as handle:
            lines = handle.readlines()
    except OSError:
        return values
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        if line.startswith('export '):
            line = line[7:].lstrip()
        key, value = line.split('=', 1)
        key = key.strip()
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        else:
            value = re.split(r'\s+#', value, maxsplit=1)[0].strip()
        values[key] = value
    return values


def _split_roots(value):
    if not value:
        return []
    return [item.strip().strip('"') for item in re.split(r'[;\n]+', value) if item.strip()]


def _wsl_drive_variant(path):
    """C:\\Recovery -> /mnt/c/Recovery. Returns None for non-drive paths."""
    drive, tail = ntpath.splitdrive(path)
    if not drive:
        return None
    letter = drive.rstrip(':').lower()
    if len(letter) != 1:
        return None
    return f"/mnt/{letter}{tail.replace(chr(92), '/')}"


def _unique(items):
    seen, result = set(), []
    for item in items:
        if not item:
            continue
        key = item.casefold()
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _default_recovery_roots():
    roots = [
        r'C:\PendriveRecovery',
        r'C:\Recovery',
        r'D:\PendriveRecovery',
        r'D:\Recovery',
        '/mnt/c/PendriveRecovery',
        '/mnt/c/Recovery',
        '/mnt/d/PendriveRecovery',
        '/mnt/d/Recovery',
    ]
    return roots


def _user_media_roots():
    home = os.path.expanduser('~')
    names = ('Desktop', 'Downloads', 'Documents', 'Videos')
    roots = [resource_dir()]
    if IS_WINDOWS:
        for name in names:
            roots.append(os.path.join(home, name))
            variant = _wsl_drive_variant(os.path.join(home, name))
            if variant:
                roots.append(variant)
    else:
        for name in names:
            roots.append(os.path.join(home, name))
        if home.startswith('/home/'):
            # WSL: also allow the Windows profile's media folders used by the web UI.
            letter = os.environ.get('WSL_WINDOWS_USER_DRIVE', 'c')
            user = os.environ.get('WSL_WINDOWS_USER', 'admin')
            for name in names:
                roots.append(f'/mnt/{letter}/Users/{user}/{name}')
    return roots


@dataclass(frozen=True)
class Settings:
    base_dir: str
    resource_dir: str
    runtime_dir: str
    log_dir: str
    token_path: str
    app_html_path: str
    port: int
    wsl_distro: str
    recovery_roots: tuple
    media_roots: tuple
    default_output_dir: str
    photorec_carve_dir: str
    handbrake_cq: int
    handbrake_encoder: str
    log_level: str


def get_settings(env_path=None):
    base = resource_dir()
    env_file = env_path or env_file_path()
    for key, value in load_env_file(env_file).items():
        os.environ.setdefault(key, value)

    try:
        port = int(os.environ.get('PORT', DEFAULT_PORT))
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    if not (1 <= port <= 65535):
        port = DEFAULT_PORT

    default_output = os.environ.get('DEFAULT_OUTPUT_DIR', r'C:\PendriveRecovery')
    configured_recovery = _split_roots(os.environ.get('RECOVERY_ROOTS', ''))
    configured_media = _split_roots(os.environ.get('MEDIA_ROOTS', ''))

    recovery_roots = _unique(
        configured_recovery + [default_output] + _default_recovery_roots()
    )
    media_roots = _unique(
        configured_media + _user_media_roots() + recovery_roots
    )

    try:
        cq = int(os.environ.get('HANDBRAKE_CQ', '18'))
    except ValueError:
        cq = 18

    run_dir = runtime_dir()
    return Settings(
        base_dir=base,
        resource_dir=base,
        runtime_dir=run_dir,
        log_dir=os.path.join(run_dir, 'logs'),
        token_path=os.path.join(run_dir, 'api_token'),
        app_html_path=os.path.join(web_dir(), APP_HTML),
        port=port,
        wsl_distro=os.environ.get('WSL_DISTRO_NAME', 'Ubuntu'),
        recovery_roots=tuple(recovery_roots),
        media_roots=tuple(media_roots),
        default_output_dir=default_output,
        photorec_carve_dir=os.environ.get('PHOTOREC_CARVE_DIR', 'carved'),
        handbrake_cq=max(0, min(51, cq)),
        handbrake_encoder=os.environ.get('HANDBRAKE_ENCODER', 'nvenc_h265_10bit'),
        log_level=os.environ.get('LOG_LEVEL', 'INFO').upper(),
    )


# The job a log line belongs to. Each flow sets it at its start; asyncio tasks and
# asyncio.to_thread workers inherit it, so every line the job causes carries the id.
JOB_ID = contextvars.ContextVar('job_id', default='-')


def short_job_id(job_id):
    return job_id[:8]


class JobIdFilter(logging.Filter):
    def filter(self, record):
        record.job_id = JOB_ID.get()
        return True


def setup_logging(settings):
    """Configure one rotating file handler plus console output, without duplicates."""
    logger = logging.getLogger('video_studio')
    logger.setLevel(getattr(logging, settings.log_level, logging.INFO))
    if logger.handlers:
        return logger
    formatter = logging.Formatter(
        '%(asctime)s %(levelname)-8s job=%(job_id)-8s %(threadName)-14s %(name)s: %(message)s'
    )
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(JobIdFilter())
    logger.addHandler(console)
    try:
        os.makedirs(settings.log_dir, exist_ok=True)
        file_handler = RotatingFileHandler(
            os.path.join(settings.log_dir, 'video_studio.log'),
            maxBytes=5 * 1024 * 1024, backupCount=5, encoding='utf-8',
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(JobIdFilter())
        logger.addHandler(file_handler)
    except OSError as exc:
        logger.warning('File logging unavailable: %s', exc)
    logger.propagate = False
    return logger
