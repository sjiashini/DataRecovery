"""FastAPI application: auth + rate-limit boundary, SPA delivery, routers, lifespan."""

import asyncio
import os
import re
import sys
import time
import webbrowser
from collections import deque
from contextlib import asynccontextmanager
from urllib.parse import parse_qs

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from vs_app import runtime, settings
from vs_app.api.routes import media as media_routes
from vs_app.api.routes import recovery as recovery_routes
from vs_app.api.routes import system as system_routes
from vs_app.core import security
from vs_app.execution import adapters, bridge
from vs_app.storage import token_file

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
    except Exception:
        pass

IS_WINDOWS = os.name == 'nt'
LOGGER = settings.LOGGER
HOST = security.LOOPBACK_HOST
PUBLIC_PATHS = {'/api/health', '/', '/index.html', '/video_studio_app.html', '/favicon.ico'}
SSE_RE = re.compile(r'^/api/jobs/[A-Za-z0-9_-]+/events/?$')
RATE_WINDOW = 60.0
RATE_BUDGET = 300
SAFE_METHODS = {'GET', 'HEAD', 'OPTIONS'}


class ApiGuard:
    """Pure-ASGI middleware: rate limit, bearer/query-token auth, security headers."""

    def __init__(self, app):
        self.app = app
        self.hits = {}
        self.lock = asyncio.Lock()

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        path = scope['path']
        api = path.startswith('/api/')
        headers = self._headers(scope)
        if not security.host_header_is_loopback(headers.get('host')):
            # DNS rebinding: a foreign name resolving to 127.0.0.1 must not reach the
            # page (it carries the token) or the API.
            await self._respond(scope, receive, send, JSONResponse(
                {'success': False, 'error': 'Forbidden: unexpected Host header.'},
                status_code=403))
            return
        origin = headers.get('origin')
        if (scope.get('method') not in SAFE_METHODS and origin is not None
                and not security.origin_is_loopback(origin)):
            await self._respond(scope, receive, send, JSONResponse(
                {'success': False, 'error': 'Forbidden: cross-origin request.'},
                status_code=403))
            return
        if api and path not in PUBLIC_PATHS:
            if not await self._rate_ok(scope):
                await self._respond(scope, receive, send, JSONResponse(
                    {'success': False, 'error': 'Too many requests — slow down.'},
                    status_code=429, headers={'Retry-After': '10'}))
                return
            if not self._authorized(scope, path, headers):
                await self._respond(scope, receive, send, JSONResponse(
                    {'success': False,
                     'error': 'Unauthorized: missing or invalid API token.'},
                    status_code=401))
                return
        await self._forward(scope, receive, send, api=api)

    async def _rate_ok(self, scope):
        client = scope.get('client') or ('unknown', 0)
        ip = client[0]
        now = time.monotonic()
        async with self.lock:
            bucket = self.hits.setdefault(ip, deque())
            while bucket and now - bucket[0] > RATE_WINDOW:
                bucket.popleft()
            if len(bucket) >= RATE_BUDGET:
                return False
            bucket.append(now)
            if len(self.hits) > 64:
                for key in [k for k, v in self.hits.items() if not v]:
                    self.hits.pop(key, None)
        return True

    @staticmethod
    def _headers(scope):
        return {key.decode('latin-1').lower(): value.decode('latin-1')
                for key, value in scope.get('headers', ())}

    @staticmethod
    def _authorized(scope, path, headers):
        token = security.bearer_token(headers.get('authorization'))
        if not token and SSE_RE.match(path):
            # EventSource cannot set headers; the SSE endpoint accepts ?token=.
            token = parse_qs(scope['query_string'].decode('latin-1')).get('token', [''])[0]
        return bool(token) and security.token_equals(settings.token(), token)

    @staticmethod
    def _wrap_send(send, api):
        injected = security.security_headers(api=api)

        async def send_wrapper(message):
            if message['type'] == 'http.response.start':
                headers = list(message.get('headers', ()))
                present = {key.lower() for key, _ in headers}
                for key, value in injected.items():
                    encoded = key.lower().encode('latin-1')
                    if encoded not in present:
                        headers.append((encoded, value.encode('latin-1')))
                message = {**message, 'headers': headers}
            await send(message)

        return send_wrapper

    async def _forward(self, scope, receive, send, api):
        await self.app(scope, receive, self._wrap_send(send, api))

    async def _respond(self, scope, receive, send, response):
        await response(scope, receive, self._wrap_send(send, api=True))


@asynccontextmanager
async def lifespan(app):
    token_file.write_private_file(settings.SETTINGS.token_path, settings.token() + '\n')
    await runtime.STORE.open()
    interrupted = await runtime.STORE.mark_interrupted()
    await runtime.JOBS.start()
    LOGGER.info('Jobs database: %s', runtime.STORE.path)
    if interrupted:
        LOGGER.warning('%s interrupted job(s) marked failed at startup', interrupted)
    await asyncio.to_thread(bridge.reap_orphan_keepalive)
    await asyncio.to_thread(bridge.ensure_wsl_alive)
    await asyncio.to_thread(adapters.reap_orphan_tools)
    try:
        yield
    finally:
        for task in list(runtime.TASKS):
            task.cancel()
        if runtime.TASKS:
            await asyncio.gather(*runtime.TASKS, return_exceptions=True)
        await runtime.JOBS.stop()
        await runtime.STORE.close()
        token_file.remove_file(settings.SETTINGS.token_path)
        bridge.stop_wsl_keepalive()
        LOGGER.info('Server stopped')


app = FastAPI(title='Video Studio', version=settings.APP_VERSION,
              docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.add_middleware(ApiGuard)
app.include_router(system_routes.router)
app.include_router(recovery_routes.router)
app.include_router(media_routes.router)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    details = []
    for error in exc.errors():
        location = '.'.join(str(part) for part in error.get('loc', ()) if part != 'body')
        message = error.get('msg', 'invalid value')
        details.append(f'{location}: {message}' if location else message)
    return JSONResponse({'success': False,
                         'error': 'Invalid request — ' + '; '.join(details)},
                        status_code=422)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    return JSONResponse({'success': False, 'error': str(exc.detail)},
                        status_code=exc.status_code)


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    LOGGER.exception('Unhandled error on %s %s', request.method, request.url.path)
    return JSONResponse({'success': False, 'error': 'Internal server error.'},
                        status_code=500)


def _page():
    try:
        with open(settings.SETTINGS.app_html_path, 'r', encoding='utf-8') as handle:
            html = handle.read()
    except OSError as exc:
        return HTMLResponse(f'video_studio_app.html could not be read: {exc}', status_code=500)
    html = html.replace('__API_TOKEN_PLACEHOLDER__', settings.token())
    html = html.replace('__APP_VERSION__', settings.APP_VERSION)
    return HTMLResponse(html, headers={'Cache-Control': 'no-store'})


@app.get('/')
def index():
    return _page()


@app.get('/index.html')
def index_html():
    return _page()


@app.get('/video_studio_app.html')
def app_page():
    return _page()


@app.get('/favicon.ico')
def favicon():
    return Response(status_code=204)


def server_is_running(port):
    import urllib.request
    try:
        with urllib.request.urlopen(
                f'http://127.0.0.1:{port}/api/health', timeout=2) as response:
            return b'"status":"ok"' in response.read(500).replace(b' ', b'')
    except Exception:
        return False


def run(port=None, distro=None, open_browser=False):
    if IS_WINDOWS:
        # Required before uvicorn creates the loop: subprocess pipes need a Proactor loop.
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    if distro:
        settings.set_distro(distro)
    port = port or settings.SETTINGS.port
    url = f'http://127.0.0.1:{port}/'
    if server_is_running(port):
        # Never restart it: that would cut off a recovery job that may be running.
        # Tools installed since it started are found anyway (media.refresh_path).
        print(f'Video Studio is already running on port {port}.')
        if open_browser:
            webbrowser.open(url)
            print(f'Opened {url} in your browser. You can close this window.')
        return 0
    tools_host = (f"WSL distro '{settings.distro()}'" if IS_WINDOWS
                  else 'native Linux tools')
    print(f'Video Studio {settings.APP_VERSION} (FastAPI) listening on {url} (loopback only).')
    print(f'   Recovery pipeline: ddrescue → TestDisk → PhotoRec ({tools_host})')
    print(f'   API token file: {settings.SETTINGS.token_path}')
    print(f'   Jobs database: {settings.jobs_db_path()}')
    print(f'   Logs: {settings.SETTINGS.log_dir}')
    if open_browser:
        webbrowser.open(url)
    uvicorn.run(app, host=HOST, port=port, loop='asyncio', http='h11',
                access_log=False, log_level=settings.SETTINGS.log_level.lower())
    return 0


if __name__ == '__main__':
    sys.exit(run(open_browser='--open-browser' in sys.argv))
