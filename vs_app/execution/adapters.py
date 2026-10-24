"""Execution layer: asyncio subprocess adapters with exact, pidfile-based cancellation.

Every command is an argv list handed to `asyncio.create_subprocess_exec`; no shell
ever parses it. On Windows the argv is prefixed with `wsl -e bash -lc '<script>'`
where the script records its own PID and then `exec`s the tool, so SIGINT/SIGTERM
reach the real process inside WSL.
"""

import asyncio
import os
import posixpath
import re
import shlex
import subprocess
import time
import uuid

from vs_app import settings
from vs_app.core import recovery as core
from vs_app.execution import bridge
from vs_app.execution.parsers import (
    ANSI_RE,
    DdrescueParser,
    PhotorecParser,
    fmt_duration,
)

LOGGER = settings.LOGGER
TESTDISK_TIMEOUT = 120


class StageFailed(RuntimeError):
    pass


class StageCancelled(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Process plumbing
# --------------------------------------------------------------------------- #
PIDFILE_RE = re.compile(r'^/tmp/video-studio-(\d+)-[0-9a-f]{32}\.pid$')

# Runs inside WSL with the orphaned pidfiles as "$@". Only a live process whose
# command is one of our tools is signalled, so a recycled PID is never touched.
# SIGINT first and a grace period so ddrescue writes its mapfile, then SIGTERM.
REAP_SCRIPT = r'''
for f in "$@"; do
  p=$(cat "$f" 2>/dev/null)
  if [ -n "$p" ] && kill -0 "$p" 2>/dev/null; then
    name=$(basename "$(tr '\0' '\n' < /proc/"$p"/cmdline 2>/dev/null | head -n 1)")
    case "$name" in
      ddrescue|photorec|testdisk)
        kill -INT "$p" 2>/dev/null
        i=0
        while kill -0 "$p" 2>/dev/null && [ $i -lt 15 ]; do sleep 1; i=$((i+1)); done
        kill -TERM "$p" 2>/dev/null
        echo "reaped $p $name";;
    esac
  fi
  rm -f "$f"
done
'''


def orphan_pidfiles(paths, owner_alive, own_pid=None):
    """Pidfiles whose owning backend process is gone (their tool may still be running)."""
    own_pid = os.getpid() if own_pid is None else own_pid
    orphans = []
    for path in paths:
        match = PIDFILE_RE.match(path.strip())
        if not match:
            continue
        owner = int(match.group(1))
        if owner != own_pid and not owner_alive(owner):
            orphans.append(match.group(0))
    return orphans


def reap_orphan_tools():
    """Stop ddrescue/PhotoRec/TestDisk left running by a backend that crashed or was killed.

    A survivor keeps writing the image and mapfile, and a new run on the same image
    would then have two writers. Returns the reaped "<pid> <tool>" lines.
    """
    try:
        listing = subprocess.run(
            bridge.run_linux(['sh', '-c', 'ls -1 /tmp/video-studio-*.pid 2>/dev/null']),
            capture_output=True, text=True, errors='replace', timeout=30)
    except Exception as exc:
        LOGGER.warning('orphan tool scan failed: %s', exc)
        return []
    orphans = orphan_pidfiles(listing.stdout.splitlines(), bridge.process_alive)
    if not orphans:
        return []
    try:
        result = subprocess.run(
            bridge.run_linux(['bash', '-c', REAP_SCRIPT, 'video-studio-reaper'] + orphans),
            capture_output=True, text=True, errors='replace', timeout=30 + 20 * len(orphans))
    except Exception as exc:
        LOGGER.warning('orphan tool reap failed: %s', exc)
        return []
    reaped = [line.strip() for line in result.stdout.splitlines()
              if line.startswith('reaped ')]
    for line in reaped:
        LOGGER.warning('Stopped orphaned tool from a previous backend: %s', line[7:])
    return reaped


def tracked_argv(argv, image=None):
    """Wrap a Linux tool so its exact PID is written inside WSL before exec.

    With `image`, the tool runs only while holding that image's lock; if another
    process holds it, the wrapper exits with IMAGE_BUSY_EXIT before the tool starts.
    """
    pidfile = f'/tmp/video-studio-{os.getpid()}-{uuid.uuid4().hex}.pid'
    command = ' '.join(shlex.quote(str(item)) for item in argv)
    script = f'echo $$ > {shlex.quote(pidfile)}; exec {command}'
    if image:
        script = core.image_lock_prelude(image) + '; ' + script
    return bridge.run_linux(['bash', '-lc', script]), pidfile


def raise_if_image_busy(returncode, image):
    if image and returncode == core.IMAGE_BUSY_EXIT:
        raise StageFailed(core.image_busy_error(image))


async def spawn(job, argv, image=None):
    if job.cancelled:
        # Cancel arrived during the stage's preparation, before anything was running.
        raise StageCancelled('Operation cancelled.')
    wrapper, pidfile = tracked_argv(argv, image)
    try:
        proc = await asyncio.create_subprocess_exec(
            *wrapper, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT)
    except FileNotFoundError as exc:
        raise StageFailed(f"Required executable not found: {exc.filename}") from exc
    job.attach_process(proc, pidfile)
    if job.cancelled:
        # Cancel arrived while the process was being created, when cancel_process had
        # nothing to signal yet: deliver it now.
        await cancel_process(job)
    return proc, pidfile


async def _remove_pidfile(pidfile):
    if not pidfile:
        return
    try:
        proc = await asyncio.create_subprocess_exec(
            *bridge.run_linux(['rm', '-f', pidfile]),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(proc.wait(), timeout=10)
    except Exception:
        pass


async def _stream_exit(proc, pidfile, argv, job, image=None):
    try:
        returncode = await proc.wait()
    finally:
        job.detach_process()
        await _remove_pidfile(pidfile)
    if job.cancelled:
        raise StageCancelled('Operation cancelled.')
    raise_if_image_busy(returncode, image)
    return returncode


async def cancel_process(job):
    """SIGINT (so ddrescue writes its mapfile), then SIGTERM, then kill the bridge."""
    proc, pidfile = job.process_state()
    if pidfile:
        target = shlex.quote(str(pidfile))
        script = (
            # The wrapper writes the pidfile a moment after it starts; wait up to 5 s.
            f'i=0; while [ ! -s {target} ] && [ $i -lt 50 ]; do sleep 0.1; i=$((i+1)); done\n'
            f'p=$(cat {target} 2>/dev/null) || exit 0\n'
            '[ -n "$p" ] || exit 0\n'
            'kill -INT "$p" 2>/dev/null\n'
            'sleep 1\n'
            'kill -TERM "$p" 2>/dev/null\n'
            f'rm -f {target}'
        )
        try:
            killer = await asyncio.create_subprocess_exec(
                *bridge.run_linux(['bash', '-lc', script]),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await asyncio.wait_for(killer.wait(), timeout=20)
        except Exception as exc:
            LOGGER.warning('Could not signal pidfile %s: %s', pidfile, exc)
    if proc is None or proc.returncode is not None:
        return
    try:
        proc.terminate()
    except Exception:
        pass
    if isinstance(proc, asyncio.subprocess.Process):
        try:
            await asyncio.wait_for(proc.wait(), timeout=8)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
    else:
        try:
            await asyncio.to_thread(proc.wait, 8)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


async def async_mkdir(path):
    try:
        proc = await asyncio.create_subprocess_exec(
            *bridge.run_linux(['mkdir', '-p', path]),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await proc.wait()
    except Exception as exc:
        LOGGER.warning('mkdir %s failed: %s', path, exc)


async def _lines(stream):
    while True:
        raw = await stream.readline()
        if not raw:
            break
        yield raw.decode('utf-8', 'replace').rstrip('\r\n')


async def _chunks(stream, size=65536):
    while True:
        chunk = await stream.read(size)
        if not chunk:
            break
        yield chunk


# --------------------------------------------------------------------------- #
# ddrescue
# --------------------------------------------------------------------------- #
class DdrescueAdapter:
    name = 'imaging'
    label = 'Imaging raw sectors (ddrescue)'

    def __init__(self, device, image, mode='normal', sector_size=None):
        self.device = device
        self.image = image
        self.argv = core.build_ddrescue_argv(device, image, mode, sector_size)

    async def run(self, job):
        started = time.time()
        job.update_progress('imaging', state='running', pct=0.0, eta=None,
                            detail='', phase='starting')
        await async_mkdir(posixpath.dirname(self.image) or '/')
        job.log('$ ' + bridge.command_text(self.argv))
        proc, pidfile = await spawn(job, self.argv, self.image)
        parser = DdrescueParser()
        state = {'rescued': None, 'rate': None, 'errors': 0}
        last_logged = None
        last_summary = time.time()
        try:
            async for line in _lines(proc.stdout):
                updates = parser.feed_line(line)
                if 'rescued' in updates:
                    state['rescued'] = updates['rescued']
                if 'rate' in updates:
                    state['rate'] = updates['rate']
                if 'errors' in updates:
                    state['errors'] = updates['errors']
                if updates:
                    updates.pop('detail', None)
                    if state['rescued']:
                        updates['detail'] = (
                            f"rescued {state['rescued']}"
                            + (f" · {state['rate']}" if state['rate'] else '')
                            + f" · read errors {state['errors']}")
                    job.update_progress('imaging', **updates)
                text = ANSI_RE.sub('', line).strip()
                if text.startswith(('Copying', 'Trimming', 'Scraping', 'Retrying')):
                    job.update_progress('imaging', phase=text)
                if updates and time.time() - last_summary >= 30:
                    last_summary = time.time()
                    progress = {'pct': updates.get('pct'), 'eta': updates.get('eta')}
                    if progress['pct'] is not None:
                        job.log(f"Imaging {progress['pct']:.2f}% · "
                                + (updates.get('detail') or '')
                                + (f" · ETA {progress['eta']}" if progress['eta'] else ''))
                if text and not parser.quiet_line(text) and text != last_logged:
                    last_logged = text
                    job.log(text)
        except asyncio.CancelledError:
            # Backend shutting down mid-run: stop the tool the safe way (SIGINT
            # first, so ddrescue saves its mapfile) instead of leaving it running.
            await cancel_process(job)
            raise
        finally:
            returncode = await _stream_exit(proc, pidfile, self.argv, job, self.image)

        seconds = round(time.time() - started, 1)
        exists = await asyncio.to_thread(bridge.linux_path_exists, self.image)
        if returncode != 0 and not exists:
            job.update_progress('imaging', state='failed', eta=None)
            raise StageFailed(
                f'ddrescue exited with code {returncode} and produced no image — see the log.')
        if returncode != 0:
            job.update_progress('imaging', state='partial', eta=None)
            job.log(f'ddrescue exited with code {returncode} (bad sectors remain) — '
                    'continuing with the partial image.')
            return {'state': 'partial', 'seconds': seconds,
                    'detail': f'image {self.image} (bad sectors remain)'}
        job.update_progress('imaging', state='completed', pct=100.0, eta=None)
        job.log(f'Imaging finished in {fmt_duration(seconds)} — image: {self.image}')
        return {'state': 'completed', 'seconds': seconds, 'detail': f'image {self.image}'}


# --------------------------------------------------------------------------- #
# TestDisk (read-only partition analysis; interactive undelete stays manual)
# --------------------------------------------------------------------------- #
class TestDiskAdapter:
    name = 'testdisk'
    label = 'Partition analysis (TestDisk)'

    def __init__(self, image, log_path):
        self.image = image
        self.log_path = log_path
        self.argv = ['testdisk', '/log', '/logname', log_path, '/list', image]

    async def run(self, job):
        started = time.time()
        job.update_progress('testdisk', state='running',
                            detail='reading the partition table')
        job.log('$ ' + bridge.command_text(self.argv))
        proc, pidfile = await spawn(job, self.argv, self.image)
        try:
            try:
                raw, _err = await asyncio.wait_for(proc.communicate(), TESTDISK_TIMEOUT)
            except asyncio.TimeoutError:
                job.log('TestDisk analysis timed out — continuing without it.')
                await cancel_process(job)
                return {'state': 'failed', 'seconds': round(time.time() - started, 1),
                        'detail': 'timed out (read-only analysis skipped)'}
        except asyncio.CancelledError:
            # Backend shutting down mid-run: stop the tool the safe way (SIGINT
            # first, so ddrescue saves its mapfile) instead of leaving it running.
            await cancel_process(job)
            raise
        finally:
            job.detach_process()
            await _remove_pidfile(pidfile)
        if job.cancelled:
            raise StageCancelled('Operation cancelled.')
        raise_if_image_busy(proc.returncode, self.image)
        text = ANSI_RE.sub('', (raw or b'').decode('utf-8', 'replace'))
        interesting = [line.strip() for line in text.splitlines()
                       if line.strip() and not line.strip().startswith('TestDisk ')]
        for line in interesting[-12:]:
            job.log(line[:300])
        seconds = round(time.time() - started, 1)
        if proc.returncode != 0:
            return {'state': 'failed', 'seconds': seconds,
                    'detail': f'testdisk exited with code {proc.returncode} (continuing)'}
        return {'state': 'completed', 'seconds': seconds,
                'detail': 'partition table analysed — log written beside the image'}


# --------------------------------------------------------------------------- #
# PhotoRec
# --------------------------------------------------------------------------- #
class PhotorecAdapter:
    name = 'carving'
    label = 'Carving files (PhotoRec, paranoid whole-space)'

    def __init__(self, image, recup_dir, categories=None):
        self.image = image
        self.recup_dir = recup_dir
        self.categories = categories
        self.argv = ['stdbuf', '-o0'] + core.build_photorec_argv(image, recup_dir, categories)

    async def run(self, job):
        started = time.time()
        if not await asyncio.to_thread(bridge.linux_path_exists, self.image):
            raise StageFailed(f'Image not found: {self.image} — run Step 1 (imaging) first.')
        job.update_progress('carving', state='running', pct=0.0, eta=None,
                            detail='', phase='starting')
        await async_mkdir(self.recup_dir)
        job.log('$ ' + bridge.command_text(self.argv))
        try:
            image_size = os.path.getsize(bridge.linux_to_local_path(self.image))
        except OSError:
            image_size = 0
        parser = PhotorecParser(image_size)
        last_pass = None
        last_logged_pct = None
        proc, pidfile = await spawn(job, self.argv, self.image)
        try:
            async for chunk in _chunks(proc.stdout):
                updates = parser.feed_bytes(chunk)
                if updates:
                    job.update_progress('carving', **updates)
                phase = updates.get('phase')
                if phase and phase != last_pass:
                    last_pass = phase
                    job.log(f'PhotoRec {phase}')
                if parser.summary_due(30):
                    job.update_progress('carving')
                    progress = updates or {}
                    pct = progress.get('pct', last_logged_pct)
                    if pct is not None:
                        last_logged_pct = pct
                        job.log(f'Carving {pct:.2f}% · {progress.get("detail", "")}'
                                + (f' · ETA {progress["eta"]}'
                                   if progress.get('eta') else ''))
        except asyncio.CancelledError:
            # Backend shutting down mid-run: stop the tool the safe way (SIGINT
            # first, so ddrescue saves its mapfile) instead of leaving it running.
            await cancel_process(job)
            raise
        finally:
            returncode = await _stream_exit(proc, pidfile, self.argv, job, self.image)

        seconds = round(time.time() - started, 1)
        if returncode != 0:
            job.update_progress('carving', state='partial', eta=None)
            job.log(f'photorec exited with code {returncode} — counting whatever was carved.')
            return {'state': 'partial', 'seconds': seconds,
                    'detail': f'photorec exit code {returncode}; files in {self.recup_dir}'}
        job.update_progress('carving', state='completed', pct=100.0, eta=None)
        job.log(f'Carving finished in {fmt_duration(seconds)} — files in {self.recup_dir}.1, .2, …')
        return {'state': 'completed', 'seconds': seconds,
                'detail': f'files in {self.recup_dir}.1, .2, …'}
