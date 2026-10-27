"""Host transport for file-panel operations; never follows sandbox host paths."""
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


def operation(runtime_id, action, path='', *, data=None, **kwargs):
    try:
        limits = file_limits()
        # JSON can escape each source byte as six ASCII bytes.
        binary = action in ('download', 'pdf')
        response_limit = MAX_UPLOAD + 4096 if binary else max(
            1024*1024, 6*limits['max_file_preview_bytes'] + 4096)
        if action == 'list':
            response_limit = max(response_limit, limits['max_file_list_entries'] * 20000)
        if action == 'preview':
            # Up to five bounded JPEGs from the View worker, plus extracted text.
            response_limit = max(response_limit, 16 * 1024 * 1024)
        payload = json.dumps({'action': action, 'path': path, 'limits': limits, **kwargs}).encode()
        if action == 'upload':
            if not isinstance(data, bytes) or len(data) > limits['max_upload_bytes']:
                raise ValueError('Invalid or oversized upload')
            payload = (payload + b'\n', data)
        with sandbox_runtime.runtime_operation(runtime_id):
            code, raw, error = sandbox_runtime.docker_operation(runtime_id,
                ['/opt/venv/bin/python', '-I', '/opt/lumen/sandbox_files.py'],
                payload,
                timeout=60, limit=response_limit)
        if code:
            raise ValueError('Container file operation failed')
        header, separator, data = raw.partition(b'\n') if binary else (raw, None, None)
        if binary and not separator:
            raise ValueError('Invalid binary response')
        result = json.loads(header)
        if not isinstance(result, dict):
            raise ValueError('Invalid file response')
        status = result.pop('status', 200)
        if binary and status in (200, 206) and 'error' not in result:
            if result.pop('length', None) != len(data):
                raise ValueError('Incomplete file response')
            result['data'] = data
        return result, status
    except Exception as exc:
        return {'error': str(exc)[:1000]}, 400


def conversation(conv_id, action, path='', **kwargs):
    data = store.load(conv_id)
    if not data:
        return {'error': 'Conversation not found'}, 404
    runtime_id = store.runtime_id(conv_id, data)
    del data
    return operation(runtime_id, action, path, **kwargs)


def folder(folder_id, action, path='', **kwargs):
    if not store.get_folder(folder_id):
        return {'error': 'Folder not found'}, 404
    return operation('folder_' + folder_id, action, path, **kwargs)


def uploads(conv_id, files):
    data = store.load(conv_id)
    if not data:
        return {'error': 'Conversation not found'}, 404
    runtime_id = store.runtime_id(conv_id, data)
    del data
    saved = []
    limit = file_limits()['max_upload_bytes']
    for item in files:
        if not item or not item.filename:
            continue
        data = item.stream.read(limit + 1)
        if len(data) > limit:
            return {'error': 'Upload exceeds size limit'}, 413
        result, status = operation(runtime_id, 'upload', name=safe_upload_name(item.filename), data=data)
        del data
        if status != 200:
            return result, status
        saved.append(result)
    return ({'files': saved}, 200) if saved else ({'error': 'No valid files uploaded'}, 400)


def download_response(result, status):
    from flask import jsonify, send_file
    if status != 200:
        return jsonify(result), status
    try:
        data = result['data']
        if not isinstance(data, bytes) or len(data) > MAX_UPLOAD:
            raise ValueError('Download too large')
        name = str(result['name']).replace('\\', '/').split('/')[-1]
        return send_file(io.BytesIO(data), as_attachment=True, download_name=name)
    except (KeyError, ValueError):
        return jsonify({'error': 'Invalid download response'}), 400


def pdf_response(result, status):
    from flask import Response, jsonify
    if status not in (200, 206, 304, 416):
        return jsonify(result), status
    try:
        total = int(result['total'])
        if not 5 <= total <= 20 * 1024 * 1024:
            raise ValueError('Invalid PDF size')
        headers = {'Accept-Ranges': 'bytes', 'ETag': result['etag'],
                   'Cache-Control': 'private, no-cache', 'X-Frame-Options': 'SAMEORIGIN',
                   'X-Content-Type-Options': 'nosniff'}
        if status == 416:
            headers['Content-Range'] = f'bytes */{total}'
        data = b''
        if status in (200, 206):
            data = result['data']
            start, end = int(result['start']), int(result['end'])
            if not 0 <= start <= end < total or len(data) != end - start + 1:
                raise ValueError('Invalid PDF range')
            if status == 206:
                headers['Content-Range'] = f'bytes {start}-{end}/{total}'
        response = Response(data, status=status, mimetype='application/pdf', headers=headers)
        response.headers.set('Content-Disposition', 'inline', filename=result['name'])
        return response
    except (KeyError, ValueError, TypeError):
        return jsonify({'error': 'Invalid PDF response'}), 400
