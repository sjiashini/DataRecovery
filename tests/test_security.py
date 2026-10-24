import os
import tempfile
import unittest

from vs_app.core import security
from vs_app.storage import token_file


class TokenTests(unittest.TestCase):
    def test_tokens_are_unique_and_urlsafe(self):
        first, second = security.generate_token(), security.generate_token()
        self.assertNotEqual(first, second)
        self.assertGreaterEqual(len(first), 40)
        self.assertNotIn('+', first)
        self.assertNotIn('/', first)

    def test_constant_time_comparison(self):
        self.assertTrue(security.token_equals('abc', 'abc'))
        self.assertFalse(security.token_equals('abc', 'abd'))
        self.assertFalse(security.token_equals('', ''))
        self.assertFalse(security.token_equals('abc', ''))

    def test_bearer_parsing(self):
        self.assertEqual(security.bearer_token('Bearer abc123'), 'abc123')
        self.assertEqual(security.bearer_token('bearer  abc123 '), 'abc123')
        self.assertEqual(security.bearer_token('Basic abc123'), '')
        self.assertEqual(security.bearer_token('Bearer'), '')
        self.assertEqual(security.bearer_token(None), '')

    def test_private_file_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, 'nested', 'api_token')
            token_file.write_private_file(path, 'secret-token\n')
            self.assertEqual(token_file.read_token(path), 'secret-token')
            self.assertEqual(token_file.read_token(os.path.join(temp, 'missing')), '')
            token_file.remove_file(path)
            self.assertFalse(os.path.exists(path))


class HeaderTests(unittest.TestCase):
    def test_no_cors_headers(self):
        for api in (False, True):
            headers = security.security_headers(api=api)
            self.assertFalse(any(key.lower().startswith('access-control-') for key in headers))

    def test_framing_and_mime_hardening(self):
        headers = security.security_headers()
        self.assertEqual(headers['X-Frame-Options'], 'DENY')
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(headers['Referrer-Policy'], 'no-referrer')
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertIn("object-src 'none'", headers['Content-Security-Policy'])

    def test_api_csp_denies_everything(self):
        headers = security.security_headers(api=True)
        self.assertEqual(headers['Content-Security-Policy'],
                         "default-src 'none'; frame-ancestors 'none'")

    def test_loopback_hosts(self):
        self.assertTrue(security.is_loopback_host('127.0.0.1'))
        self.assertTrue(security.is_loopback_host('localhost'))
        self.assertTrue(security.is_loopback_host('::1'))
        self.assertFalse(security.is_loopback_host('0.0.0.0'))
        self.assertFalse(security.is_loopback_host('192.168.1.10'))

    def test_host_header_must_name_loopback(self):
        for value in ('127.0.0.1:8080', 'localhost:8080', 'LOCALHOST', '[::1]:8080',
                      '127.0.0.1'):
            self.assertTrue(security.host_header_is_loopback(value), value)
        for value in ('evil.example:8080', 'localhost.evil.example', '127.0.0.1.nip.io',
                      '0.0.0.0:8080', '[::1', '', None):
            self.assertFalse(security.host_header_is_loopback(value), value)

    def test_origin_must_be_loopback(self):
        self.assertTrue(security.origin_is_loopback('http://127.0.0.1:8080'))
        self.assertTrue(security.origin_is_loopback('http://localhost:8080'))
        self.assertTrue(security.origin_is_loopback('http://[::1]:8080'))
        for value in ('null', 'http://evil.example', 'file://', 'ftp://127.0.0.1',
                      'http://127.0.0.1.evil.example', ''):
            self.assertFalse(security.origin_is_loopback(value), value)


if __name__ == '__main__':
    unittest.main()
