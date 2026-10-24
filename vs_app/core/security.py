"""Authentication and browser-hardening helpers for the loopback control plane."""

import hmac
import secrets
from urllib.parse import urlsplit

LOOPBACK_HOST = '127.0.0.1'


def generate_token():
    return secrets.token_urlsafe(32)


def token_equals(expected, provided):
    if not expected or not provided:
        return False
    return hmac.compare_digest(str(expected).encode('utf-8'), str(provided).encode('utf-8'))


def bearer_token(header_value):
    if not header_value:
        return ''
    parts = header_value.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != 'bearer':
        return ''
    return parts[1].strip()


def is_loopback_host(host):
    return host in ('127.0.0.1', '::1', 'localhost')


def host_header_name(value):
    """Hostname part of a Host header ('localhost:8080', '[::1]:8080'), lowercased."""
    value = str(value or '').strip().lower()
    if value.startswith('['):
        end = value.find(']')
        return value[1:end] if end > 0 else ''
    return value.split(':', 1)[0]


def host_header_is_loopback(value):
    """Reject DNS-rebinding requests: the browser sends the attacker's name as Host."""
    return is_loopback_host(host_header_name(value))


def origin_is_loopback(value):
    """True for an http(s) Origin on a loopback host; 'null' and other sites are refused."""
    try:
        parts = urlsplit(str(value or '').strip())
    except ValueError:
        return False
    return parts.scheme in ('http', 'https') and is_loopback_host(parts.hostname or '')


def security_headers(api=False):
    """Headers applied to every response. The page is same-origin, so no CORS is allowed."""
    headers = {
        'X-Content-Type-Options': 'nosniff',
        'X-Frame-Options': 'DENY',
        'Referrer-Policy': 'no-referrer',
        'Cross-Origin-Opener-Policy': 'same-origin',
        'Cross-Origin-Resource-Policy': 'same-origin',
        'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
        'Cache-Control': 'no-store',
    }
    if api:
        headers['Content-Security-Policy'] = "default-src 'none'; frame-ancestors 'none'"
    else:
        headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; connect-src 'self'; "
            "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
        )
    return headers
