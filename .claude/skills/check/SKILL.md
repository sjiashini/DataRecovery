---
name: check
description: Verify a change to Video Studio before calling it done - compiles everything, runs the unit tests, and smoke-tests a live backend on a spare port with a throwaway runtime folder (optionally builds and starts the exe). Use after editing any code in vs_app/, clients/, scripts/ or tests/, before reporting work as finished, or when the user asks to check, verify or test the project.
---

# /check — verify a change

Run from the project root:

```
python scripts/check.py
```

Options:
- `--quick` compile and unit tests only (seconds; use while iterating).
- `--exe` also builds the folder exe in a temp folder and starts it. It needs PyInstaller,
  so run it with the build environment's Python if the global one lacks it:
  `.venv-build\Scripts\python.exe scripts\check.py --exe` (created by `build_exe.bat`).
  Use `--exe` after changing `video_studio_backend.spec`, `vs_app/config.py`, the page
  location, or the dependency locks.

## How to report the result

- Quote each `PASS`/`FAIL` line and the final `RESULT:` line.
- On a failure, read the detail it prints, fix the cause, and run it again. Don't
  report the work as done while anything fails.
- Always repeat the script's "Not covered" line, and add anything specific to the
  change that it can't exercise (for example: a real pen drive, a cancel during imaging,
  a UI change that needs a look in the browser).

## Rules this script already follows (don't work around them)

- The smoke server and exe use a temp `VS_RUNTIME_DIR`/`VS_JOBS_DB` and a free port.
  Never test against the real `.runtime/` folder or port 8080, which the user's live
  backend may be using.
- It never runs `clients/watch_and_carve.py` (that would start real imaging).
- If a test needs a new kind of check, add it to `tests/` (plain `unittest`) or to
  `scripts/check.py` rather than running ad-hoc commands against the live app.

The project's safety rules live in `CLAUDE.md`; for changes to the recovery path, also
ask the `recovery-reviewer` agent for a review.
