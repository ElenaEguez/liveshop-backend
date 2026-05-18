"""Helpers for building absolute URLs behind reverse proxies (HTTPS)."""
from urllib.parse import urlparse

from django.conf import settings


def _https_trusted_host(host: str) -> bool:
    if not host:
        return False
    for origin in getattr(settings, 'CSRF_TRUSTED_ORIGINS', []):
        if not origin.startswith('https://'):
            continue
        if urlparse(origin).netloc == host:
            return True
    return False


def secure_absolute_uri(request, path: str | None) -> str | None:
    """
    Build an absolute media URL, preferring HTTPS when the site is served over TLS
    (directly or via X-Forwarded-Proto) or the host is in CSRF_TRUSTED_ORIGINS.
    """
    if not path:
        return path

    if path.startswith('//'):
        path = f'https:{path}'

    if path.startswith('http://') or path.startswith('https://'):
        uri = path
    elif request is not None:
        uri = request.build_absolute_uri(path)
    else:
        return path

    if not uri.startswith('http://'):
        return uri

    host = urlparse(uri).netloc
    use_https = False
    if request is not None:
        if request.is_secure():
            use_https = True
        elif request.META.get('HTTP_X_FORWARDED_PROTO', '').lower() == 'https':
            use_https = True
    if _https_trusted_host(host):
        use_https = True

    if use_https:
        return 'https://' + uri[len('http://'):]
    return uri
