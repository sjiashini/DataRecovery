# Ultimate CD/DVD + Pen Drive Recovery Tool (WSL2 + NVIDIA RTX 3050 NVENC)

A hybrid cross-platform recovery suite and hardware-accelerated video extraction pipeline engineered for fragile, scratched, or degraded optical media (CDs, DVDs, VCDs) **and formatted or degraded USB pen drives**. The recovery core images any block device sector-by-sector with GNU ddrescue, then carves every recoverable file with PhotoRec — with TestDisk available to undelete files with their original filenames.

---

## 1. Project Overview & Architectural Principles

The **CD-Recovery Tool** bridges the low-level hardware control of Windows 11 with the error-tolerant POSIX block extraction capabilities of Linux (WSL2) and hardware video acceleration powered by the NVIDIA GeForce RTX 3050.

### Core Architectural Principles
1. **Physical Media Preservation (Non-Destructive First Pass)**:
   Optical drives suffer excessive wear when repeatedly attempting to read damaged laser sectors. The system utilizes `GNU ddrescue` with custom 2048-byte sector geometry (`-b 2048`), first copying all readable data without splitting, then executing targeted multi-pass scrapes only on damaged clusters.
2. **Hybrid Host-Guest Virtualization**:
   Combines `usbipd-win` for host USB passthrough to WSL2 with Windows-native disk image mounting to bypass UDF filesystem boundary deadlocks.
3. **Hardware-Accelerated Transcoding (RTX 3050 NVENC)**:
   Integrates `HandBrakeCLI` with 10-bit H.265 NVENC hardware encoding (`nvenc_h265_10bit`), motion-adaptive deinterlacing (`--comb-detect --decomb`), achieving encoding throughput exceeding ~280 frames per second.
4. **Decoupled Asynchronous UI**:
   A sleek modern dark-mode GUI constructed with `customtkinter` running background worker threads to keep UI telemetry responsive during multi-hour disc extraction jobs.
5. **Damaged Disc Navigation Packet Bypass (Hybrid SOP)**:
   Automatically compensates for unreadable `VIDEO_TS.IFO` structures by concatenating raw `.VOB` MPEG-2 streams bitwise before feeding them to the hardware transcoder.
6. **Formatted Flash Recovery (Image-First Signature Carving)**:
   Pen drives are never repaired in place. The drive is first cloned to a raw image (`ddrescue -b 512`, multi-pass with retries) — a quick format only resets filesystem metadata, so the file data survives on the flash until overwritten. PhotoRec then carves every recoverable file by signature across the whole image space (`paranoid + wholespace + keep corrupted files`), and TestDisk can rebuild directory entries to recover original filenames.
7. **Layered ASGI Backend**:
   The web backend is a FastAPI application split into presentation (static SPA), API (REST + SSE), orchestration (job manager + pipeline engine), execution (subprocess adapters) and storage (SQLite job snapshots + replayable event stream) layers. Jobs survive page reloads and backend restarts are detected and marked explicitly.

---

## 2. Complete Tech Stack Table

| Component Layer | Technology / Tool | Version / Spec | Description |
| :--- | :--- | :--- | :--- |
| **Operating System (Host)** | Windows 11 Pro / Enterprise | 64-bit | Physical device host, USB manager, native disk mounter |
| **Operating System (Guest)** | WSL2 (Ubuntu Linux) | 22.04 / 24.04 LTS | POSIX recovery subsystem, block device handler |
| **GPU Hardware** | NVIDIA GeForce RTX 3050 | Ampere Architecture | 4GB/6GB/8GB VRAM, NVENC Gen 7, Tensor/CUDA cores |
| **Language Runtime** | Python | 3.10+ | Primary GUI and automation script execution |
| **Presentation / Web UI** | HTML5 / CSS / JavaScript SPA | `vs_app/web/video_studio_app.html` | Served by FastAPI with a per-launch token; SSE live logs and stage timeline |
| **Presentation / Desktop UI** | `customtkinter` | 5.2+ | Modern dark-theme GUI with real-time log terminal |
| **Web API** | FastAPI + Uvicorn | 0.128.8 / 0.40.0 | Loopback ASGI app: REST, SSE streaming, Pydantic validation, bearer-token auth, rate limiting |
| **Orchestration** | Job Manager + Pipeline Engine | `vs_app/orchestration/jobs.py`, `vs_app/orchestration/pipeline.py` | Job state machine, stage adapters, per-stage failure policy, exact-process cancellation |
| **Persistence** | SQLite via `aiosqlite` (WAL) | `.runtime/jobs.db` | Job snapshots plus a replayable SSE event stream per job |
| **Device Virtualization** | `usbipd-win` | 4.0+ | Host-to-WSL USB/IP device bus forwarding |
| **Block Recovery Engine** | `GNU ddrescue` | 1.27+ | Two-phase mapfile sector-level disc rescuer (optical 2048B / USB flash 512B sectors) |
| **File Carving Engine** | `PhotoRec` | `testdisk` 7.1+ | Signature-based carving of 480+ file types from raw images; whole-space scan for formatted media |
| **Undelete Engine** | `TestDisk` | `testdisk` 7.1+ | Rebuilds FAT/exFAT/NTFS directory entries to recover files with original filenames |
| **Media Transcoder** | `HandBrakeCLI` | 1.6+ | Hardware-accelerated CLI video transcoder with NVENC |
| **Video Codec** | HEVC / H.265 (10-bit) | `nvenc_h265_10bit` | Hardware-compressed modern video format |
| **Audio Codec** | Advanced Audio Coding | AAC (`av_aac`) | Universal stereo/surround audio standard |
| **Deinterlace Filter** | Comb Detection + Decomb | EEDI2 / Yadif | Motion-adaptive anti-combing for interlaced DVDs |
| **Documentation** | Markdown + HTML | `README.md`, `docs/` | This README; historical recovery reports in `docs/archive/`, UI theme notes in `docs/design/` |

### 2.1 Layered Runtime (`vs_app/`)

| Layer | Modules | Responsibility |
| :--- | :--- | :--- |
| **Presentation** | `vs_app/web/video_studio_app.html` | SPA served by the backend; EventSource streams live logs/progress and renders a stage timeline, with 2 s polling as fallback. |
| **API** | `vs_app/main.py`, `vs_app/api/` | REST endpoints, SSE endpoint, Pydantic request models (`schemas.py`), bearer-token auth, rate limiting, security headers. |
| **Orchestration** | `vs_app/orchestration/` (`jobs.py`, `pipeline.py`, `flows.py`, `gate.py`) | Job state machine (`queued → running → completed/partial/failed/cancelled`), sequential ddrescue → TestDisk → PhotoRec pipeline with per-stage failure policy, RECOVERY and ENCODER locks. |
| **Execution** | `vs_app/execution/` (`adapters.py`, `parsers.py`, `bridge.py`, `media.py`) | `asyncio.create_subprocess_exec` adapters, line/byte stream parsing per tool, pidfile-based exact cancellation, exit-code mapping. |
| **Storage / Events** | `vs_app/storage/` (`store.py`, `bus.py`), `vs_app/runtime.py` | SQLite job snapshots and numbered events for replay, in-process asyncio queues fanning out to SSE subscribers. |

---

## 3. Prerequisites & Environment Installation

### Quick setup (recommended)

Double-click **`setup_studio.bat`**. It checks WSL2 and the Ubuntu distro, installs the Linux tools (`gddrescue`, `testdisk`) as root inside WSL with no sudo prompt, installs usbipd-win, ffmpeg and HandBrakeCLI with `winget`, and installs the app's Python packages into a project `.venv` from the hash-locked `requirements.lock`. It is safe to re-run: anything already present is skipped, and it ends with a table of what is ready and what still needs attention. Run `setup_studio.bat -CheckOnly` to get the report without changing anything. The launchers use `.venv` automatically when it exists.

Installing WSL itself needs an admin PowerShell and a reboot (`wsl --install`), so the script tells you the command instead of running it. The manual steps below remain as a reference.

### Step 1: Windows Host Setup
1. Ensure **WSL2** is enabled and an Ubuntu distribution is installed:
   ```powershell
   wsl --install -d Ubuntu
   ```
2. Install **usbipd-win** via `winget`:
   ```powershell
   winget install --interactive --exact dorssel.usbipd-win
   ```
3. Install the latest **NVIDIA Game Ready / Studio Drivers** on Windows. (WSL2 automatically inherits CUDA/NVENC support).

### Step 2: WSL2 Guest Setup
1. Launch your Ubuntu terminal in WSL2:
   ```bash
   wsl -d Ubuntu
   ```
2. Update packages and install prerequisites (`ddrescue`, `PhotoRec`/`TestDisk`, `HandBrakeCLI`, `linux-tools`, `hwdata`):
   ```bash
   sudo apt update
   sudo apt install -y gddrescue testdisk handbrake-cli linux-tools-generic hwdata python3-pip python3-tk
   ```
3. Create and activate a dedicated Python virtual environment:
   ```bash
   python3 -m venv ~/cd_recovery_env
   source ~/cd_recovery_env/bin/activate
   pip install customtkinter
   ```

---

## 4. Exhaustive CLI Commands Reference

| Operation | Environment / Shell | Command | Description |
| :--- | :--- | :--- | :--- |
| **List USB Devices** | PowerShell (Admin) | `usbipd list` | Enumerates physical USB devices and Bus IDs on Windows |
| **Bind Drive for Sharing** | PowerShell (Admin) *or* Web UI | `usbipd bind --busid <BUSID>` | One-time: registers drive for USB/IP sharing. The Web UI does this via **Share with WSL** (validated bus ID, one UAC prompt). |
| **Attach Drive to WSL** | Web UI / backend | `usbipd attach --wsl Ubuntu --busid <BUSID>` | Hands drive to WSL2. The backend auto-attaches already-shared devices on **Detect Drives**; unshared devices get an explicit **Share with WSL** button. |
| **Detach Drive from WSL** | Windows CMD | `usbipd detach --busid <BUSID>` | Returns drive control to Windows host |
| **Activate Python Venv** | WSL2 (Ubuntu Bash) | `source ~/cd_recovery_env/bin/activate` | Enters Python environment |
| **Run Recovery GUI** | WSL2 (Ubuntu Bash) | `python clients/cd_recovery_tool.py` | Launches interactive recovery desktop interface |
| **Standard ddrescue** | WSL2 (Ubuntu Bash) | `ddrescue -b 2048 -n -v /dev/sr0 cd_backup.iso cd_backup.iso.log` | Fast optical extraction skipping bad sectors |
| **Aggressive ddrescue** | WSL2 (Ubuntu Bash) | `ddrescue -b 2048 -d -r 5 -R -v /dev/sr0 cd_backup.iso cd_backup.iso.log` | Direct optical I/O, 5 retries, reverse scraping |
| **Pen Drive Detect** | WSL2 (Ubuntu Bash) | `lsblk -P -o NAME,TYPE,SIZE,TRAN,RM,MODEL,LOG-SEC` | Lists optical + USB flash drives (`TYPE="disk"` with `TRAN="usb"` or `RM="1"`) |
| **Pen Drive Imaging** | WSL2 (Ubuntu Bash) | `ddrescue -b 512 -r 3 -v /dev/sdb pendrive_backup.img pendrive_backup.img.log` | Full multi-pass raw image with 3 retries (512B flash sectors) |
| **Pen Drive Aggressive** | WSL2 (Ubuntu Bash) | `ddrescue -b 512 -d -r 5 -v /dev/sdb pendrive_backup.img pendrive_backup.img.log` | Direct I/O + 5 retries for degraded/bad-block flash |
| **PhotoRec Carve (all files)** | WSL2 (Ubuntu Bash) | `photorec /log /d /mnt/c/PendriveRecovery/carved /cmd /mnt/c/PendriveRecovery/pendrive_backup.img options,keep_corrupted_file,paranoid,fileopt,everything,enable,wholespace,search` | Scripted whole-space carving into `carved/recup_dir.*` |
| **TestDisk Undelete (original names)** | WSL2 (Ubuntu Bash) | `testdisk /mnt/c/PendriveRecovery/pendrive_backup.img` | Interactive: Advanced → Undelete → select files → copy with original filenames |
| **Mount ISO (Windows)** | PowerShell | `$m = Mount-DiskImage -ImagePath "C:\path\cd_backup.iso" -PassThru` | Mounts ISO without cross-boundary Linux stalls |
| **Extract VOB Chunks** | PowerShell | `Copy-Item -Path "E:\VIDEO_TS\VTS_01_*.VOB" -Destination "C:\Recovery\"` | Copies raw MPEG-2 video streams from disc |
| **Concatenate VOBs** | Windows CMD | `cmd /c copy /b VTS_01_1.VOB + VTS_01_2.VOB + VTS_01_3.VOB main_video.vob` | Bitwise joining to prevent AV sync splits |
| **Run Backend Server** | Windows CMD / PowerShell | `run_backend.bat` | Starts the FastAPI/Uvicorn backend on `127.0.0.1:8080` and opens the browser. Every launch writes a fresh token to `.runtime\api_token`. |
| **Launch Full Studio** | Windows CMD / Explorer | `start_studio.bat` | Starts the backend with `--open-browser`; the page must be opened from the URL, never as a local `.html` file (it needs the injected token). If the backend is already running, it opens that one in the browser instead (it never restarts it, so a running recovery is not cut off). Tools installed while it runs, such as HandBrakeCLI or ffmpeg through winget, are found without a restart. |
| **Set Up Everything** | Windows CMD / Explorer | `setup_studio.bat` (`-CheckOnly` to only report) | Installs or verifies WSL tools, Windows media tools and the locked Python packages. |
| **Build Backend EXE** | Windows CMD / PowerShell | `build_exe.bat` | Builds in a private `.venv-build` from the hash-locked `requirements-dev.lock`. Output is a folder, `dist\video_studio_backend\`, holding the exe and its `_internal` files. `.env` is never bundled; put one next to the exe to configure it. |
| **Run Tests** | Project root | `python -m unittest discover -s tests -v` | Pure policy/config/job/store/pipeline/parser/media tests (no device access). |
| **Check a Change** | Project root | `python scripts/check.py` (`--quick`, `--exe`) | Compiles, runs the tests and smoke-tests a live backend on a free port with a throwaway runtime folder; `--exe` also builds and starts the exe. |
| **Relock Dependencies** | Project root | `python scripts/lock_deps.py` (`--upgrade-package <name>`) | Rebuilds both hash-checked lock files from `requirements*.txt` with `uv` and lists every pin that changed. |

---

## 5. Formatted Pen Drive Recovery Workflow

### 5.1 Reality check: quick format vs full format

| Format type | Recoverable? | Why |
| :--- | :--- | :--- |
| Quick format (Windows default) | **Yes — usually most files** | Only the file table (FAT/exFAT/NTFS metadata) is reset; file data stays on the flash until overwritten |
| Full format (zero-fill) | No | Every sector is overwritten with zeros |
| Any format + continued use | Partial | New writes overwrite old blocks; flash wear-leveling delays but never prevents this |

**Rule #1: stop using the pen drive immediately.** Connect it only for the imaging step and save everything to the PC — never write the image back onto the drive you are recovering.

### 5.2 The 3-stage pipeline

1. **Plug in the pen drive** — if it has never been shared, the Web UI flags it and shows a **🔗 Share with WSL** button (one UAC prompt; the backend validates the bus ID and never interpolates it into a shell). After that, **Detect Drives** auto-attaches shared drives, and the backend keeps the WSL VM alive so the drive stays visible even after WSL idle shutdowns.
2. **Image the raw drive** (imaging first — never work on the drive itself):
   `ddrescue -b 512 -r 3 -v /dev/sdb /mnt/c/PendriveRecovery/pendrive_backup.img /mnt/c/PendriveRecovery/pendrive_backup.img.log`
3. **Carve all files** (whole-space signature scan):
   `photorec /log /d /mnt/c/PendriveRecovery/carved /cmd /mnt/c/PendriveRecovery/pendrive_backup.img options,keep_corrupted_file,paranoid,fileopt,everything,enable,wholespace,search`
4. **Original filenames (optional)**: `testdisk /mnt/c/PendriveRecovery/pendrive_backup.img` → `Advanced` → `Undelete` → select files → `C` to copy.

### 5.3 One-click execution

* **Web UI**: tab **"💿💾 CD/DVD & Pen Drive Recovery"** → 🚀 Run Full Recovery (backend must run via `start_studio.bat`). Live progress, cancel, TestDisk launcher and Explorer folder open are built in.
* **Two-step mode (manual)**: uncheck **"Carve ALL files with PhotoRec after imaging"**, then 🚀 Run Full Recovery — Stage 1 (imaging) runs alone and stops. Whenever you're ready, click **🧩 Step 2: Start Carving (from image)** to carve the finished `.img` in a separate job. No auto-carve ever happens in this mode.
* **Live progress bars**: each stage shows its own bar with a real percentage, ETA and detail — imaging reads ddrescue's `pct rescued`/rate/errors, carving decodes PhotoRec's `Pass N — Reading sector X/Y` screen output (forced unbuffered). The active stage's percent also appears in the status line and a `📊` summary is written to the log every ~30s. For a partial/truncated image the carving total is capped to the actual file size so the bar can't overshoot.
* **Desktop GUI**: `python clients/cd_recovery_tool.py` inside WSL2 (from the project folder) → select the 💾 drive → Start Recovery.
* **API**: all routes require `Authorization: Bearer <token>` (except `GET /api/health` and the page itself); the token lives in `.runtime\api_token` and is injected into the page served at `http://127.0.0.1:8080/`. `POST /api/recover_all` returns `job_id` + `events_url`; the browser streams `GET /api/jobs/{id}/events` (SSE; `?token=…` because EventSource cannot send headers, `?since=<seq>` for replay) and automatically falls back to `GET /api/recovery_progress` polling if the stream drops. `POST /api/recovery_cancel` cancels the exact child process (ddrescue saves its mapfile) and the snapshot keeps the image path so the next run resumes. Add `"carve": false` for imaging-only, or `"skip_imaging": true` to carve an existing image. Add `"file_types": ["images","videos","pdf","word","excel","powerpoint"]` (any subset; omit or `"all"` for everything) to carve only those types. Terminal stages are `completed`, `partial`, `failed`, `cancelled` — a partial image is never auto-carved.
* **File types to carve** (UI chips): carving only selected types is faster and skips junk (`.cab`, `.dll`, `.txt` fragments). Output goes to a separate folder per selection (e.g. `carved_pdf-excel.1`). Word/Excel/PowerPoint share PhotoRec's OLE (`doc`) and ZIP containers, so unselected Office/zip files that get carved are **moved** (not deleted) to `carved_<types>_other`. Filtered runs force `blocksize,512` so a sparse selection isn't missed due to a wrong cluster-size guess.

### 5.4 What you get

* PhotoRec returns **content intact but generic names** (`f0123456.jpg`) — expected, since the file table that stored names is gone. File type is detected by signature, so renamed/corrupt-extension files still come back. Files land in numbered folders (`carved.1`, `carved.2`, …) next to the image — PhotoRec starts a new folder every 500 files.
* TestDisk undelete recovers **original filenames + folder paths** for deleted files whose entries survive in the FAT/exFAT/NTFS table (typical right after a quick format).
* All stages read **only from the image** — the source pen drive is never written to, so nothing degrades while you work.

---

## 6. Repository Directory Tree

```
VideoStudio-V3/
├── start_studio.bat                 # Start the app (opens the browser; reuses a running backend)
├── run_backend.bat                  # Backend in this console window
├── setup_studio.bat                 # One-step WSL, tools and Python setup (re-runnable, -CheckOnly)
├── build_exe.bat                    # Hash-locked folder EXE build in .venv-build
├── video_studio_backend.py          # CLI launcher (--port/--distro) → vs_app.main.run()
├── video_studio_backend.spec        # PyInstaller folder-build spec (bundles the page, never .env)
├── requirements.txt / -dev.txt      # Direct dependencies (inputs for the locks)
├── requirements.lock / -dev.lock    # Every package pinned with hashes (pip --require-hashes)
├── .env.example                     # Environment template (actual supported keys)
├── README.md                        # Architecture, SOP and configuration
├── CLAUDE.md                        # Project notes and safety rules read by Claude Code
├── .claude/
│   ├── skills/check/                # /check: run scripts/check.py and report what it covers
│   ├── skills/update-deps/          # /update-deps: edit, relock, verify in a clean venv
│   └── agents/recovery-reviewer.md  # Read-only reviewer for recovery-path changes
├── vs_app/                          # The application, one sub-package per layer
│   ├── main.py                      # ASGI app, Host/Origin + token guard, lifespan, page serving, run()
│   ├── runtime.py                   # Process-wide store/bus/job-manager singletons
│   ├── settings.py                  # Loaded settings, logger, per-launch token, WSL distro
│   ├── config.py                    # .env loading, roots, runtime dirs, rotating logs
│   ├── web/
│   │   └── video_studio_app.html    # Presentation: the SPA (token-injected, SSE, stage timeline)
│   ├── api/                         # API layer
│   │   ├── routes/                  # system.py, recovery.py, media.py routers
│   │   ├── schemas.py               # Pydantic request models
│   │   └── policy.py                # Device allowlist, path sandbox (links resolved), media inputs
│   ├── orchestration/               # Orchestration layer
│   │   ├── jobs.py                  # Job state machine + JobManager
│   │   ├── pipeline.py              # Sequential stage engine with per-stage failure policy
│   │   ├── flows.py                 # Recovery and repair pipelines (gate ownership)
│   │   └── gate.py                  # RECOVERY and ENCODER locks (imaging and media may overlap)
│   ├── execution/                   # Execution layer
│   │   ├── adapters.py              # ddrescue/TestDisk/PhotoRec adapters, pidfiles, orphan reaper
│   │   ├── parsers.py               # ddrescue + PhotoRec/terminal parsers
│   │   ├── bridge.py                # WSL bridge, drive scan, usbipd, image lock probe, paths
│   │   └── media.py                 # ffmpeg/HandBrake operations, tool lookup with live PATH
│   ├── storage/                     # Storage layer
│   │   ├── store.py                 # SQLite jobs/events persistence (aiosqlite, WAL)
│   │   ├── bus.py                   # In-process asyncio fan-out for SSE
│   │   └── token_file.py            # Writes and removes the per-launch API token file
│   └── core/                        # Pure rules, no I/O (shared with the desktop GUI)
│       ├── recovery.py              # Devices, paths, tool argv, carving rules, image locks
│       └── security.py              # Token, Host/Origin checks, security headers
├── clients/
│   ├── cd_recovery_tool.py          # Optional CustomTkinter desktop GUI (run inside WSL)
│   └── watch_and_carve.py           # Turnkey image-then-carve automation over the API
├── scripts/
│   ├── setup_studio.ps1             # The setup logic behind setup_studio.bat
│   ├── check.py                     # One-command verification (compile, tests, smoke, exe)
│   └── lock_deps.py                 # Regenerates both dependency locks
├── tests/                           # unittest suite (150 tests, no device access)
├── docs/
│   ├── archive/                     # Historical reports and logs (not runtime code)
│   └── design/                      # UI theme notes
└── .runtime/                        # Created at runtime: api_token, logs/, jobs.db
```

---

## 7. Environment Configuration

Copy [`.env.example`](.env.example) to `.env`. For the packaged exe, put the `.env` next to `video_studio_backend.exe`; it is never built into the exe. Only these keys are read:

| Key | Default | Purpose |
| :--- | :--- | :--- |
| `PORT` | `8080` | Loopback port (server always binds `127.0.0.1`). |
| `DEFAULT_OUTPUT_DIR` | `C:\PendriveRecovery` | Default image/output folder; must be inside `RECOVERY_ROOTS`. |
| `RECOVERY_ROOTS` | `C:\PendriveRecovery`, `C:\Recovery`, `D:\PendriveRecovery`, `D:\Recovery`, plus `/mnt/...` variants | Semicolon-separated allowlist for image outputs, uploads, carved folders and repair outputs. |
| `MEDIA_ROOTS` | Workspace + Desktop/Downloads/Documents/Videos + recovery roots | Allowlist for Cut/Join/Transcode inputs and outputs. |
| `WSL_DISTRO_NAME` | `Ubuntu` | Distribution used to run Linux tools. |
| `PHOTOREC_CARVE_DIR` | `carved` | Folder name created next to the image for `carved.*` results. |
| `HANDBRAKE_CQ` | `18` | Constant Quality (clamped 0–51). |
| `HANDBRAKE_ENCODER` | `nvenc_h265_10bit` | HandBrake encoder. |
| `LOG_LEVEL` | `INFO` | Console + rotating file log level. |
| `VS_RUNTIME_DIR` | `<workspace>/.runtime` (or `%LOCALAPPDATA%\VideoStudio` when frozen) | Token, logs and runtime state. |
| `VS_JOBS_DB` | `<VS_RUNTIME_DIR>\jobs.db` | Optional override for the SQLite job store (snapshots + replayable events). |

---

## 8. Security & Reliability Boundary

* The server binds **127.0.0.1 only**, sends no CORS headers, and denies framing/objects. The page itself has no authentication value without the per-launch bearer token injected by the server.
* Every request (the page included) must carry a loopback `Host` header (`127.0.0.1`, `localhost` or `[::1]`), so a DNS-rebinding site cannot load the token-bearing page. State-changing requests that send an `Origin` must come from a loopback origin; scripts that send no `Origin` still work with the token.
* Cut/Join/Transcode inputs and the repair reference must sit inside `MEDIA_ROOTS`. All root checks also resolve junctions and symlinks, so a link inside a root cannot point outside it.
* On startup the backend stops ddrescue/PhotoRec/TestDisk processes left running inside WSL by a previous backend that crashed or was killed (SIGINT first so ddrescue saves its mapfile, then SIGTERM). Only processes whose pidfile owner is gone and whose command is one of those tools are touched.
* Each image has a lock (`flock` on `/tmp/video-studio-locks/<hash>.lock` inside WSL). ddrescue, TestDisk and PhotoRec hold it while they run, and so does an interactive TestDisk window opened from the Web UI. The desktop GUI uses the same lock, so it also blocks a second backend. If the image is busy, the request is refused before anything starts. The kernel releases the lock when the tool exits or is killed, so a stale lock can never be left behind.
* All `/api` routes require the token except `/api/health` and the page itself. The SSE endpoint also accepts `?token=` (EventSource cannot set headers); the API guard rate-limits requests per client and rejects unknown paths.
* Device operations accept only `/dev/sd*`, `/dev/sr*`, `/dev/mmcblk*`, `/dev/nvme*` nodes that `lsblk` currently enumerates as optical/USB storage. Output paths must resolve inside configured roots.
* Cut, join and transcode run as `media` jobs under the `ENCODER` lock, so they can run while a drive is being imaged or carved (which holds the `RECOVERY` lock); repair takes both. A media output may not be saved into the folder of the recovery that is running. `POST /api/media_cancel` (or the Cancel button under Cut and Join) stops the running ffmpeg/HandBrakeCLI and removes the partial output; `GET /api/media_progress` shows the current one. The response of `/api/cut`, `/api/join` and `/api/transcode` keeps its old shape, plus `job_id`.
* Every external command is an argv list (no `shell=True`, no string interpolation of paths or bus IDs). Media outputs are written to `.__vs_partial__` files and atomically renamed.
* Cancellation targets the exact pidfile-wrapped child (`SIGINT`, then `SIGTERM`); it never uses `pkill`. ddrescue's mapfile and the image are preserved for resume.
* The `RECOVERY` lock (`vs_app/orchestration/gate.py`) allows one recovery at a time, and the `ENCODER` lock one cut/join/transcode at a time; jobs report `completed`, `partial`, `failed` or `cancelled` explicitly. Job snapshots and their numbered events are persisted to `.runtime/jobs.db`; jobs left running by a crash are marked `failed` on the next start.

Run the suite with `python -m unittest discover -s tests -v`.

