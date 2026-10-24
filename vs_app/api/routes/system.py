"""System, device, dialog, and job-inspection endpoints (including SSE)."""

import asyncio
import json
import os
import subprocess
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from vs_app import runtime, settings
from vs_app.api import policy
from vs_app.api.schemas import OpenFolderRequest, TestDiskRequest, UsbipdBindRequest
from vs_app.core import recovery as core
from vs_app.execution import media
from vs_app.execution import bridge
from vs_app.execution.bridge import BUSID_RE

router = APIRouter(prefix='/api')
# Share re-attaches (detach first) and Release hands the drive to Windows: either one
# pulls the source out from under a running ddrescue.
USB_BUSY_ERROR = ('A recovery is running. Sharing or releasing a USB drive now could '
                  'detach the drive being imaged. Wait for it to finish or cancel it first.')
IS_WINDOWS = os.name == 'nt'
LOGGER = settings.LOGGER

HEARTBEAT_SECONDS = 15


# --------------------------------------------------------------------------- #
# Health / status
# --------------------------------------------------------------------------- #
@router.get('/health')
def health():
    return {'status': 'ok', 'version': settings.APP_VERSION, 'auth': 'bearer',
            'architecture': 'fastapi'}


@router.get('/status')
def status():
    return {
        'status': 'online',
        'version': settings.APP_VERSION,
        'workspace': settings.SETTINGS.base_dir,
        'host': 'windows-wsl-bridge' if IS_WINDOWS else 'linux',
        'wsl_distro': settings.distro(),
        'gpu': 'NVIDIA GeForce RTX 3050 (NVENC Enabled)',
        'recovery_engine': 'GNU ddrescue (2048B optical / 512B pen drive)',
        'carver': 'PhotoRec (testdisk package)',
        'partitioner': 'TestDisk (read-only analysis, interactive undelete on demand)',
        'transcoder': 'HandBrakeCLI + FFmpeg',
        'architecture': 'FastAPI + Uvicorn (ASGI), SQLite job store, SSE',
        'jobs_db': runtime.STORE.path,
        'recovery_roots': list(settings.SETTINGS.recovery_roots),
        'media_roots': list(settings.SETTINGS.media_roots),
    }


# --------------------------------------------------------------------------- #
# Devices
# --------------------------------------------------------------------------- #
@router.get('/drives')
async def drives():
    return await asyncio.to_thread(_drives)


def _drives():
    bridge.wsl_ready()
    found = bridge.scan_recovery_drives()
    attached, unshared, storage = [], [], {}
    if IS_WINDOWS:
        storage, exe = bridge.usbipd_storage()
        unshared = [busid for busid, state in storage.items() if state == 'Not shared']
        if not any(item['type'] == 'usb' for item in found) and exe:
            storage, attached = bridge.attach_all_shared(exe)
            unshared = [busid for busid, state in storage.items() if state == 'Not shared']
            if attached:
                found = bridge.wait_for_usb_drive()
    payload = {'success': True, 'drives': found, 'count': len(found)}
    attached_ids = sorted(busid for busid, state in storage.items() if state == 'Attached')
    if attached_ids:
        payload['attached'] = attached_ids
    released = bridge.released_busids(storage)
    if released:
        payload['released'] = released
    if attached:
        payload['auto_attached'] = attached
    if unshared:
        payload['unshared'] = unshared
        payload['bind_hint'] = ('Seen by usbipd but not shared with WSL. Use "Share with WSL" '
                                '(one UAC prompt) for the bus ID you want.')
    return payload


@router.post('/usbipd_bind')
def usbipd_bind(payload: UsbipdBindRequest):
    return _usbipd_bind(payload.busid)


def _recovery_running():
    job = runtime.JOBS.current('recovery')
    return job is not None and job.active


def _usbipd_bind(busid):
    if not IS_WINDOWS:
        return {'success': False, 'error': 'usbipd is only used on the Windows host.'}
    if _recovery_running():
        return {'success': False, 'error': USB_BUSY_ERROR}
    busid = str(busid or '').strip()
    if not BUSID_RE.match(busid):
        return {'success': False, 'error': 'A bus ID looks like 2-1. Pick one from the drive list.'}
    storage, exe = bridge.usbipd_storage()
    if not exe:
        return {'success': False, 'error': 'usbipd-win was not found. Install it, then retry.'}
    if busid not in storage:
        return {'success': False, 'error': f'{busid} is not a storage device usbipd can see.'}
    state = storage[busid]
    if state == 'Not shared':
        if not bridge.elevated_bind([busid]):
            return {'success': False,
                    'error': 'Sharing needs administrator approval (UAC). Retry and approve the prompt.'}
        time.sleep(2)
        storage, exe = bridge.usbipd_storage()
        state = storage.get(busid, 'Not shared')
    if state in ('Shared', 'Attached'):
        if not bridge.wsl_ready():
            return {'success': False,
                    'error': f"WSL2 distro '{settings.distro()}' is not starting. "
                             'Open a WSL terminal once, then retry.'}
        bridge.clear_released(busid)
        if bridge.do_attach(exe, busid, state):
            found = bridge.wait_for_usb_drive()
            return {'success': True, 'message': f'{busid} attached to WSL2.', 'drives': found}
    return {'success': False,
            'error': f'{busid} could not be attached. Unplug the drive, re-plug it, '
                     'then click Share again.'}


@router.post('/usbipd_release')
def usbipd_release(payload: UsbipdBindRequest):
    return _usbipd_release(payload.busid)


def _usbipd_release(busid):
    """Hand the USB device back to Windows so it reappears in Explorer."""
    if not IS_WINDOWS:
        return {'success': False, 'error': 'usbipd is only used on the Windows host.'}
    if _recovery_running():
        return {'success': False, 'error': USB_BUSY_ERROR}
    busid = str(busid or '').strip()
    if not BUSID_RE.match(busid):
        return {'success': False, 'error': 'A bus ID looks like 2-1. Pick one from the drive list.'}
    storage, exe = bridge.usbipd_storage()
    if not exe:
        return {'success': False, 'error': 'usbipd-win was not found. Install it, then retry.'}
    if storage.get(busid) == 'Shared':
        # Windows already owns it; mark it so Detect Drives leaves it alone.
        bridge.note_released(busid)
        return {'success': True,
                'message': f'{busid} is already in Windows and will stay there.',
                'drives': bridge.scan_recovery_drives()}
    if storage.get(busid) != 'Attached':
        return {'success': False,
                'error': f'{busid} is not attached to WSL2, so Windows already has it.'}
    bridge.wsl_sync()
    if not bridge.do_detach(exe, busid):
        return {'success': False, 'error': f'{busid} could not be released. Eject it, then retry.'}
    bridge.note_released(busid)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if bridge.usbipd_storage()[0].get(busid) != 'Attached':
            break
        time.sleep(0.5)
    # WSL needs a moment to drop the block device after detach.
    time.sleep(2)
    found = bridge.scan_recovery_drives()
    return {'success': True,
            'message': f'{busid} handed back to Windows — it should reappear in This PC.',
            'drives': found}


@router.get('/tools')
def tools():
    available = {'ddrescue': False, 'photorec': False, 'testdisk': False}
    try:
        out = subprocess.check_output(
            bridge.run_linux(['bash', '-lc',
                             'command -v ddrescue photorec testdisk 2>/dev/null; true']),
            text=True, stderr=subprocess.DEVNULL, timeout=25)
        lines = [line.strip() for line in out.splitlines() if line.strip()]
        for name in available:
            available[name] = any(line.endswith('/' + name) for line in lines)
    except Exception:
        pass
    missing = [name for name, ok in available.items() if not ok]
    install_hint = None
    if missing:
        packages = []
        if 'ddrescue' in missing:
            packages.append('gddrescue')
        if 'photorec' in missing or 'testdisk' in missing:
            packages.append('testdisk')
        install_hint = 'sudo apt install -y ' + ' '.join(packages)
    return {
        'success': True, 'tools': available, 'missing': missing,
        'install_hint': install_hint, 'wsl_distro': settings.distro(),
        'host': 'windows-wsl-bridge' if IS_WINDOWS else 'linux',
        'ffmpeg': bool(media.find_ffmpeg()),
        'handbrake': bool(media.find_handbrake()),
        'untrunc': bool(bridge.find_untrunc()),
    }


# --------------------------------------------------------------------------- #
# Native dialogs / folders / TestDisk console
# --------------------------------------------------------------------------- #
@router.get('/browse_file')
def browse_file():
    return _browse_file()


@router.post('/browse_file')
def browse_file_post():
    return _browse_file()


def _browse_file():
    file_path = None
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        root.focus_force()
        try:
            file_path = filedialog.askopenfilename(
                title='Select Input Video File for Trimming',
                filetypes=[
                    ('Video Files',
                     '*.mp4;*.vob;*.mkv;*.avi;*.mov;*.wmv;*.ts;*.webm;*.m4v;*.mpg;*.mpeg'),
                    ('All Files', '*.*'),
                ])
        finally:
            root.destroy()
    except Exception as exc:
        LOGGER.warning('Open dialog unavailable: %s', exc)
        return {'success': False,
                'error': 'The native file dialog could not open. Type the full path into the field instead.'}
    if not file_path:
        return {'success': False, 'cancelled': True, 'message': 'File selection was cancelled.'}
    normalized = os.path.abspath(os.path.normpath(file_path))
    return {'success': True, 'path': normalized, 'filename': os.path.basename(normalized),
            'directory': os.path.dirname(normalized),
            'message': f"Selected '{os.path.basename(normalized)}'"}


@router.get('/browse_save_file')
def browse_save_file(initial_name: str = 'Trimmed_output.mp4'):
    return _browse_save_file(initial_name)


@router.post('/browse_save_file')
def browse_save_file_post(payload: dict | None = None):
    initial = (payload or {}).get('initial_name', 'Trimmed_output.mp4')
    return _browse_save_file(initial)


def _browse_save_file(initial_file='Trimmed_output.mp4'):
    initial = os.path.basename(str(initial_file or '').strip()) or 'Trimmed_output.mp4'
    if not os.path.splitext(initial)[1]:
        initial += '.mp4'
    file_path = None
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        root.focus_force()
        try:
            file_path = filedialog.asksaveasfilename(
                title='Select Output Destination & File Name',
                initialfile=initial, defaultextension='.mp4',
                filetypes=[
                    ('MP4 Video (*.mp4)', '*.mp4'),
                    ('MKV Video (*.mkv)', '*.mkv'),
                    ('VOB Video (*.vob)', '*.vob'),
                    ('All Files (*.*)', '*.*'),
                ])
        finally:
            root.destroy()
    except Exception as exc:
        LOGGER.warning('Save dialog unavailable: %s', exc)
        return {'success': False,
                'error': 'The native save dialog could not open. Type the full output path into the field instead.'}
    if not file_path:
        return {'success': False, 'cancelled': True,
                'message': 'Output destination selection was cancelled.'}
    normalized = os.path.abspath(os.path.normpath(file_path))
    return {'success': True, 'path': normalized, 'filename': os.path.basename(normalized),
            'directory': os.path.dirname(normalized),
            'message': f"Output path set to '{os.path.basename(normalized)}'"}


@router.get('/workspace_videos')
def workspace_videos():
    videos = []
    try:
        for location, folder in (('workspace', settings.SETTINGS.base_dir),
                                 ('uploads', os.path.join(settings.SETTINGS.base_dir, 'uploads'))):
            if not os.path.isdir(folder):
                continue
            for name in os.listdir(folder):
                full_path = os.path.join(folder, name)
                if (name.lower().rsplit('.', 1)[-1] in core.VIDEO_EXTS
                        and os.path.isfile(full_path)):
                    videos.append({
                        'filename': name,
                        'path': full_path,
                        'size_mb': round(os.path.getsize(full_path) / (1024 * 1024), 2),
                        'location': location,
                    })
    except Exception as exc:
        return {'success': False, 'error': str(exc), 'videos': []}
    return {'success': True, 'videos': videos, 'count': len(videos)}


@router.post('/open_folder')
def open_folder(payload: OpenFolderRequest):
    path = str(payload.path or '').strip()
    if not path:
        return {'success': False, 'error': 'Folder path is required.'}
    roots = settings.SETTINGS.recovery_roots + settings.SETTINGS.media_roots
    if not policy.within(path, roots):
        return {'success': False,
                'error': 'Folder is outside the configured recovery/media roots.'}
    win_path = bridge.wsl_to_win_path(path) if path.startswith('/') else path
    if not IS_WINDOWS:
        return {'success': False,
                'error': f'Only available on the Windows host. Folder: {win_path}'}
    try:
        if os.path.isdir(win_path):
            os.startfile(win_path)  # noqa: windows-only
            return {'success': True, 'message': f'Opened {win_path}'}
        return {'success': False, 'error': f'Folder does not exist yet: {win_path}'}
    except Exception as exc:
        return {'success': False, 'error': str(exc)}


@router.post('/testdisk')
def launch_testdisk(payload: TestDiskRequest):
    image = str(payload.image or '').strip()
    if not image:
        return {'success': False,
                'error': 'Image path is required — image the drive with ddrescue first.'}
    image_wsl = bridge.win_to_wsl_path(image)
    if not policy.within(image_wsl, settings.SETTINGS.recovery_roots):
        return {'success': False, 'error': 'Image must be inside a recovery root.'}
    if not bridge.linux_path_exists(image_wsl):
        return {'success': False, 'error': f'Image not found: {image_wsl}'}
    if not IS_WINDOWS:
        return {'success': False,
                'error': f'Launch manually inside this Linux host: testdisk {image_wsl}'}
    if bridge.image_busy(image_wsl):
        return {'success': False, 'error': core.image_busy_error(image_wsl)}
    try:
        # New console directly: avoids cmd.exe re-parsing paths that contain spaces.
        # The window holds the image lock, so no imaging starts while it is open.
        subprocess.Popen(['wsl.exe', '-d', settings.distro(), '-e']
                         + core.locked_argv(['testdisk', image_wsl], image_wsl),
                         creationflags=getattr(subprocess, 'CREATE_NEW_CONSOLE', 0))
        return {'success': True,
                'message': (f'TestDisk launched in a new window for {image_wsl}.\n'
                            'Flow: [Proceed] → select the partition → Advanced → Undelete → '
                            'select files (a/A select all) → C to copy into the chosen directory.')}
    except Exception as exc:
        return {'success': False, 'error': str(exc)}


# --------------------------------------------------------------------------- #
# Jobs and SSE
# --------------------------------------------------------------------------- #
@router.get('/jobs')
async def list_jobs(kind: str = '', limit: int = 25):
    limit = max(1, min(int(limit), 100))
    rows = await runtime.STORE.recent(limit=limit)
    if kind:
        rows = [row for row in rows if row['kind'] == kind]
    return {'success': True, 'jobs': rows, 'count': len(rows)}


@router.get('/jobs/{job_id}')
async def get_job(job_id: str):
    job = runtime.JOBS.get(job_id)
    if job is not None:
        return job.public()
    snapshot = await runtime.STORE.get_job(job_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail='Unknown job.')
    return snapshot


@router.post('/jobs/{job_id}/cancel')
async def cancel_job(job_id: str):
    job = runtime.JOBS.get(job_id)
    if job is None or not job.active:
        return {'success': False, 'error': 'Job is not running.'}
    job.request_cancel()
    from vs_app.execution.adapters import cancel_process
    await cancel_process(job)
    return {'success': True, 'message': 'Cancellation requested.',
            'job': job.public(include_log=False)}


def _frame(event):
    data = json.dumps(event['data'], ensure_ascii=False, separators=(',', ':'))
    lines = []
    if event.get('id') is not None:
        lines.append(f"id: {event['id']}")
    lines.append(f"event: {event['type']}")
    lines.append(f'data: {data}')
    return '\n'.join(lines) + '\n\n'


@router.get('/jobs/{job_id}/events')
async def job_events(request: Request, job_id: str, since: int = 0):
    job = runtime.JOBS.get(job_id)
    snapshot = job.public() if job is not None else await runtime.STORE.get_job(job_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail='Unknown job.')

    async def stream():
        last = since
        terminal_seen = False
        for event in await runtime.STORE.events_since(job_id, after=since):
            last = max(last, event['id'])
            if event['type'] == 'end':
                terminal_seen = True
            yield _frame(event)
        current = job.public(include_log=False) if job is not None else snapshot
        yield _frame({'type': 'state', 'data': current})
        if not current.get('active'):
            if not terminal_seen:
                yield _frame({'type': 'end',
                              'data': {'status': current.get('status'),
                                       'error': current.get('error')}})
            return
        if job is None:
            return
        queue = runtime.BUS.subscribe(job_id)
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield ': keep-alive\n\n'
                    latest = job.public(include_log=False) if job is not None else None
                    if latest is not None and not latest.get('active'):
                        yield _frame({'type': 'state', 'data': latest})
                        yield _frame({'type': 'end',
                                      'data': {'status': latest.get('status'),
                                               'error': latest.get('error')}})
                        break
                    continue
                if event.get('id') is not None and event['id'] <= last:
                    continue
                if event.get('id') is not None:
                    last = event['id']
                yield _frame(event)
                if event['type'] == 'end':
                    break
        finally:
            runtime.BUS.unsubscribe(job_id, queue)

    return StreamingResponse(
        stream(), media_type='text/event-stream',
        headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})
