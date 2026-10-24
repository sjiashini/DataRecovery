"""Safe media operations: argument-only subprocesses, atomic outputs, exact cancellation."""

import os
import shlex
import shutil
import subprocess
import tempfile

from vs_app.core.recovery import (
    normalize_codec,
    normalize_preset,
    normalize_quality,
    normalize_timecode,
    wsl_to_win_path,
)

IS_WINDOWS = os.name == 'nt'


class MediaError(RuntimeError):
    pass


class MediaCancelled(MediaError):
    pass


def local_path(path):
    """Convert a WSL /mnt/<drive> path to a Windows path when running on Windows."""
    if not path:
        return path
    path = str(path).strip().strip('"')
    if IS_WINDOWS and path.startswith('/mnt/'):
        return wsl_to_win_path(path)
    return path


REGISTRY_PATHS = (
    ('HKEY_LOCAL_MACHINE', r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment'),
    ('HKEY_CURRENT_USER', 'Environment'),
)


def _registry_path_entries():
    import winreg
    entries = []
    for hive, key in REGISTRY_PATHS:
        try:
            with winreg.OpenKey(getattr(winreg, hive), key) as handle:
                value, _kind = winreg.QueryValueEx(handle, 'Path')
        except OSError:
            continue
        entries.extend(os.path.expandvars(value).split(';'))
    return entries


def merge_path(current, extra):
    """`current` PATH plus any `extra` entries it lacks, order kept, no duplicates."""
    parts = [part for part in current.split(os.pathsep) if part]
    seen = {os.path.normcase(part.rstrip('\\/')) for part in parts}
    for entry in extra:
        key = os.path.normcase(entry.strip().rstrip('\\/'))
        if key and key not in seen:
            seen.add(key)
            parts.append(entry.strip())
    return os.pathsep.join(parts)


def refresh_path():
    """Pick up PATH entries added since this process started (e.g. by winget install).

    A process keeps the PATH it was started with, so without this a tool installed
    while the backend runs stays invisible until a restart. Returns True if PATH grew.
    """
    if not IS_WINDOWS:
        return False
    current = os.environ.get('PATH', '')
    merged = merge_path(current, _registry_path_entries())
    if merged == current:
        return False
    os.environ['PATH'] = merged
    return True


def which(*names):
    """shutil.which over several names, retrying once with a refreshed PATH."""
    for attempt in range(2):
        for name in names:
            exe = shutil.which(name)
            if exe:
                return exe
        if attempt == 0 and not refresh_path():
            break
    return None


def find_ffmpeg():
    exe = which('ffmpeg')
    if exe:
        return exe
    winget = os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe')
    return winget if os.path.exists(winget) else None


def find_handbrake():
    exe = which('HandBrakeCLI', 'HandBrakeCLI.exe')
    if exe:
        return exe
    for candidate in (
        r'C:\Program Files\HandBrake\HandBrakeCLI.exe',
        r'C:\Program Files (x86)\HandBrake\HandBrakeCLI.exe',
        os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\WinGet\Links\HandBrakeCLI.exe'),
    ):
        if os.path.exists(candidate):
            return candidate
    return None


def display_command(cmd):
    if IS_WINDOWS:
        return subprocess.list2cmdline([str(part) for part in cmd])
    return shlex.join([str(part) for part in cmd])


def capture(cmd, job=None, cancel_check=None, timeout=None):
    """Run a command, return (returncode, stdout, stderr), and never leak its output pipe."""
    if cancel_check is not None and cancel_check():
        raise MediaCancelled('Operation cancelled.')
    try:
        proc = subprocess.Popen(
            [str(part) for part in cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, errors='replace',
        )
    except FileNotFoundError as exc:
        raise MediaError(f"Required executable not found: {exc.filename}") from exc
    if job is not None:
        job.attach_process(proc)
    if cancel_check is not None and cancel_check():
        # Cancel arrived while the process was starting, before there was one to stop.
        proc.kill()
    try:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            stdout, stderr = proc.communicate()
            raise MediaError(f'Operation timed out after {timeout}s.') from exc
    finally:
        if job is not None:
            job.detach_process()
    if cancel_check is not None and cancel_check():
        raise MediaCancelled('Operation cancelled.')
    return proc.returncode, stdout, stderr


def _run(cmd, job=None, cancel_check=None, timeout=None):
    returncode, stdout, stderr = capture(
        cmd, job=job, cancel_check=cancel_check, timeout=timeout
    )
    if returncode != 0:
        detail = (stderr or stdout or '').strip()
        raise MediaError(
            f'Command failed with exit code {returncode}.'
            + (f' {detail[-2000:]}' if detail else '')
        )
    return stdout, stderr


def _prepare_output(output):
    output = os.path.abspath(output)
    parent = os.path.dirname(output)
    if parent:
        os.makedirs(parent, exist_ok=True)
    root, extension = os.path.splitext(output)
    return output, f'{root}.__vs_partial__{extension}'


def _commit_output(temp_path, output):
    if not os.path.exists(temp_path) or os.path.getsize(temp_path) <= 0:
        raise MediaError('The media tool produced no output.')
    os.replace(temp_path, output)


def _cleanup(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def validate_media_input(path):
    path = local_path(path)
    if not path:
        raise MediaError('Input path is required.')
    if not os.path.isfile(path):
        raise MediaError(f'Input file not found: {path}')
    return path


def validate_time_range(start, end):
    normalized_start = normalize_timecode(start, '00:00:00')
    normalized_end = normalize_timecode(end, '00:01:00')
    if normalized_start is None or normalized_end is None:
        raise MediaError('Start/end must be HH:MM:SS, MM:SS, or seconds.')
    start_seconds = _time_seconds(normalized_start)
    end_seconds = _time_seconds(normalized_end)
    if end_seconds <= start_seconds:
        raise MediaError('End time must be later than start time.')
    return normalized_start, normalized_end, end_seconds - start_seconds


def _time_seconds(value):
    parts = value.split(':')
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    return float(value)


def build_cut_command(input_path, output_path, start, end, mode='copy'):
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise MediaError('ffmpeg was not found on PATH.')
    input_path = validate_media_input(input_path)
    output_path = os.path.abspath(local_path(output_path))
    if os.path.normcase(input_path) == os.path.normcase(output_path):
        raise MediaError('Output must not overwrite the input file.')
    start, _end, duration = validate_time_range(start, end)
    if mode == 'copy':
        codec = ['-c', 'copy']
    elif mode == 'nvenc':
        codec = ['-c:v', 'hevc_nvenc', '-cq', '18', '-c:a', 'aac']
    else:
        raise MediaError("Cut mode must be 'copy' or 'nvenc'.")
    output, temp = _prepare_output(output_path)
    cmd = [
        ffmpeg, '-y', '-ss', start, '-i', input_path,
        '-t', f'{duration:.3f}', *codec,
        '-map_metadata', '-1', '-movflags', '+faststart', temp,
    ]
    return cmd, output, temp


def run_cut(input_path, output_path, start, end, mode='copy', job=None, cancel_check=None):
    cmd, output, temp = build_cut_command(input_path, output_path, start, end, mode)
    try:
        _run(cmd, job=job, cancel_check=cancel_check)
        _commit_output(temp, output)
    finally:
        _cleanup(temp)
    return {'output_file': output, 'command': display_command(cmd)}


def build_concat_command(files, output_path, runtime_dir):
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise MediaError('ffmpeg was not found on PATH.')
    local_files = [validate_media_input(item) for item in files]
    output = os.path.abspath(local_path(output_path))
    if any(os.path.normcase(item) == os.path.normcase(output) for item in local_files):
        raise MediaError('Output must not overwrite an input file.')
    os.makedirs(runtime_dir, exist_ok=True)
    fd, list_path = tempfile.mkstemp(prefix='concat-', suffix='.txt', dir=runtime_dir, text=True)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            for item in local_files:
                escaped = os.path.abspath(item).replace('\\', '/').replace("'", "'\\''")
                handle.write(f"file '{escaped}'\n")
    except Exception:
        _cleanup(list_path)
        raise
    output, temp = _prepare_output(output)
    cmd = [ffmpeg, '-y', '-f', 'concat', '-safe', '0', '-i', list_path, '-c', 'copy', temp]
    return cmd, output, temp, list_path


def run_concat(files, output_path, runtime_dir, job=None, cancel_check=None):
    cmd, output, temp, list_path = build_concat_command(files, output_path, runtime_dir)
    try:
        _run(cmd, job=job, cancel_check=cancel_check)
        _commit_output(temp, output)
    finally:
        _cleanup(temp)
        _cleanup(list_path)
    return {'output_file': output, 'command': display_command(cmd)}


def run_bitwise_join(files, output_path, job=None, cancel_check=None):
    local_files = [validate_media_input(item) for item in files]
    output = os.path.abspath(local_path(output_path))
    if any(os.path.normcase(item) == os.path.normcase(output) for item in local_files):
        raise MediaError('Output must not overwrite an input file.')
    output, temp = _prepare_output(output)
    total = 0
    try:
        with open(temp, 'wb') as destination:
            for item in local_files:
                with open(item, 'rb') as source:
                    while True:
                        if cancel_check is not None and cancel_check():
                            raise MediaCancelled('Operation cancelled.')
                        chunk = source.read(4 * 1024 * 1024)
                        if not chunk:
                            break
                        destination.write(chunk)
                        total += len(chunk)
        _commit_output(temp, output)
    finally:
        _cleanup(temp)
    return {
        'output_file': output,
        'bytes': total,
        'command': 'bitwise concat (Python, no shell): ' + ' + '.join(local_files) + f' -> {output}',
    }


def run_transcode(input_path, output_path, codec, quality, preset, job=None, cancel_check=None):
    handbrake = find_handbrake()
    if not handbrake:
        raise MediaError('HandBrakeCLI was not found on PATH.')
    input_path = validate_media_input(input_path)
    output = os.path.abspath(local_path(output_path))
    if os.path.normcase(input_path) == os.path.normcase(output):
        raise MediaError('Output must not overwrite the input file.')
    output, temp = _prepare_output(output)
    cmd = [
        handbrake, '-i', input_path, '-o', temp,
        '-e', normalize_codec(codec), '-q', str(normalize_quality(quality)),
        '--encoder-preset', normalize_preset(preset),
        '--comb-detect', '--decomb', '-f', 'mp4', '--aencoder', 'av_aac',
    ]
    try:
        _run(cmd, job=job, cancel_check=cancel_check)
        _commit_output(temp, output)
    finally:
        _cleanup(temp)
    return {'output_file': output, 'command': display_command(cmd)}
