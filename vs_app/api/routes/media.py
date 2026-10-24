"""Media operations (cancellable jobs serialized by the ENCODER lock) and uploads."""

import asyncio
import os
import posixpath

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from vs_app import runtime, settings
from vs_app.api import policy
from vs_app.api.schemas import CutRequest, JoinRequest, TranscodeRequest
from vs_app.core import recovery as core
from vs_app.execution import bridge, media
from vs_app.execution.adapters import cancel_process
from vs_app.orchestration.flows import media_flow
from vs_app.orchestration.gate import ENCODER

router = APIRouter(prefix='/api')
MAX_UPLOAD_BYTES = 8 * 1024 ** 3


BUSY_ERROR = 'Another cut, join, transcode or repair is running. Wait for it or cancel it.'
NVENC_HINT = ('\n\nNVENC driver mismatch: this ffmpeg requires a newer NVIDIA driver. '
              'Update the display driver, or use "Instant Lossless Stream Copy" mode.')


def _refuse_inside_active_recovery(path):
    """Media jobs may run beside a recovery, but never write into its folder.

    That folder holds the image being written and the carve folders PhotoRec is
    filling right now.
    """
    job = runtime.JOBS.current('recovery')
    image = job.fields.get('image') if job is not None and job.active else None
    if not image:
        return
    folder = bridge.linux_to_local_path(posixpath.dirname(image))
    if policy.within(path, [folder]):
        raise policy.PolicyError(
            f'{folder} belongs to the recovery running now (its image and carve folders). '
            'Save the output somewhere else, or wait for the recovery to finish.')


async def _run_media_job(operation, label, run, kwargs, describe):
    """Run one media operation as a cancellable `media` job and wait for its outcome.

    The response keeps the old synchronous shape (success/output_file/command/message),
    plus job_id; /api/media_cancel or /api/jobs/{id}/cancel stops it meanwhile.
    """
    if not ENCODER.try_acquire():
        return {'success': False, 'error': BUSY_ERROR}
    job = runtime.JOBS.create('media', fields={
        'operation': operation, 'output': kwargs.get('output_path'),
        'output_file': None, 'command': None})
    if job is None:
        ENCODER.release()
        return {'success': False, 'error': BUSY_ERROR}
    job.add_stage(operation, label, 'abort')
    job.log(f'{label} started -> {kwargs.get("output_path")}')
    # Shielded: a dropped browser request must not cancel the job; Cancel does that.
    outcome, value = await asyncio.shield(
        runtime.launch(media_flow(job, operation, run, kwargs)))
    if outcome == 'completed':
        return {'success': True, 'command': value['command'],
                'output_file': value['output_file'], 'message': describe(value),
                'job_id': job.id}
    if outcome == 'cancelled':
        return {'success': False, 'cancelled': True, 'error': 'Operation cancelled.',
                'job_id': job.id}
    return {'success': False, 'error': value, 'job_id': job.id}


@router.post('/cut')
async def cut(payload: CutRequest):
    try:
        input_path = policy.media_input(payload.input)
        policy.require_media_output(payload.output, 'Output')
        _refuse_inside_active_recovery(payload.output)
    except policy.PolicyError as exc:
        return {'success': False, 'error': str(exc)}
    result = await _run_media_job(
        'cut', 'Cut (ffmpeg)', media.run_cut,
        {'input_path': input_path, 'output_path': payload.output, 'start': payload.start,
         'end': payload.end, 'mode': payload.mode},
        lambda value: f"Trimmed video saved to '{value['output_file']}'")
    lowered = str(result.get('error') or '').lower()
    if ('nvenc' in lowered and ('driver does not support' in lowered
                                or 'minimum required nvidia driver' in lowered)):
        result['error'] += NVENC_HINT
    return result


@router.post('/join')
async def join(payload: JoinRequest):
    strategy, join_error = core.join_strategy(payload.files, payload.mode)
    if join_error:
        return {'success': False, 'error': join_error}
    try:
        inputs = [policy.media_input(item) for item in payload.files]
        policy.require_media_output(payload.output, 'Output')
        _refuse_inside_active_recovery(payload.output)
    except policy.PolicyError as exc:
        return {'success': False, 'error': str(exc)}
    def describe(value):
        return f"Joined {len(inputs)} files into '{value['output_file']}'"

    if strategy == 'bitwise':
        return await _run_media_job(
            'join', 'Join (bitwise copy)', media.run_bitwise_join,
            {'files': inputs, 'output_path': payload.output}, describe)
    return await _run_media_job(
        'join', 'Join (ffmpeg concat)', media.run_concat,
        {'files': inputs, 'output_path': payload.output,
         'runtime_dir': settings.SETTINGS.runtime_dir}, describe)


@router.post('/transcode')
async def transcode(payload: TranscodeRequest):
    try:
        input_path = policy.media_input(payload.input)
        policy.require_media_output(payload.output, 'Output')
        _refuse_inside_active_recovery(payload.output)
    except policy.PolicyError as exc:
        return {'success': False, 'error': str(exc)}
    return await _run_media_job(
        'transcode', 'Transcode (HandBrakeCLI)', media.run_transcode,
        {'input_path': input_path, 'output_path': payload.output,
         'codec': payload.codec or settings.SETTINGS.handbrake_encoder,
         'quality': (payload.quality if payload.quality is not None
                     else settings.SETTINGS.handbrake_cq),
         'preset': payload.preset},
        lambda value: f"HandBrake transcode completed: '{value['output_file']}'")


@router.get('/media_progress')
async def media_progress():
    job = runtime.JOBS.current('media')
    if job is None:
        return {'active': False, 'stage': 'idle'}
    return job.public()


@router.post('/media_cancel')
async def media_cancel():
    job = runtime.JOBS.current('media')
    if job is None or not job.active:
        return {'success': False, 'error': 'No cut, join or transcode is running.'}
    job.request_cancel()
    await cancel_process(job)
    job.log('Cancel requested - stopping the media tool.')
    return {'success': True, 'message': 'Cancellation requested.', 'job_id': job.id}


@router.post('/upload')
async def upload(request: Request):
    length = request.headers.get('content-length')
    try:
        length = int(length) if length else 0
    except ValueError:
        length = 0
    if length <= 0:
        return JSONResponse({'success': False, 'error': 'Upload body is empty.'}, status_code=400)
    if length > MAX_UPLOAD_BYTES:
        return JSONResponse({'success': False, 'error': 'Upload is larger than 8 GB.'},
                            status_code=413)
    filename = (request.query_params.get('filename')
                or request.headers.get('x-filename') or 'uploaded_video.mp4')
    filename = os.path.basename(str(filename)).strip() or 'uploaded_video.mp4'
    if os.path.splitext(filename)[1].lstrip('.').lower() not in core.VIDEO_EXTS:
        return JSONResponse({'success': False, 'error': 'Only video files can be uploaded.'},
                            status_code=400)
    uploads_dir = os.path.join(settings.SETTINGS.base_dir, 'uploads')
    try:
        os.makedirs(uploads_dir, exist_ok=True)
    except OSError as exc:
        return JSONResponse({'success': False, 'error': f'Cannot create uploads folder: {exc}'},
                            status_code=500)
    destination = os.path.join(uploads_dir, filename)
    temp_path = destination + '.__vs_partial__'
    written = 0
    try:
        with open(temp_path, 'wb') as handle:
            async for chunk in request.stream():
                if not chunk:
                    continue
                handle.write(chunk)
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise ValueError('Upload is larger than 8 GB.')
        if written == 0:
            os.remove(temp_path)
            return JSONResponse({'success': False, 'error': 'Upload was interrupted.'},
                                status_code=400)
        os.replace(temp_path, destination)
    except Exception as exc:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
        return JSONResponse({'success': False, 'error': f'Upload failed: {exc}'},
                            status_code=500)
    size_mb = round(os.path.getsize(destination) / (1024 * 1024), 2)
    return {'success': True, 'path': destination, 'filename': filename, 'size_mb': size_mb,
            'message': f"Uploaded '{filename}' ({size_mb} MB)."}
