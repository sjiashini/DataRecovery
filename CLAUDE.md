# Video Studio V3

Loopback-only FastAPI app on Windows 11 that recovers files from damaged CDs/DVDs and
formatted pen drives (ddrescue → TestDisk → PhotoRec inside WSL2) and does video
cut/join/transcode (ffmpeg, HandBrakeCLI). One user, one machine, one device at a time.
The data it recovers is often irreplaceable, and every extra read wears a failing disc.

## Layout (one sub-package per layer)
- `vs_app/main.py` ASGI app, API guard (Host/Origin/token), lifespan, `run()`
- `vs_app/api/` routes, Pydantic schemas, `policy.py` (device allowlist, path sandbox)
- `vs_app/orchestration/` job state machine, pipeline engine, flows, resource locks (`gate.py`)
- `vs_app/execution/` `adapters.py` (tool subprocesses, pidfiles, reaper), `parsers.py`,
  `bridge.py` (WSL, usbipd, drives, image-lock probe), `media.py` (ffmpeg/HandBrake)
- `vs_app/storage/` SQLite job store, event bus · `vs_app/web/` the SPA page
- `vs_app/core/` pure rules, no I/O: `recovery.py` (argv, paths, locks), `security.py`
- `clients/` desktop GUI (runs inside WSL) and `watch_and_carve.py` · `scripts/` · `tests/`

## Commands
- Verify a change: `python scripts/check.py` (add `--exe` to also build and start the exe)
- Tests only: `python -m unittest discover -s tests`
- Start the app: `start_studio.bat` · Setup/report tools: `setup_studio.bat [-CheckOnly]`
- Relock dependencies: `python scripts/lock_deps.py` (see the `/update-deps` skill)

## Safety rules (the single source; the reviewer agent checks these)
1. Never write to the source device. Image first with ddrescue; every later stage reads
   only the image.
2. External commands are argv lists. No `shell=True`. Shell text run inside WSL quotes
   values with `shlex.quote` or passes them as `"$@"`, never by interpolation.
3. Every ddrescue/TestDisk/PhotoRec run holds the image lock: `spawn(job, argv, image)`
   in adapters, or `core.locked_argv(argv, image)` elsewhere. Busy exits with 75.
4. Cancel with SIGINT, then SIGTERM, through the pidfile (`adapters.cancel_process`).
   Never `pkill`. ddrescue's mapfile must survive so the image can resume.
5. Every user-supplied path goes through `api/policy.py` (`require_within`,
   `media_input`). Root checks resolve junctions and symlinks; keep it that way.
6. Devices go through `policy.validate_device` (pattern plus a live `lsblk` listing).
7. API guard: every request needs a loopback `Host`; state-changing requests with an
   `Origin` need a loopback origin; `/api/*` needs the token except `/api/health`.
   Don't grow `PUBLIC_PATHS`.
8. Layers: `core/` has no I/O or side effects (the GUI imports it inside WSL). `api/`
   validates and delegates. Long-running work runs as a job through `JobManager` and
   holds its lock from `gate.py`, released in `finally`: `RECOVERY` for drive, image
   and carve work, `ENCODER` for ffmpeg/HandBrake, both for repair (`try_acquire_all`). Only `execution/` starts subprocesses. Known
   exceptions: the legacy `/api/ddrescue` and `/api/photorec` routes, and in
   `api/routes/system.py` the `/api/tools` probe and the TestDisk console window.
9. Never stop or restart a running backend without first checking that no recovery or
   repair job is active (`/api/recovery_progress`, `/api/repair_progress`).
10. Never resume an image from a different source: imaging checks the image's
    `.source.json` record and mapfile size (`bridge.image_source_problem`). File and
    carve-folder outputs must be strictly inside a root (`policy.require_inside`), and
    media outputs must be video files that never replace data in a recovery folder
    and never land in the folder of the recovery that is running.

## Testing rules
- A change isn't done until `python scripts/check.py` passes. Say what it can't cover:
  real devices, real WSL tool runs, the UI.
- Never point tests or smoke servers at the real `.runtime/` (it holds the live
  `jobs.db`). Use a temp `VS_RUNTIME_DIR` and `VS_JOBS_DB`, as check.py does.
- Never run `clients/watch_and_carve.py` against the live backend: it starts real
  imaging of `/dev/sde`. Never write to `C:\PendriveRecovery` or a real device in tests.

## Conventions
- No git. Before moving or deleting many files, zip a backup next to the project
  folder (`VideoStudio-V3-backup-<what>-<date>.zip`).
- Dependencies: edit `requirements*.txt` (direct deps only, pinned `==`), then relock.
  Never hand-edit the `.lock` files.
- Python 3.10+ compatible (the locks are universal for 3.10+); this PC runs 3.14.
- When files move, update the README folder tree and every path in it.
- Match the surrounding code: comment density, naming, plain `unittest` tests.
