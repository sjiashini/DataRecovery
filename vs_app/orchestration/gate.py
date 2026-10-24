"""Non-blocking locks for the two resources long-running jobs compete for.

RECOVERY  the source device, its image and its carve folders: recovery, the legacy
          ddrescue/photorec routes, and repair (which reads carve output).
ENCODER   ffmpeg/HandBrakeCLI work: cut, join, transcode, and repair.

Imaging a drive and transcoding a video use different resources, so they may run
at the same time; repair needs both. Acquire with try_acquire_all() and release the
same gates in a `finally`.
"""

import threading


class OperationGate:
    def __init__(self, name):
        self.name = name
        self._lock = threading.Lock()

    def try_acquire(self):
        return self._lock.acquire(blocking=False)

    def release(self):
        try:
            self._lock.release()
        except RuntimeError:
            pass


RECOVERY = OperationGate('recovery')
ENCODER = OperationGate('encoder')


def try_acquire_all(*gates):
    """Take every gate or none; on a busy one, return what was taken and report False."""
    taken = []
    for gate in gates:
        if not gate.try_acquire():
            release_all(*taken)
            return False
        taken.append(gate)
    return True


def release_all(*gates):
    for gate in gates:
        gate.release()
