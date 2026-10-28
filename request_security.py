"""Same-origin session protection for the local desktop/web application."""
import hmac
import os
import secrets
from urllib.parse import urlsplit
from flask import jsonify, request, session


def install(app):
    @app.before_request
    def protect():
        # Test clients may explicitly disable request protection in their fixture.
        if app.config.get('DISABLE_REQUEST_SECURITY', False):
            return None
        host = (urlsplit(request.host_url).hostname or '').lower()
        allowed = {'localhost', '127.0.0.1', '::1', *filter(None, os.getenv('VIRTUS_ALLOWED_HOSTS', '').split(','))}
        if host not in allowed:
            return jsonify({'error': 'Untrusted Host header'}), 403
        origin = request.headers.get('Origin')
        if origin and origin != request.host_url.rstrip('/'):
            return jsonify({'error': 'Cross-origin request denied'}), 403
        if request.headers.get('Sec-Fetch-Site') == 'cross-site':
            return jsonify({'error': 'Cross-site request denied'}), 403
        if request.path == '/' and request.method == 'GET':
            session.setdefault('client_id', secrets.token_hex(32))
            session.setdefault('csrf', secrets.token_hex(32))
        if request.path.startswith('/api/'):
            if not session.get('client_id'):
                return jsonify({'error': 'Open the application before using its API'}), 403
            if request.method not in ('GET', 'HEAD', 'OPTIONS'):
                token = request.headers.get('X-Virtus-CSRF', '')
                if not hmac.compare_digest(token, session.get('csrf', 'invalid')):
                    return jsonify({'error': 'Invalid request token; reload the application'}), 403

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers.setdefault('X-Frame-Options', 'DENY')
        response.headers['Referrer-Policy'] = 'same-origin'
        return response
