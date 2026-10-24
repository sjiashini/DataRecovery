#!/usr/bin/env python3
"""Turnkey: image a device fully, then auto-run a Videos-only PhotoRec carve on the
completed image, then report. Aborts if imaging is cancelled/partial/failed so we never
carve a partial image. Only ever writes to C:\\PendriveRecovery; never touches the source.

The backend is loopback-only and requires its per-launch bearer token, which it writes
to .runtime/api_token (or %LOCALAPPDATA%\\VideoStudio\\api_token for the packaged exe).

Environment overrides:
  VS_BASE_URL       default http://127.0.0.1:8080
  VS_DEVICE         default /dev/sde
  VS_RUNTIME_DIR    directory containing api_token
  VS_POLL_SECONDS   default 30
  VS_MAX_HOURS      default 10
"""
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.environ.get('VS_BASE_URL', 'http://127.0.0.1:8080').rstrip('/')
DEVICE = os.environ.get('VS_DEVICE', '/dev/sde')
MODE = os.environ.get('VS_MODE', 'normal')
POLL = int(os.environ.get('VS_POLL_SECONDS', '30'))
MAX_HOURS = float(os.environ.get('VS_MAX_HOURS', '10'))
TOKEN_PATH = os.path.join(
    os.environ.get('VS_RUNTIME_DIR') or os.path.join(PROJECT_DIR, '.runtime'),
    'api_token',
)

BAD_STATES = ('cancelled', 'partial', 'failed', 'error')


HEADERS = {}  # filled in by main() once the token has been read


def read_token():
    try:
        with open(TOKEN_PATH, 'r', encoding='utf-8') as handle:
            return handle.read().strip()
    except OSError:
        return ''


def api_get(path):
    try:
        request = urllib.request.Request(BASE + path, headers=HEADERS)
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode('utf-8', 'replace'))
    except urllib.error.HTTPError as exc:
        print(f'  [GET {path} HTTP {exc.code}]', flush=True)
    except Exception as exc:
        print(f'  [GET {path} error: {exc}]', flush=True)
    return None


def api_post(path, payload):
    request = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(), method='POST',
        headers={'Content-Type': 'application/json', **HEADERS},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode('utf-8', 'replace'))
    except urllib.error.HTTPError as exc:
        return {'success': False, 'error': f'HTTP {exc.code}: {exc.read().decode("utf-8", "replace")[:400]}'}
    except Exception as exc:
        return {'success': False, 'error': str(exc)}


def overtime(deadline):
    return time.time() > deadline


def wait_until_idle(deadline):
    while not overtime(deadline):
        job = api_get('/api/recovery_progress')
        if job is None or not job.get('active'):
            return True
        time.sleep(POLL)
    return False


def main():
    """Run the whole image-then-carve sequence; returns the process exit code."""
    token = read_token()
    if not token:
        print(f'ABORT: no API token at {TOKEN_PATH}. Start the backend first — it writes one per launch.',
              flush=True)
        return 1
    HEADERS['Authorization'] = 'Bearer ' + token

    stamp = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    image = f'/mnt/c/PendriveRecovery/{stamp}/pendrive_backup.img'
    deadline = time.time() + MAX_HOURS * 3600
    print(f'[{time.strftime("%H:%M:%S")}] TARGET IMAGE = {image}', flush=True)

    # ---- Phase 0: make sure no job is running, then start FULL imaging (carve off) ----
    if not wait_until_idle(deadline):
        print('ABORT: a job never went idle.', flush=True)
        return 5
    response = api_post('/api/recover_all',
                        {'device': DEVICE, 'output': image, 'mode': MODE, 'carve': False})
    if not (response and response.get('success')):
        print(json.dumps({'result': 'imaging_start_failed',
                          'error': (response or {}).get('error')}), flush=True)
        return 1
    print(f'[{time.strftime("%H:%M:%S")}] imaging started -> {response.get("image")}', flush=True)

    # ---- Phase A: wait for imaging to finish cleanly ----
    imaging_done = False
    while not overtime(deadline):
        job = api_get('/api/recovery_progress')
        if not job:
            time.sleep(POLL)
            continue
        imaging = (job.get('progress') or {}).get('imaging') or {}
        print(f'[{time.strftime("%H:%M:%S")}] imaging={imaging.get("state")} '
              f'pct={imaging.get("pct")} detail={imaging.get("detail")}', flush=True)
        if (imaging.get('state') in BAD_STATES or job.get('error')
                or job.get('stage') in BAD_STATES):
            print(json.dumps({'result': 'aborted',
                              'reason': job.get('error') or imaging.get('state')}), flush=True)
            return 2
        if imaging.get('state') == 'completed':
            imaging_done = True
            break
        time.sleep(POLL)
    if not imaging_done:
        print(json.dumps({'result': 'timeout', 'phase': 'imaging'}), flush=True)
        return 3
    print(f'[{time.strftime("%H:%M:%S")}] imaging complete — waiting for the job to go idle', flush=True)
    wait_until_idle(deadline)

    # ---- Phase B: Videos-only carve from the complete image ----
    response = None
    for attempt in range(10):
        response = api_post('/api/recover_all',
                            {'output': image, 'carve': True, 'skip_imaging': True,
                             'file_types': ['videos']})
        if response and response.get('success'):
            break
        print(f'  carve start attempt {attempt + 1}: {response}', flush=True)
        time.sleep(POLL)
    if not (response and response.get('success')):
        print(json.dumps({'result': 'carve_start_failed',
                          'error': (response or {}).get('error')}), flush=True)
        return 4
    print(f'[{time.strftime("%H:%M:%S")}] video carve started -> {response.get("recup_dir")}', flush=True)

    # ---- Phase C: wait for carve, then summarize ----
    final = None
    while not overtime(deadline):
        job = api_get('/api/recovery_progress')
        if not job:
            time.sleep(POLL)
            continue
        carving = (job.get('progress') or {}).get('carving') or {}
        print(f'[{time.strftime("%H:%M:%S")}] carving={carving.get("state")} '
              f'pct={carving.get("pct")} detail={carving.get("detail")}', flush=True)
        if not job.get('active'):
            final = job
            break
        time.sleep(POLL)

    exit_code = 0
    if final:
        summary = {'result': final.get('stage'), 'outcome': final.get('outcome'),
                   'files_recovered': final.get('files_recovered'),
                   'recup_dir': final.get('recup_dir'), 'error': final.get('error'),
                   'image': image}
        if final.get('stage') == 'partial':
            exit_code = 6
        elif final.get('stage') in ('failed', 'cancelled'):
            exit_code = 7
    else:
        summary = {'result': 'timeout', 'phase': 'carving', 'image': image}
        exit_code = 3
    print('==== CARVE DONE ====')
    print(json.dumps(summary, indent=2), flush=True)
    return exit_code


# Only run when started directly: importing this file (tests, tools) must not start a recovery.
if __name__ == '__main__':
    sys.exit(main())
