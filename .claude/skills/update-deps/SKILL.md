---
name: update-deps
description: Add, remove or upgrade a Python dependency of Video Studio safely - edits requirements.txt or requirements-dev.txt, regenerates both hash-checked lock files, installs them into a clean environment and runs the full check. Use whenever a package must be added, removed, pinned or upgraded, or when the user asks to update dependencies or the lock files.
---

# /update-deps — change dependencies safely

The `.lock` files are generated; never edit them by hand. `requirements.txt` holds the
app's direct dependencies, `requirements-dev.txt` adds the build tools (PyInstaller).

## Steps

1. **Edit the input file.** Direct dependencies only, pinned with `==`:
   - runtime package → `requirements.txt`
   - build or test tool → `requirements-dev.txt`
   To upgrade a direct dependency, change its `==` version there.

2. **Relock both files** from the project root:
   ```
   python scripts/lock_deps.py
   ```
   - To bump an *indirect* package (one not listed in the .txt files):
     `python scripts/lock_deps.py --upgrade-package <name>`
   - `--upgrade` bumps everything; only do that when the user asks for it.
   - If `uv` is missing: `python -m pip install uv`.
   The script prints every pin that changed. Show that list to the user.

3. **Install the locks into a clean, temporary environment** (never the user's global
   Python, never `.venv` or `.venv-build` while testing):
   ```
   python -m venv <temp dir>\venv
   <temp dir>\venv\Scripts\python.exe -m pip install --require-hashes -r requirements-dev.lock
   ```
   An install failure here means the lock is broken. Stop and report it.

4. **Run the full check with that environment's Python**, so the tests run against the
   locked versions:
   ```
   <temp dir>\venv\Scripts\python.exe scripts\check.py --exe
   ```
   Report its PASS/FAIL lines (see the `/check` skill). Delete the temp folder afterwards.

5. **Tell the user how to pick up the change:**
   - their `.venv`: re-run `setup_studio.bat`
   - the exe: re-run `build_exe.bat` (it reinstalls `.venv-build` from the lock)

## Watch for

- The locks must stay valid for Python 3.10+ on Windows and Linux. If a new version
  drops Python 3.10, the relock fails or adds markers. Say so rather than raising the
  floor silently.
- A changed `fastapi`, `starlette`, `uvicorn` or `pydantic` version can change the API
  guard or the SSE stream. The smoke step in check.py exercises both; read its output.
