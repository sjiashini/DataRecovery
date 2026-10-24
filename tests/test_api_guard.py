import asyncio
import unittest

from vs_app import settings
from vs_app.main import ApiGuard


async def inner_app(scope, receive, send):
    await send({'type': 'http.response.start', 'status': 200, 'headers': []})
    await send({'type': 'http.response.body', 'body': b'ok'})


def call(path, method='GET', host='127.0.0.1:8080', origin=None, token=None):
    headers = []
    if host is not None:
        headers.append((b'host', host.encode('latin-1')))
    if origin is not None:
        headers.append((b'origin', origin.encode('latin-1')))
    if token is not None:
        headers.append((b'authorization', f'Bearer {token}'.encode('latin-1')))
    scope = {'type': 'http', 'method': method, 'path': path, 'query_string': b'',
             'headers': headers, 'client': ('127.0.0.1', 50000)}
    messages = []

    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}

    async def send(message):
        messages.append(message)

    asyncio.run(ApiGuard(inner_app)(scope, receive, send))
    return messages[0]['status']


class HostCheckTests(unittest.TestCase):
    def test_rebound_host_cannot_load_the_tokenized_page(self):
        self.assertEqual(call('/', host='evil.example:8080'), 403)
        self.assertEqual(call('/', host='localhost:8080'), 200)

    def test_rebound_host_is_refused_even_with_a_token(self):
        self.assertEqual(call('/api/status', host='evil.example:8080',
                              token=settings.token()), 403)

    def test_missing_host_is_refused(self):
        self.assertEqual(call('/', host=None), 403)


class OriginCheckTests(unittest.TestCase):
    def test_cross_origin_post_is_refused(self):
        self.assertEqual(call('/api/recover_all', method='POST',
                              origin='http://evil.example', token=settings.token()), 403)
        self.assertEqual(call('/api/recover_all', method='POST', origin='null',
                              token=settings.token()), 403)

    def test_same_origin_and_scripted_posts_are_allowed(self):
        self.assertEqual(call('/api/recover_all', method='POST',
                              origin='http://127.0.0.1:8080', token=settings.token()), 200)
        self.assertEqual(call('/api/recover_all', method='POST',
                              token=settings.token()), 200)

    def test_token_is_still_required(self):
        self.assertEqual(call('/api/recover_all', method='POST',
                              origin='http://127.0.0.1:8080'), 401)


if __name__ == '__main__':
    unittest.main()
