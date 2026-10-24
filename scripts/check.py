#!/usr/bin/env python3
"""Verify a change in one command: compile, unit tests, a live smoke test, optional exe.

    python scripts/check.py          compile + tests + smoke server
    python scripts/check.py --quick  compile + lint + tests only
    python scripts/check.py --exe    also build the folder exe and start it

The smoke server and the exe run on a free port with a throwaway runtime folder, so the
real .runtime (api_token, the live jobs.db) is never touched. It does start WSL, and the
backend's startup reaper may stop a ddrescue/PhotoRec left behind by a crashed backend,
exactly as a normal start would. Exit code 0 means every step that ran passed.
"""

import argparse
import http.client
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = sys.executable
IS_WINDOWS = os.name == 'nt'
# Own process group, so a graceful stop signal reaches only the server.
SERVER_FLAGS = subprocess.CREATE_NEW_PROCESS_GROUP if IS_WINDOWS else 0
NOT_COVERED = ('real devices and real ddrescue/PhotoRec runs', 'the browser UI',
               'process cancellation inside WSL')


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def run(argv, **kwargs):
    return subprocess.run(argv, cwd=ROOT, stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, errors='replace', **kwargs)


def request(port, method, path, host=None, headers=None):
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    conn.putrequest(method, path, skip_host=True)
    conn.putheader('Host', host or f'127.0.0.1:{port}')
    for key, value in (headers or {}).items():
        conn.putheader(key, value)
    if method == 'POST':
        conn.putheader('Content-Length', '0')
    conn.endheaders()
    response = conn.getresponse()
    return response.status, response.read()


def wait_healthy(port, proc, seconds=45):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            if request(port, 'GET', '/api/health')[0] == 200:
                return True
        except OSError:
            time.sleep(0.3)
    return False


def stop(proc):
    """Stop a server the way Ctrl+C would, so its shutdown code runs.

    A hard kill skips the lifespan cleanup and leaves the backend's WSL keepalive
    (`wsl -e sleep infinity`) running with no parent.
    """
    if proc.poll() is not None:
        return
    try:
        proc.send_signal(signal.CTRL_BREAK_EVENT if IS_WINDOWS else signal.SIGINT)
        proc.wait(timeout=25)
    except (OSError, subprocess.TimeoutExpired):
        proc.kill()
        proc.wait(timeout=10)


def probe_server(port, runtime_dir):
    """The page, the token, and the Host/Origin/token guard on a live instance."""
    problems = []
    status, body = request(port, 'GET', '/')
    token_path = os.path.join(runtime_dir, 'api_token')
    token = open(token_path, encoding='utf-8').read().strip() if os.path.exists(token_path) else ''
    if status != 200 or not token or token.encode() not in body:
        problems.append(f'page: status {status}, token injected: {bool(token) and token.encode() in body}')
    auth = {'Authorization': 'Bearer ' + token}
    checks = (
        ('rebound Host is refused', request(port, 'GET', '/', host='evil.example')[0], 403),
        ('API works with the token', request(port, 'GET', '/api/status', headers=auth)[0], 200),
        ('API refuses no token', request(port, 'GET', '/api/status')[0], 401),
        ('cross-origin POST is refused',
         request(port, 'POST', '/api/recovery_cancel',
                 headers={**auth, 'Origin': 'http://evil.example'})[0], 403),
    )
    for label, got, want in checks:
        if got != want:
            problems.append(f'{label}: got {got}, expected {want}')
    return problems


def step_lint():
    """ruff with ruff.toml; skipped (not failed) when ruff isn't installed."""
    if run([PYTHON, '-m', 'ruff', '--version']).returncode != 0:
        return None, 'ruff not installed (python -m pip install --require-hashes -r requirements-dev.lock)'
    result = run([PYTHON, '-m', 'ruff', 'check', 'vs_app', 'clients', 'scripts', 'tests',
                  'video_studio_backend.py'])
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output[-2500:] if result.returncode else 'no lint findings'


def step_compile():
    targets = ['vs_app', 'clients', 'tests', 'scripts', 'video_studio_backend.py']
    result = run([PYTHON, '-m', 'compileall', '-q'] + targets)
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output[-1500:] or 'all files compile'


def step_tests():
    result = run([PYTHON, '-m', 'unittest', 'discover', '-s', 'tests'])
    lines = [line for line in result.stderr.splitlines() if line.strip()]
    summary = ', '.join(line for line in lines[-3:] if line.startswith(('Ran ', 'OK', 'FAILED')))
    detail = summary if result.returncode == 0 else result.stderr.strip()[-3000:]
    return result.returncode == 0, detail


def step_smoke():
    temp = tempfile.mkdtemp(prefix='vs-check-')
    port = free_port()
    env = dict(os.environ, VS_RUNTIME_DIR=temp, VS_JOBS_DB=os.path.join(temp, 'jobs.db'))
    proc = subprocess.Popen([PYTHON, 'video_studio_backend.py', '--port', str(port)],
                            cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            errors='replace', creationflags=SERVER_FLAGS)
    try:
        if not wait_healthy(port, proc):
            stop(proc)
            return False, 'server did not become healthy:\n' + proc.stdout.read()[-2000:]
        problems = probe_server(port, temp)
        return not problems, '; '.join(problems) or f'page, token and guard OK on port {port}'
    finally:
        stop(proc)
        shutil.rmtree(temp, ignore_errors=True)


def step_exe():
    if run([PYTHON, '-c', 'import PyInstaller']).returncode != 0:
        return False, ('PyInstaller is not installed for this Python. Use the build venv: '
                       '.venv-build\\Scripts\\python.exe scripts\\check.py --exe')
    temp = tempfile.mkdtemp(prefix='vs-check-exe-')
    try:
        build = run([PYTHON, '-m', 'PyInstaller', '--noconfirm', '--clean',
                     '--distpath', os.path.join(temp, 'dist'),
                     '--workpath', os.path.join(temp, 'build'), 'video_studio_backend.spec'])
        if build.returncode != 0:
            return False, 'build failed:\n' + build.stderr[-2500:]
        app_dir = os.path.join(temp, 'dist', 'video_studio_backend')
        bundled_env = [os.path.join(base, name) for base, _dirs, files in os.walk(app_dir)
                       for name in files if name == '.env']
        if bundled_env:
            return False, f'.env was bundled: {bundled_env}'
        port = free_port()
        with open(os.path.join(app_dir, '.env'), 'w', encoding='utf-8') as handle:
            handle.write(f'PORT={port}\n')
        runtime = os.path.join(temp, 'runtime')
        env = {key: value for key, value in os.environ.items() if key != 'PORT'}
        env['VS_RUNTIME_DIR'] = runtime
        exe = os.path.join(app_dir, 'video_studio_backend.exe' if IS_WINDOWS
                           else 'video_studio_backend')
        proc = subprocess.Popen([exe], cwd=app_dir, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=SERVER_FLAGS)
        try:
            if not wait_healthy(port, proc):
                return False, 'exe did not become healthy (port read from .env next to it)'
            problems = probe_server(port, runtime)
            return not problems, '; '.join(problems) or 'folder build OK, no .env inside, served the app'
        finally:
            stop(proc)
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--quick', action='store_true', help='compile and unit tests only')
    parser.add_argument('--exe', action='store_true', help='also build and start the exe')
    args = parser.parse_args(argv)

    steps = [('compile', step_compile), ('lint', step_lint), ('unit tests', step_tests)]
    if not args.quick:
        steps.append(('smoke server', step_smoke))
    if args.exe:
        steps.append(('exe build', step_exe))

    failed = False
    for name, step in steps:
        started = time.time()
        try:
            ok, detail = step()
        except Exception as exc:
            ok, detail = False, f'{exc.__class__.__name__}: {exc}'
        failed = failed or ok is False
        label = 'SKIP' if ok is None else 'PASS' if ok else 'FAIL'
        print(f"{label}  {name:<13} {time.time() - started:5.1f}s  {detail}",
              flush=True)
    for cache in ('vs_app', 'clients', 'tests', 'scripts'):
        for base, dirs, _files in os.walk(os.path.join(ROOT, cache)):
            if '__pycache__' in dirs:
                shutil.rmtree(os.path.join(base, '__pycache__'), ignore_errors=True)
    print('Not covered: ' + '; '.join(NOT_COVERED) + '.')
    print('RESULT: ' + ('FAILED' if failed else 'ALL PASSED'))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
