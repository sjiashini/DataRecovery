"""Shared runtime settings, logging, and the per-launch API token."""

import os

from vs_app.config import get_settings, setup_logging
from vs_app.core import security

APP_VERSION = '3.0.0'

SETTINGS = get_settings()
LOGGER = setup_logging(SETTINGS)

_state = {
    'distro': SETTINGS.wsl_distro,
    'token': security.generate_token(),
}


def distro():
    return _state['distro']


def set_distro(name):
    value = str(name or '').strip()
    if value:
        _state['distro'] = value
    return _state['distro']


def token():
    return _state['token']


def jobs_db_path():
    override = os.environ.get('VS_JOBS_DB')
    if override:
        return os.path.abspath(override)
    return os.path.join(SETTINGS.runtime_dir, 'jobs.db')
