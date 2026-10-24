"""Bridge, device, and filesystem helpers shared by the API and the pipelines."""

import atexit
import json
import os
import re
import shlex
import shutil
import subprocess
import time

from vs_app import settings
from vs_app.core import recovery as core
from vs_app.execution import media

IS_WINDOWS = os.name == 'nt'
LOGGER = settings.LOGGER
SETTINGS = settings.SETTINGS

BUSID_RE = re.compile(r'^\d+-\d+$')
SAFE_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._ -]{0,63}$')
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_ACCESS_DENIED = 5
STILL_ACTIVE = 259

_WSL_KEEPALIVE = {'proc': None}

# Bus IDs the user explicitly released back to Windows. Detect Drives auto-attaches
# every Shared device, which would otherwise re-hide a released drive from Explorer
# the moment the page reloads — making the Release button pointless.
_RELEASED = set()


def note_released(busid):
    _RELEASED.add(busid)


def clear_released(busid):
    _RELEASED.discard(busid)


def released_busids(storage):
    """Shared devices the user released, so the UI can offer a re-share button."""
    return sorted(busid for busid, state in storage.items()
                  if state == 'Shared' and busid in _RELEASED)


# --------------------------------------------------------------------------- #
# Bridge plumbing
# --------------------------------------------------------------------------- #
def run_linux(args):
    """Build argv for a Linux tool; `wsl -e` execs directly (no shell expansion)."""
    if IS_WINDOWS:
        return ['wsl', '-d', settings.distro(), '-e'] + [str(item) for item in args]
    return [str(item) for item in args]


def process_alive(pid):
    """Whether a host process exists. Never signals it (os.kill terminates on Windows)."""
    if pid <= 0:
        return False
    if IS_WINDOWS:
        import ctypes
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ctypes.get_last_error() == ERROR_ACCESS_DENIED
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def command_text(argv):
    if not argv:
        return None
    if IS_WINDOWS:
        return subprocess.list2cmdline([str(item) for item in argv])
    return shlex.join([str(item) for item in argv])


def ensure_wsl_alive():
    """Hold a WSL process open so usbipd attachments survive WSL's idle shutdown."""
    if not IS_WINDOWS:
        return
    proc = _WSL_KEEPALIVE['proc']
    if proc is not None and proc.poll() is None:
        return
    try:
        _WSL_KEEPALIVE['proc'] = subprocess.Popen(
            ['wsl', '-d', settings.distro(), '-e', 'sleep', 'infinity'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        _WSL_KEEPALIVE['proc'] = None


def wsl_ready(timeout=25):
    """Block until the distro answers; usbipd rejects attach while WSL is still booting."""
    if not IS_WINDOWS:
        return True
    ensure_wsl_alive()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(run_linux(['true']),
                                    capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def stop_wsl_keepalive():
    proc = _WSL_KEEPALIVE.get('proc')
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass


def reap_orphan_keepalive():
    """Drop keepers left behind when a previous run was killed instead of closed.

    Closing the console window skips the lifespan finally-block, so terminate() never
    runs and each restart adds another keeper that holds the distro open forever.
    Only processes whose own parent is already gone are touched.
    """
    if not IS_WINDOWS:
        return
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='wsl.exe'\" | "
        "Where-Object { $_.CommandLine -like '*-e sleep infinity*' -and "
        "-not (Get-Process -Id $_.ParentProcessId -ErrorAction SilentlyContinue) } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    try:
        subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
                       capture_output=True, timeout=30)
    except Exception as exc:
        LOGGER.warning('orphan keepalive reap failed: %s', exc)


atexit.register(stop_wsl_keepalive)


# --------------------------------------------------------------------------- #
# usbipd-win
# --------------------------------------------------------------------------- #
def usbipd_exe():
    exe = media.which('usbipd')
    if exe:
        return exe
    fallback = r'C:\Program Files\usbipd-win\usbipd.exe'
    return fallback if os.path.exists(fallback) else None


def parse_usbipd_storage(out):
    result = {}
    for line in out.splitlines():
        if line.strip().startswith('Persisted:'):
            break
        match = re.match(
            r'^\s*(\d+-\d+)\s+[0-9a-fA-F]{4}:[0-9a-fA-F]{4}\s+(.+?)\s+'
            r'(Not shared|Shared|Attached)\s*$', line)
        if not match:
            continue
        busid, desc, state = match.group(1), match.group(2).lower(), match.group(3)
        if 'mass storage' in desc or 'disk' in desc or 'flash' in desc:
            result[busid] = state
    return result


def usbipd_storage():
    exe = usbipd_exe()
    if not exe:
        return {}, None
    try:
        out = subprocess.check_output([exe, 'list'], text=True,
                                      stderr=subprocess.DEVNULL, timeout=20)
    except Exception as exc:
        LOGGER.warning('usbipd list failed: %s', exc)
        return {}, exe
    return parse_usbipd_storage(out), exe


def do_attach(exe, busid, state):
    try:
        if state == 'Attached':
            do_detach(exe, busid)
            time.sleep(1)
        wsl_ready()
        result = subprocess.run([exe, 'attach', '--wsl', settings.distro(), '--busid', busid],
                                capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            result = subprocess.run([exe, 'attach', '--wsl', '--busid', busid],
                                    capture_output=True, text=True, timeout=30)
        LOGGER.info('usbipd attach %s rc=%s %s', busid, result.returncode,
                    (result.stderr or '').strip()[:200])
        if result.returncode != 0:
            return False
        # usbipd flips to "Attached" before WSL's vhci layer enumerates the disk
        # on a cold boot, so confirm a USB disk actually reaches lsblk.
        return wait_for_attached(busid) and bool(wait_for_usb_drive(timeout=20))
    except Exception as exc:
        LOGGER.warning('usbipd attach %s failed: %s', busid, exc)
        return False


def do_detach(exe, busid):
    try:
        result = subprocess.run([exe, 'detach', '--busid', busid],
                                capture_output=True, text=True, timeout=30)
        LOGGER.info('usbipd detach %s rc=%s %s', busid, result.returncode,
                    (result.stderr or '').strip()[:200])
        return result.returncode == 0
    except Exception as exc:
        LOGGER.warning('usbipd detach %s failed: %s', busid, exc)
        return False


def wsl_sync():
    """Flush WSL's writeback cache so /mnt/c recovery output is committed to disk."""
    if not IS_WINDOWS:
        return
    try:
        subprocess.run(run_linux(['sync']), capture_output=True, text=True, timeout=120)
    except Exception as exc:
        LOGGER.warning('wsl sync failed: %s', exc)


def wait_for_attached(busid, timeout=10):
    """Poll usbipd until the bus ID reads 'Attached' instead of trusting exit codes."""
    exe = usbipd_exe()
    if not exe:
        return False
    deadline = time.monotonic() + timeout
    while True:
        storage, _ = usbipd_storage()
        if storage.get(busid) == 'Attached':
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.5)


def wait_for_usb_drive(timeout=25):
    """Poll lsblk for a USB drive; cold-booted WSL enumerates vhci disks slowly."""
    deadline = time.monotonic() + timeout
    while True:
        found = scan_recovery_drives()
        usb = [item for item in found if item.get('type') == 'usb']
        if usb or time.monotonic() >= deadline:
            return found
        time.sleep(1)


def attach_all_shared(exe):
    storage = {}
    attached = []
    try:
        out = subprocess.check_output([exe, 'list'], text=True,
                                      stderr=subprocess.DEVNULL, timeout=20)
        storage = parse_usbipd_storage(out)
    except Exception:
        return storage, attached
    # Forget releases of devices that have since left the bus.
    _RELEASED.intersection_update(storage)
    for busid, state in storage.items():
        if state == 'Shared' and busid not in _RELEASED and do_attach(exe, busid, state):
            attached.append(busid)
    if attached:
        # Re-list: the caller reports on post-attach state, not the stale snapshot.
        storage, _ = usbipd_storage()
    return storage, attached


def elevated_bind(busids):
    """One explicit UAC prompt for `usbipd bind`; bus IDs are validated beforehand."""
    if not busids or not all(BUSID_RE.match(item) for item in busids):
        return False
    exe = usbipd_exe()
    if not exe:
        return False
    joined = ','.join(f"'{item}'" for item in busids)
    script = (
        f"$ErrorActionPreference='Stop'; "
        f"$ids=@({joined}); "
        f"foreach($id in $ids) {{ & '{exe}' bind --busid $id | Out-Null }}"
    )
    try:
        subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command',
             "Start-Process", "-FilePath", "powershell",
             "-Verb", "RunAs", "-Wait",
             "-ArgumentList", f"'-NoProfile','-Command',\"{script}\""],
            capture_output=True, text=True, timeout=120)
        return True
    except Exception as exc:
        LOGGER.warning('Elevated bind failed: %s', exc)
        return False


# --------------------------------------------------------------------------- #
# Paths and devices
# --------------------------------------------------------------------------- #
def win_to_wsl_path(path):
    return core.win_to_wsl_path(path)


def wsl_to_win_path(path):
    return core.wsl_to_win_path(path)


def linux_to_local_path(path):
    if not IS_WINDOWS or not path or not path.startswith('/'):
        return path
    if re.match(r'^/mnt/[a-zA-Z]/', path):
        return wsl_to_win_path(path)
    return '\\\\wsl.localhost\\' + settings.distro() + path.replace('/', '\\')


def linux_path_exists(wsl_path):
    if IS_WINDOWS:
        try:
            if os.path.exists(wsl_to_win_path(wsl_path)):
                return True
        except Exception:
            pass
    try:
        return subprocess.run(run_linux(['test', '-s', wsl_path]),
                              capture_output=True, timeout=20).returncode == 0
    except Exception:
        return False


def image_busy(wsl_path):
    """True while another process holds the image's lock (see core.image_lock_prelude)."""
    try:
        result = subprocess.run(
            run_linux(['bash', '-c', core.image_lock_prelude(wsl_path) + '; exit 0']),
            capture_output=True, text=True, timeout=30)
    except Exception as exc:
        LOGGER.warning('image lock probe failed for %s: %s', wsl_path, exc)
        return False
    return result.returncode == core.IMAGE_BUSY_EXIT


def linux_mkdir(wsl_path):
    try:
        subprocess.run(run_linux(['mkdir', '-p', wsl_path]),
                       capture_output=True, timeout=30)
    except Exception as exc:
        LOGGER.warning('mkdir %s failed: %s', wsl_path, exc)


def scan_recovery_drives():
    try:
        out = subprocess.check_output(
            run_linux(['lsblk', '-P', '-o', 'NAME,TYPE,SIZE,TRAN,RM,MODEL,LOG-SEC']),
            text=True, stderr=subprocess.DEVNULL, timeout=25)
    except Exception as exc:
        LOGGER.warning('lsblk scan failed: %s', exc)
        return []
    return core.parse_lsblk_drives(out)


def device_identity(device):
    """Size in bytes plus serial/filesystem UUID/label of a device, for image records."""
    try:
        out = subprocess.check_output(
            run_linux(['lsblk', '-b', '-d', '-n', '-P', '-o', 'NAME,SIZE,SERIAL,UUID,LABEL', device]),
            text=True, stderr=subprocess.DEVNULL, timeout=20)
    except Exception as exc:
        LOGGER.warning('lsblk identity for %s failed: %s', device, exc)
        return {}
    props = (core.parse_lsblk_pairs(out) or [{}])[0]
    try:
        size = int(props.get('SIZE') or 0) or None
    except ValueError:
        size = None
    return {'size': size, 'serial': props.get('SERIAL', '').strip(),
            'uuid': props.get('UUID', '').strip(), 'label': props.get('LABEL', '').strip()}


def source_record_path(image_wsl):
    return linux_to_local_path(image_wsl) + '.source.json'


def read_source_record(image_wsl):
    try:
        with open(source_record_path(image_wsl), 'r', encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def write_source_record(image_wsl, device, identity):
    """Remember which device an image came from, beside the image and its mapfile."""
    record = dict(identity, device=device, created=time.strftime('%Y-%m-%dT%H:%M:%S'))
    path = source_record_path(image_wsl)
    try:
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump(record, handle, indent=2)
    except OSError as exc:
        LOGGER.warning('could not write %s: %s', path, exc)


def image_source_problem(image_wsl, device):
    """Why imaging `device` into an existing image would corrupt it, or None."""
    local = linux_to_local_path(image_wsl)
    mapfile = local + '.log'
    if not (os.path.exists(local) or os.path.exists(mapfile)):
        return None
    try:
        with open(mapfile, 'r', encoding='utf-8', errors='replace') as handle:
            extent = core.mapfile_extent(handle.read())
    except OSError:
        extent = None
    reason = core.source_mismatch(read_source_record(image_wsl), device_identity(device), extent)
    return core.source_mismatch_error(image_wsl, reason) if reason else None


def image_space_problem(image_wsl, device):
    """Why the image's drive lacks room for the rest of `device`, or None."""
    local = linux_to_local_path(image_wsl)
    existing = os.path.getsize(local) if os.path.isfile(local) else 0
    folder = os.path.dirname(local) or '.'
    while not os.path.isdir(folder):  # the run folder is created later; measure its drive
        parent = os.path.dirname(folder)
        if parent == folder:
            return None
        folder = parent
    try:
        free = shutil.disk_usage(folder).free
    except OSError as exc:
        LOGGER.warning('free-space check skipped for %s: %s', folder, exc)
        return None
    return core.space_problem(device_identity(device).get('size'), existing, free)


def detect_sector_size(device):
    if core.is_optical_device(device):
        return 2048
    try:
        out = subprocess.check_output(
            run_linux(['lsblk', '-Pdno', 'LOG-SEC', device]),
            text=True, stderr=subprocess.DEVNULL, timeout=15).strip()
        value = int(out.splitlines()[0])
        if value in (512, 2048, 4096):
            return value
    except Exception:
        pass
    return 512


def check_device_readable(device):
    target = _shell_quote(str(device))
    try:
        result = subprocess.run(
            run_linux(['bash', '-lc',
                       f'if [ ! -e {target} ]; then echo MISSING; '
                       f'elif [ ! -r {target} ]; then echo DENIED; else echo OK; fi']),
            capture_output=True, text=True, timeout=25)
        state = (result.stdout or '').strip()
    except Exception as exc:
        return f'WSL2 check for {device} failed: {exc}'
    if state == 'OK':
        return None
    if state == 'MISSING':
        return (f'{device} not found in WSL2 — plug the drive in and click Detect Drives '
                '(only devices usbipd has shared can be attached).')
    return (f'WSL2 cannot read {device} (permission denied). One-time fix in a WSL terminal: '
            f'sudo usermod -aG disk $USER — then run "wsl --shutdown" and reconnect.')


def _shell_quote(value):
    return "'" + str(value).replace("'", "'\\''") + "'"


# --------------------------------------------------------------------------- #
# Carved output helpers
# --------------------------------------------------------------------------- #
def free_path(path):
    """path, or path with ' (2)', ' (3)', ... before the extension if it already exists."""
    if not os.path.exists(path):
        return path
    root, extension = os.path.splitext(path)
    number = 2
    while os.path.exists(f'{root} ({number}){extension}'):
        number += 1
    return f'{root} ({number}){extension}'


def set_aside_unselected_office(recup_dir, categories):
    office = [cat for cat in categories if 'keep' in core.CARVE_CATEGORIES[cat]]
    if not office:
        return 0, None
    keep = set().union(*(core.CARVE_CATEGORIES[cat]['keep'] for cat in office))
    local = linux_to_local_path(recup_dir.rstrip('/'))
    parent, base = os.path.dirname(local), os.path.basename(local)
    other = os.path.join(parent, base + '_other')
    moved = 0
    try:
        entries = list(os.scandir(parent))
    except OSError:
        return 0, None
    for entry in entries:
        if not (entry.is_dir() and (entry.name == base or entry.name.startswith(base + '.'))):
            continue
        try:
            names = os.listdir(entry.path)
        except OSError:
            continue
        for name in names:
            ext = os.path.splitext(name)[1].lstrip('.').lower()
            if ext in core.OFFICE_CONTAINER_EXTS and ext not in keep:
                os.makedirs(other, exist_ok=True)
                shutil.move(os.path.join(entry.path, name), free_path(os.path.join(other, name)))
                moved += 1
    return moved, other


def count_recovered_files(recup_dir):
    recup_dir = recup_dir.rstrip('/\\')
    parent = os.path.dirname(recup_dir) or '.'
    base = os.path.basename(recup_dir)
    if not base:
        return 0
    skip = {'photorec.log', 'report.xml'}

    if IS_WINDOWS:
        try:
            local_parent = linux_to_local_path(parent)
            total, found = 0, False
            for entry in os.scandir(local_parent):
                if entry.is_dir() and (entry.name == base or entry.name.startswith(base + '.')):
                    found = True
                    total += sum(len([f for f in files if f not in skip])
                                 for _, _, files in os.walk(entry.path))
            if found:
                return total
        except Exception:
            pass

    try:
        wsl_parent = win_to_wsl_path(parent)
        names = subprocess.check_output(
            run_linux(['ls', '-1', wsl_parent]),
            text=True, stderr=subprocess.DEVNULL, timeout=30).splitlines()
        total, found = 0, False
        for name in names:
            if name == base or name.startswith(base + '.'):
                found = True
                out = subprocess.check_output(
                    run_linux(['find', f"{wsl_parent}/{name}", '-type', 'f']),
                    text=True, stderr=subprocess.DEVNULL, timeout=180)
                total += len([line for line in out.splitlines()
                              if line.strip() and os.path.basename(line.strip()) not in skip])
        return total if found else 0
    except Exception:
        return None


def has_moov(path):
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as handle:
            if b'moov' in handle.read(1 << 20):
                return True
            if size > 1 << 20:
                handle.seek(max(0, size - (1 << 20)))
                return b'moov' in handle.read(1 << 20)
    except OSError:
        pass
    return False


def discover_carved_videos(parent_local, base):
    found = []
    if not base:
        return found
    try:
        entries = sorted(os.scandir(parent_local), key=lambda entry: entry.name)
    except OSError:
        return found
    for entry in entries:
        if not entry.is_dir():
            continue
        name = entry.name
        if not name.startswith(base) or '_repaired' in name.lower():
            continue
        try:
            files = sorted(os.scandir(entry.path), key=lambda item: item.name)
        except OSError:
            continue
        for handle in files:
            if (handle.is_file()
                    and os.path.splitext(handle.name)[1].lstrip('.').lower() in core.VIDEO_EXTS):
                found.append({
                    'name': handle.name,
                    'local': handle.path,
                    'wsl': f"{win_to_wsl_path(parent_local)}/{name}/{handle.name}",
                    'label': name,
                })
    return found


def find_ffmpeg():
    return media.find_ffmpeg(), media.which('ffprobe')


def find_untrunc():
    return media.which('untrunc', 'untrunc.exe')


def atomic_target(path):
    root, extension = os.path.splitext(path)
    return root + '.__vs_partial__' + extension


def output_duration(ffprobe, path):
    if not ffprobe or not os.path.exists(path):
        return None
    try:
        out = subprocess.check_output(
            [ffprobe, '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path],
            text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return None
    try:
        return out if float(out) > 0 else None
    except ValueError:
        return None
