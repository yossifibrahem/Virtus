"""Host transport for file-panel operations; never follows sandbox host paths."""
import base64
import io
import json
import re

import advanced_config
import sandbox_runtime
import store


MAX_UPLOAD = 50 * 1024 * 1024


def file_limits():
    cfg = advanced_config.load_advanced_config()
    return {key: min(max(1, int(cfg[key])), ceiling) for key, ceiling in (
        ('max_file_preview_bytes', 4 * 1024 * 1024),
        ('max_file_list_entries', 5000),
        ('max_upload_bytes', MAX_UPLOAD),
    )}


def safe_upload_name(name):
    filename = str(name or '').replace('\\', '/').rsplit('/', 1)[-1]
    return re.sub(r'[^A-Za-z0-9._ -]+', '_', filename).strip(' ._') or 'file'


def operation(runtime_id, action, path='', **kwargs):
    try:
        limits = file_limits()
        # JSON can escape each source byte as six ASCII bytes.
        response_limit = 70*1024*1024 if action == 'download' else max(
            1024*1024, 6*limits['max_file_preview_bytes'] + 4096)
        if action == 'list':
            response_limit = max(response_limit, limits['max_file_list_entries'] * 20000)
        with sandbox_runtime.runtime_operation(runtime_id):
            code, raw, error = sandbox_runtime.docker_operation(runtime_id,
                ['/opt/venv/bin/python', '-I', '/opt/lumen/sandbox_files.py'],
                json.dumps({'action': action, 'path': path, 'limits': limits, **kwargs}).encode(),
                timeout=60, limit=response_limit)
        if code:
            raise ValueError('Container file operation failed')
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError('Invalid file response')
        return result, result.pop('status', 200)
    except Exception as exc:
        return {'error': str(exc)[:1000]}, 400


def conversation(conv_id, action, path=''):
    if not store.load(conv_id):
        return {'error': 'Conversation not found'}, 404
    return operation(store.runtime_id(conv_id), action, path)


def folder(folder_id, action, path=''):
    if not store.get_folder(folder_id):
        return {'error': 'Folder not found'}, 404
    return operation('folder_' + folder_id, action, path)


def uploads(conv_id, files):
    if not store.load(conv_id):
        return {'error': 'Conversation not found'}, 404
    saved = []
    runtime_id = store.runtime_id(conv_id)
    limit = file_limits()['max_upload_bytes']
    for item in files:
        if not item or not item.filename:
            continue
        data = item.stream.read(limit + 1)
        if len(data) > limit:
            return {'error': 'Upload exceeds size limit'}, 413
        encoded = base64.b64encode(data).decode()
        del data
        result, status = operation(runtime_id, 'upload', name=safe_upload_name(item.filename), data=encoded)
        del encoded
        if status != 200:
            return result, status
        saved.append(result)
    return ({'files': saved}, 200) if saved else ({'error': 'No valid files uploaded'}, 400)


def download_response(result, status):
    from flask import jsonify, send_file
    if status != 200:
        return jsonify(result), status
    try:
        encoded = result['data']
        if not isinstance(encoded, str) or len(encoded) > 70*1024*1024:
            raise ValueError('Invalid download')
        data = base64.b64decode(encoded, validate=True)
        if len(data) > 50*1024*1024:
            raise ValueError('Download too large')
        name = str(result['name']).replace('\\', '/').split('/')[-1]
        return send_file(io.BytesIO(data), as_attachment=True, download_name=name)
    except (KeyError, ValueError):
        return jsonify({'error': 'Invalid download response'}), 400
