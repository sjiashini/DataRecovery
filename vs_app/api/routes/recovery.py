"""Recovery and repair endpoints: pipeline launch, progress, cancellation."""

import asyncio
import os
import posixpath
import subprocess

from fastapi import APIRouter

from vs_app import runtime, settings
from vs_app.api import policy
from vs_app.api.schemas import RecoverAllRequest, RepairRequest
from vs_app.core import recovery as core
from vs_app.execution import bridge
from vs_app.execution.adapters import cancel_process
from vs_app.execution.parsers import new_progress
from vs_app.orchestration.flows import RecoverySpec, recovery_flow, repair_flow
from vs_app.orchestration.gate import ENCODER, RECOVERY, release_all, try_acquire_all

router = APIRouter(prefix='/api')
LOGGER = settings.LOGGER
RECOVERY_BUSY = 'A recovery or repair is already running. Wait for it to finish.'
REPAIR_BUSY = ('Repair needs both the recovery and the encoder free. Wait for the running '
               'recovery, cut, join or transcode to finish.')


def _launch(coro):
    return runtime.launch(coro)


async def _snapshot(kind):
    snapshot = runtime.JOBS.snapshot(kind)
    if snapshot is None:
        snapshot = await runtime.STORE.latest_for_kind(kind)
    if snapshot is None:
        return {'active': False, 'stage': 'idle', 'progress': {}}
    snapshot['active'] = bool(snapshot.get('active'))
    return snapshot


# --------------------------------------------------------------------------- #
# Full pipeline
# --------------------------------------------------------------------------- #
@router.post('/recover_all')
async def recover_all(payload: RecoverAllRequest):
    await asyncio.to_thread(bridge.ensure_wsl_alive)
    roots = settings.SETTINGS.recovery_roots
    try:
        output = str(payload.output or '').strip()
        policy.require_recovery_output(output, 'Output image')
        image_wsl = bridge.win_to_wsl_path(output)
        policy.require_inside(image_wsl, roots, 'Output image')

        device = ''
        dd_argv = None
        sector = None
        if payload.skip_imaging:
            if not payload.carve:
                raise policy.PolicyError(
                    'Nothing to run: imaging is skipped and carving is disabled.')
            if not await asyncio.to_thread(bridge.linux_path_exists, image_wsl):
                raise policy.PolicyError(
                    f'Image not found: {image_wsl} — run Step 1 (imaging) first.')
        else:
            drives = await asyncio.to_thread(bridge.scan_recovery_drives)
            device = policy.validate_device(payload.device, drives)
            problem = await asyncio.to_thread(bridge.check_device_readable, device)
            if problem:
                raise policy.PolicyError(problem)
            sector = payload.sector_size
            if not sector:
                sector = next((item['sector_size'] for item in drives
                               if item['device'] == device), None)
                sector = sector or await asyncio.to_thread(bridge.detect_sector_size, device)
            dd_argv = core.build_ddrescue_argv(device, image_wsl, payload.mode, sector)
            problem = await asyncio.to_thread(bridge.image_source_problem, image_wsl, device)
            if problem:
                raise policy.PolicyError(problem)

        categories = policy.carve_categories(payload.file_types)
        recup_dir = str(payload.recup_dir or '').strip()
        if recup_dir:
            recup_dir = bridge.win_to_wsl_path(recup_dir)
        else:
            recup_dir = core.default_recup_dir(
                image_wsl, categories, settings.SETTINGS.photorec_carve_dir)
        policy.require_inside(recup_dir, roots, 'Carve folder')
        pc_argv = (core.build_photorec_argv(image_wsl, recup_dir, categories)
                   if payload.carve else None)
        if await asyncio.to_thread(bridge.image_busy, image_wsl):
            raise policy.PolicyError(core.image_busy_error(image_wsl))
    except policy.PolicyError as exc:
        return {'success': False, 'error': str(exc)}

    if not RECOVERY.try_acquire():
        return {'success': False, 'error': RECOVERY_BUSY}
    job = runtime.JOBS.create('recovery', fields={
        'device': device,
        'image': image_wsl,
        'recup_dir': recup_dir,
        'carve': payload.carve,
        'skip_imaging': payload.skip_imaging,
        'mode': payload.mode,
        'file_types': categories,
        'files_recovered': None,
        'set_aside': None,
        'progress': {
            'imaging': new_progress('skipped' if payload.skip_imaging else 'pending'),
            'carving': new_progress('pending' if payload.carve else 'off'),
        },
        'commands': {
            'ddrescue': bridge.command_text(dd_argv),
            'photorec': bridge.command_text(pc_argv),
        },
    })
    if job is None:
        RECOVERY.release()
        return {'success': False, 'error': 'A recovery job is already active.'}

    job.add_stage('imaging', 'Imaging raw sectors (ddrescue)', 'abort')
    job.add_stage('testdisk', 'Partition analysis (TestDisk)', 'continue')
    job.add_stage('carving', 'Carving files (PhotoRec)', 'report')
    job.log(f"Recovery started · device={device or '—'} · mode={payload.mode} · "
            f"carve={payload.carve}" + (' · imaging skipped' if payload.skip_imaging else ''))
    if categories:
        job.log('File types: ' + ', '.join(
            core.CARVE_CATEGORIES[cat]['label'] for cat in categories))
    elif payload.carve:
        job.log('File types: ALL (every format PhotoRec knows)')
    for label in ('ddrescue', 'photorec'):
        if job.commands.get(label):
            job.log('$ ' + job.commands[label])

    testdisk_log = posixpath.join(
        posixpath.dirname(image_wsl) or '/mnt/c/PendriveRecovery', 'testdisk.log')
    spec = RecoverySpec(
        device=device, image=image_wsl, recup_dir=recup_dir,
        carve=payload.carve, skip_imaging=payload.skip_imaging,
        mode=payload.mode, sector_size=sector,
        file_types=categories, run_testdisk=payload.run_testdisk,
        testdisk_log=testdisk_log)
    if device and not await asyncio.to_thread(bridge.read_source_record, image_wsl):
        identity = await asyncio.to_thread(bridge.device_identity, device)
        await asyncio.to_thread(bridge.write_source_record, image_wsl, device, identity)
    _launch(recovery_flow(job, spec))

    if payload.skip_imaging:
        message = f'Carving from {image_wsl} with PhotoRec (no re-imaging).'
    else:
        tail = ' then carving with PhotoRec.' if payload.carve else ' (imaging only).'
        message = f'Imaging {device} → {image_wsl}{tail}'
    return {'success': True, 'message': message, 'image': image_wsl,
            'recup_dir': recup_dir, 'commands': job.commands,
            'job_id': job.id, 'events_url': f'/api/jobs/{job.id}/events'}


@router.get('/recovery_progress')
async def recovery_progress():
    return await _snapshot('recovery')


@router.post('/recovery_cancel')
async def recovery_cancel():
    job = runtime.JOBS.current('recovery')
    if job is None or not job.active:
        return {'success': False, 'error': 'No active recovery job.'}
    job.request_cancel()
    await cancel_process(job)
    stage = job.public(include_log=False).get('stage')
    if stage == 'imaging':
        job.log('Cancel requested (SIGINT sent — ddrescue saves its mapfile state).')
    else:
        job.log('Cancel requested (SIGINT sent — PhotoRec stops and keeps carved files).')
    return {'success': True,
            'message': 'Cancellation requested. The partial image is kept and resumable '
                       'at the same path.',
            'image': job.public(include_log=False).get('image'),
            'job_id': job.id}


# --------------------------------------------------------------------------- #
# Repair & salvage
# --------------------------------------------------------------------------- #
@router.post('/repair_videos')
async def repair_videos(payload: RepairRequest):
    image = str(payload.image or payload.output or '').strip()
    if not image:
        return {'success': False,
                'error': 'Set the image path first — the carved folder is located beside it.'}
    image_local = bridge.linux_to_local_path(bridge.win_to_wsl_path(image))
    roots = settings.SETTINGS.recovery_roots
    try:
        policy.require_within(image_local, roots, 'Image')
        ffmpeg, ffprobe = bridge.find_ffmpeg()
        if not ffmpeg:
            raise policy.PolicyError(
                'ffmpeg not found on Windows. Install it (winget install ffmpeg) and retry.')
        base = policy.carve_folder_name(
            payload.carve_base or settings.SETTINGS.photorec_carve_dir)
        parent_local = os.path.dirname(image_local) or '.'
        videos = await asyncio.to_thread(bridge.discover_carved_videos, parent_local, base)
        if not videos:
            raise policy.PolicyError(
                f"No carved videos found beside {image} in folders starting '{base}'. "
                'Image the drive and carve first.')
        out_local = os.path.join(parent_local, base + '_repaired')
        policy.require_within(out_local, roots, 'Repair output')
        reference_local = None
        if payload.reference:
            reference_local = policy.media_input(payload.reference)
    except policy.PolicyError as exc:
        return {'success': False, 'error': str(exc)}

    if not try_acquire_all(RECOVERY, ENCODER):
        return {'success': False, 'error': REPAIR_BUSY}
    job = runtime.JOBS.create('repair', fields={
        'image': bridge.win_to_wsl_path(image),
        'out_dir': bridge.win_to_wsl_path(out_local),
        'total': len(videos), 'processed': 0, 'clean': 0, 'salvaged': 0,
        'rebuilt': 0, 'hopeless': 0, 'pct': 0.0, 'detail': '',
        'progress': {'repair': {'state': 'pending', 'pct': 0.0, 'eta': None,
                                'detail': '', 'phase': '', 'seconds': None}},
    })
    if job is None:
        release_all(RECOVERY, ENCODER)
        return {'success': False, 'error': 'A repair job is already active.'}
    job.add_stage('repair', 'Repair & salvage (ffmpeg + untrunc)', 'report')
    job.log(f"Repair started · {len(videos)} video(s) in '{base}*' folders · ffmpeg: {ffmpeg}")
    job.log('untrunc: ' + (bridge.find_untrunc()
                           or 'not installed — headerless carves get re-encode salvage only'))
    job.log(f'Output (originals never touched): {bridge.win_to_wsl_path(out_local)}')
    try:
        await asyncio.to_thread(os.makedirs, out_local, exist_ok=True)
    except OSError as exc:
        job.finish('failed', error=str(exc))
        release_all(RECOVERY, ENCODER)
        return {'success': False, 'error': f'Could not create output folder: {exc}'}
    _launch(repair_flow(job, videos, ffmpeg, ffprobe, out_local, reference_local))
    return {'success': True,
            'message': f'Repairing {len(videos)} carved video(s). Live progress is streaming.',
            'out_dir': bridge.win_to_wsl_path(out_local), 'total': len(videos),
            'job_id': job.id, 'events_url': f'/api/jobs/{job.id}/events'}


@router.get('/repair_progress')
async def repair_progress():
    return await _snapshot('repair')


@router.post('/repair_cancel')
async def repair_cancel():
    job = runtime.JOBS.current('repair')
    if job is None or not job.active:
        return {'success': False, 'error': 'No active repair job.'}
    job.request_cancel()
    await cancel_process(job)
    job.log('Repair cancel requested — stopping the current file.')
    return {'success': True,
            'message': 'Cancellation requested. Files already repaired are kept.',
            'job_id': job.id}


# --------------------------------------------------------------------------- #
# Legacy single-tool endpoints (kept for scripts; prefer /recover_all)
# --------------------------------------------------------------------------- #
@router.post('/ddrescue')
def ddrescue(payload: RecoverAllRequest):
    try:
        output = str(payload.output or '').strip()
        policy.require_recovery_output(output, 'Output image')
        device = policy.validate_device(payload.device)
        problem = bridge.check_device_readable(device)
        if problem:
            raise policy.PolicyError(problem)
        sector = payload.sector_size or bridge.detect_sector_size(device)
        image_wsl = bridge.win_to_wsl_path(output)
        policy.require_inside(image_wsl, settings.SETTINGS.recovery_roots, 'Output image')
        argv = core.build_ddrescue_argv(device, image_wsl, payload.mode, sector)
        command = bridge.command_text(argv)
        problem = bridge.image_source_problem(image_wsl, device)
        if problem:
            raise policy.PolicyError(problem)
        if bridge.image_busy(image_wsl):
            raise policy.PolicyError(core.image_busy_error(image_wsl))
    except policy.PolicyError as exc:
        return {'success': False, 'error': str(exc)}
    if not RECOVERY.try_acquire():
        return {'success': False, 'error': RECOVERY_BUSY}
    try:
        bridge.linux_mkdir(os.path.dirname(image_wsl) or '/')
        if not bridge.read_source_record(image_wsl):
            bridge.write_source_record(image_wsl, device, bridge.device_identity(device))
        result = subprocess.run(bridge.run_linux(core.locked_argv(argv, image_wsl)),
                                capture_output=True, text=True, errors='replace')
        if result.returncode == 0:
            return {'success': True, 'command': command,
                    'message': f"ddrescue completed: '{bridge.win_to_wsl_path(output)}'"}
        return {'success': False, 'command': command,
                'error': (result.stdout or result.stderr
                          or f'ddrescue exited with code {result.returncode}')[-2000:]}
    except FileNotFoundError as exc:
        return {'success': False, 'command': command,
                'error': f"'{exc.filename}' not found. Inside WSL: "
                         'sudo apt install -y gddrescue testdisk'}
    except Exception as exc:
        return {'success': False, 'command': command, 'error': str(exc)}
    finally:
        RECOVERY.release()


@router.post('/photorec')
def photorec(payload: RecoverAllRequest):
    try:
        image = str(payload.image or '').strip()
        if not image:
            raise policy.PolicyError(
                'Image path is required — create one with ddrescue first.')
        image_wsl = bridge.win_to_wsl_path(image)
        roots = settings.SETTINGS.recovery_roots
        policy.require_within(image_wsl, roots, 'Image')
        if not bridge.linux_path_exists(image_wsl):
            raise policy.PolicyError(f'Image not found: {image_wsl}')
        categories = policy.carve_categories(payload.file_types)
        recup = str(payload.recup_dir or '').strip()
        recup_dir = (bridge.win_to_wsl_path(recup) if recup else
                     core.default_recup_dir(image_wsl, categories,
                                            settings.SETTINGS.photorec_carve_dir))
        policy.require_inside(recup_dir, roots, 'Carve folder')
        argv = core.build_photorec_argv(image_wsl, recup_dir, categories)
        command = bridge.command_text(argv)
        if bridge.image_busy(image_wsl):
            raise policy.PolicyError(core.image_busy_error(image_wsl))
    except policy.PolicyError as exc:
        return {'success': False, 'error': str(exc)}
    if not RECOVERY.try_acquire():
        return {'success': False, 'error': RECOVERY_BUSY}
    try:
        bridge.linux_mkdir(recup_dir)
        result = subprocess.run(bridge.run_linux(core.locked_argv(argv, image_wsl)),
                                capture_output=True, text=True, errors='replace')
        if result.returncode == core.IMAGE_BUSY_EXIT:
            return {'success': False, 'command': command,
                    'error': core.image_busy_error(image_wsl)}
        if categories:
            bridge.set_aside_unselected_office(recup_dir, categories)
        count = bridge.count_recovered_files(recup_dir)
        ok = result.returncode == 0 or (count or 0) > 0
        return {
            'success': ok, 'command': command, 'recup_dir': recup_dir,
            'files_recovered': count,
            'message': (f"PhotoRec finished — {count} file(s) carved into '{recup_dir}'"
                        if ok else None),
            'error': None if ok else (result.stdout or result.stderr
                                      or 'photorec failed')[-2000:],
        }
    except FileNotFoundError as exc:
        return {'success': False, 'command': command,
                'error': f"'{exc.filename}' not found — inside WSL: "
                         'sudo apt install -y testdisk'}
    except Exception as exc:
        return {'success': False, 'command': command, 'error': str(exc)}
    finally:
        RECOVERY.release()
