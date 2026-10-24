"""Sequential pipeline engine with a failure policy per stage."""

import time

from vs_app import settings
from vs_app.execution.adapters import StageCancelled, StageFailed

LOGGER = settings.LOGGER


class Stage:
    """One adapter invocation plus what to do when it fails."""

    def __init__(self, name, label, adapter, policy='report', skip_detail=None):
        self.name = name
        self.label = label
        self.adapter = adapter
        self.policy = policy
        self.skip_detail = skip_detail


class Pipeline:
    def __init__(self, stages):
        self.stages = list(stages)

    async def run(self, job):
        partial = False
        total = len(self.stages)
        for index, stage in enumerate(self.stages, 1):
            if job.cancelled:
                job.log('Cancel requested — stopping before ' + stage.label + '.')
                job.finish('cancelled')
                return None
            if stage.adapter is None:
                job.finish_stage(stage.name, 'skipped', detail=stage.skip_detail or '')
                continue

            job.start_stage(stage.name)
            job.log(f'Stage {index}/{total} — {stage.label}')
            started = time.time()
            try:
                result = await stage.adapter.run(job)
            except StageCancelled:
                job.finish_stage(stage.name, 'cancelled',
                                 seconds=round(time.time() - started, 1))
                job.log('Cancelled during ' + stage.label.lower() + '. '
                        'Work already completed is kept.')
                job.finish('cancelled')
                return None
            except StageFailed as exc:
                message = str(exc)
                job.finish_stage(stage.name, 'failed', detail=message,
                                 seconds=round(time.time() - started, 1))
                job.log('ERROR ' + message)
                if stage.policy == 'abort':
                    job.finish('failed', error=message)
                    return None
                if stage.policy == 'report':
                    partial = True
                job.log(f'{stage.label} failed — continuing (policy: {stage.policy}).')
                continue
            except Exception as exc:
                LOGGER.exception('Stage %s failed unexpectedly', stage.name)
                message = str(exc) or exc.__class__.__name__
                job.finish_stage(stage.name, 'failed', detail=message,
                                 seconds=round(time.time() - started, 1))
                job.log('ERROR ' + message)
                if stage.policy == 'abort':
                    job.finish('failed', error=message)
                    return None
                partial = True
                continue

            state = result.get('state', 'completed')
            job.finish_stage(stage.name, state, detail=result.get('detail'),
                             seconds=result.get('seconds'))
            if state == 'partial':
                partial = True
        return 'partial' if partial else 'completed'
