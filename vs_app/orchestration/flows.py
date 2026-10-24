"""Concrete pipelines: ddrescue → TestDisk → PhotoRec, and video repair/salvage."""

import asyncio
import os
import time
from dataclasses import dataclass, field

from vs_app import settings
from vs_app.execution import media
from vs_app.execution import bridge
from vs_app.execution.adapters import (
    DdrescueAdapter,
    PhotorecAdapter,
    StageCancelled,
    TestDiskAdapter,
    cancel_process,
)
from vs_app.execution.parsers import fmt_duration
from vs_app.orchestration.gate import ENCODER, RECOVERY, release_all
from vs_app.orchestration.pipeline import Pipeline, Stage

LOGGER = settings.LOGGER


@dataclass
class RecoverySpec:
    device: str = ''
    image: str = ''
    recup_dir: str = ''
    carve: bool = True
    skip_imaging: bool = False
    mode: str = 'normal'
    sector_size: int = None
    file_types: list = None
    run_testdisk: bool = True
    testdisk_log: str = field(default='')


async def recovery_flow(job, spec):
    try:
        job.start()
        stages = []
        if spec.skip_imaging:
            stages.append(Stage('imaging', 'Imaging raw sectors (ddrescue)', None,
                                'abort', 're-using the existing image'))
            job.progress.setdefault('imaging', {}).update(state='skipped')
            if not await asyncio.to_thread(bridge.linux_path_exists, spec.image):
                job.finish('failed',
                           error=f'Image not found: {spec.image} — run Step 1 (imaging) first.')
                return
            job.log('Imaging skipped — reusing existing image: ' + spec.image)
        else:
            stages.append(Stage(
                'imaging', 'Imaging raw sectors (ddrescue)',
                DdrescueAdapter(spec.device, spec.image, spec.mode, spec.sector_size),
                'abort'))

        if spec.run_testdisk:
            stages.append(Stage(
                'testdisk', 'Partition analysis (TestDisk)',
                TestDiskAdapter(spec.image, spec.testdisk_log), 'continue'))
        else:
            stages.append(Stage('testdisk', 'Partition analysis (TestDisk)', None,
                                'continue', 'disabled for this run'))

        if spec.carve:
            stages.append(Stage(
                'carving', 'Carving files (PhotoRec, paranoid whole-space)',
                PhotorecAdapter(spec.image, spec.recup_dir, spec.file_types), 'report'))
        else:
            stages.append(Stage('carving', 'Carving files (PhotoRec)', None,
                                'report', 'imaging-only run — use Step 2 later'))
            job.progress.setdefault('carving', {}).update(state='off')

        status = await Pipeline(stages).run(job)
        if status is None:
            return

        if spec.carve:
            if spec.file_types:
                try:
                    moved, other = await asyncio.to_thread(
                        bridge.set_aside_unselected_office, spec.recup_dir, spec.file_types)
                    if moved:
                        job.set_fields(set_aside={'count': moved, 'folder': other})
                        job.log(f'{moved} Office/zip file(s) of unselected types were moved '
                                f'(not deleted) to {other}')
                except Exception as exc:
                    job.log(f'Could not separate unselected Office types: {exc}')
            count = await asyncio.to_thread(bridge.count_recovered_files, spec.recup_dir)
            job.set_fields(files_recovered=count)
            job.log(f'{count} file(s) carved into {spec.recup_dir}.1, .2, …')
        else:
            job.log('Imaging-only run — use Step 2 to carve from this image later.')

        job.log('Recovery completed.' if status == 'completed'
                else 'Recovery finished with warnings.')
        await asyncio.to_thread(bridge.wsl_sync)
        job.finish(status)
    except StageCancelled:
        job.finish('cancelled')
    except Exception as exc:
        LOGGER.exception('Recovery job failed')
        job.log('ERROR ' + str(exc))
        job.finish('failed', error=str(exc))
    finally:
        RECOVERY.release()


# --------------------------------------------------------------------------- #
# Repair & salvage
# --------------------------------------------------------------------------- #
def _capture(job, cmd):
    return media.capture(cmd, job=job, cancel_check=lambda: job.cancelled)


def pick_reference(videos, ffmpeg, ffprobe, job):
    """First carved video that opens with no errors and reports a real duration."""
    if not ffprobe:
        return None
    for video in videos:
        if job.cancelled:
            return None
        try:
            _rc, _out, err = _capture(
                job, [ffmpeg, '-v', 'error', '-i', video['local'], '-f', 'null', '-'])
            if (err or '').strip():
                continue
            duration = bridge.output_duration(ffprobe, video['local'])
            if duration and float(duration) > 0.5:
                return video['local']
        except media.MediaCancelled:
            return None
        except Exception:
            continue
    return None


def repair_worker(job, videos, ffmpeg, ffprobe, out_local, reference_local=None):
    counters = {'processed': 0, 'clean': 0, 'salvaged': 0, 'rebuilt': 0, 'hopeless': 0}
    try:
        job.start()
        job.start_stage('repair')
        reference = reference_local
        reference_tried = bool(reference)
        untrunc = bridge.find_untrunc()
        headerless_seen = False
        total = len(videos)
        for index, video in enumerate(videos):
            if job.cancelled:
                job.log('Repair cancelled. Files already repaired are kept.')
                job.finish('cancelled')
                return
            name, local = video['name'], video['local']
            counters['processed'] = index + 1
            job.set_fields(**counters, pct=round(100.0 * (index + 1) / total, 2),
                           detail=f'checking {name}')
            _rc, _out, probe_err = _capture(
                job, [ffmpeg, '-v', 'error', '-i', local, '-f', 'null', '-'])
            stderr = probe_err or ''
            if not stderr.strip():
                counters['clean'] += 1
                job.set_fields(**counters)
                job.log(f"[{index + 1}/{total}] CLEAN    {name}")
                continue

            # Carved names repeat across carved.1 / carved.2, so always prefix the folder.
            out_path = os.path.join(out_local, f"{video['label']}__{name}")
            temp_path = bridge.atomic_target(out_path)
            if os.path.exists(temp_path):
                os.remove(temp_path)

            headerless = ('moov atom not found' in stderr
                          or 'Invalid data found when processing input' in stderr)
            if headerless and bridge.has_moov(local):
                headerless = False
            if headerless and not untrunc and not headerless_seen:
                headerless_seen = True
                job.log('Headerless carve(s) found. Install untrunc for a lossless index '
                        'rebuild — falling back to re-encode salvage.')
            if headerless and untrunc:
                if not reference and not reference_tried:
                    reference_tried = True
                    reference = pick_reference(videos, ffmpeg, ffprobe, job)
                    if reference:
                        job.log('Reference for index rebuild: ' + os.path.basename(reference))
                if reference and os.path.abspath(reference) != os.path.abspath(local):
                    _capture(job, [untrunc, '-o', temp_path, reference, local])
                    rebuilt = bridge.output_duration(ffprobe, temp_path)
                    if rebuilt and os.path.getsize(temp_path) > 65536:
                        os.replace(temp_path, out_path)
                        counters['rebuilt'] += 1
                        job.set_fields(**counters)
                        job.log(f"[{index + 1}/{total}] REBUILD  {name} -> "
                                f'{rebuilt} s (index rebuilt by untrunc)')
                        continue
                    if os.path.exists(temp_path):
                        os.remove(temp_path)

            _capture(job, [
                ffmpeg, '-y', '-v', 'error', '-err_detect', 'ignore_err', '-i', local,
                '-map', '0:v:0', '-map', '0:a:0?', '-c:v', 'libx264', '-preset', 'veryfast',
                '-crf', '23', '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart',
                temp_path])
            duration = bridge.output_duration(ffprobe, temp_path)
            if duration and os.path.getsize(temp_path) > 65536:
                os.replace(temp_path, out_path)
                counters['salvaged'] += 1
                job.set_fields(**counters)
                job.log(f"[{index + 1}/{total}] SALVAGE  {name} -> {duration} s playable")
                continue
            if os.path.exists(temp_path):
                os.remove(temp_path)
            counters['hopeless'] += 1
            job.set_fields(**counters)
            reason = ('headerless, index rebuild and re-encode both failed' if headerless
                      else 'corrupt bitstream, no decodable frames')
            job.log(f"[{index + 1}/{total}] HOPELESS {name} ({reason})")

        status = 'partial' if counters['hopeless'] else 'completed'
        detail = (f"clean {counters['clean']} · salvaged {counters['salvaged']} · "
                  f"rebuilt {counters['rebuilt']} · hopeless {counters['hopeless']}")
        job.finish_stage('repair', status, detail=detail)
        job.log('Repair complete — ' + detail)
        job.finish(status)
    except media.MediaCancelled:
        job.log('Repair cancelled. Files already repaired are kept.')
        job.finish('cancelled')
    except Exception as exc:
        LOGGER.exception('Repair job failed')
        job.log('ERROR ' + str(exc))
        job.finish('failed', error=str(exc))


async def repair_flow(job, videos, ffmpeg, ffprobe, out_local, reference_local=None):
    try:
        await asyncio.to_thread(repair_worker, job, videos, ffmpeg, ffprobe,
                                out_local, reference_local)
    except asyncio.CancelledError:
        # Backend shutting down: the worker thread can't be cancelled, so tell it to
        # stop and end the ffmpeg it is running, rather than leaving both behind.
        job.request_cancel()
        await cancel_process(job)
        raise
    finally:
        release_all(RECOVERY, ENCODER)


# --------------------------------------------------------------------------- #
# Cut / join / transcode
# --------------------------------------------------------------------------- #
async def media_flow(job, operation, run, kwargs):
    """Run one blocking media operation as a cancellable job.

    Returns ('completed', result), ('cancelled', None) or ('failed', message). Cancel
    works through the job: job.request_cancel() plus cancel_process() stops the
    running ffmpeg/HandBrakeCLI, and media.capture turns that into MediaCancelled.
    """
    try:
        job.start()
        job.start_stage(operation)
        started = time.time()
        try:
            result = await asyncio.to_thread(
                run, job=job, cancel_check=lambda: job.cancelled, **kwargs)
        except media.MediaCancelled:
            job.finish_stage(operation, 'cancelled', seconds=time.time() - started)
            job.log('Cancelled. The partial output was removed; inputs are untouched.')
            job.finish('cancelled')
            return 'cancelled', None
        except media.MediaError as exc:
            job.finish_stage(operation, 'failed', detail=str(exc), seconds=time.time() - started)
            job.log('ERROR ' + str(exc))
            job.finish('failed', error=str(exc))
            return 'failed', str(exc)
        job.set_fields(output_file=result.get('output_file'), command=result.get('command'))
        job.finish_stage(operation, 'completed', detail=result.get('output_file'),
                         seconds=time.time() - started)
        job.log('Saved ' + str(result.get('output_file')))
        job.finish('completed')
        return 'completed', result
    except asyncio.CancelledError:
        # Backend shutting down: stop the media tool rather than leaving it running.
        job.request_cancel()
        await cancel_process(job)
        raise
    except Exception as exc:
        LOGGER.exception('Media job failed')
        job.finish('failed', error=str(exc))
        return 'failed', str(exc)
    finally:
        ENCODER.release()


# --------------------------------------------------------------------------- #
# Helpers shared by routes
# --------------------------------------------------------------------------- #
def media_capture(job, cmd):
    return _capture(job, cmd)


def format_roots(roots):
    return '; '.join(roots)


def elapsed_text(started):
    return fmt_duration(round(time.time() - started, 1))
