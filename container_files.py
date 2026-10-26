"""Host transport for file-panel operations; never follows sandbox host paths."""
import base64
import io
import json
import sandbox_runtime
import store


def operation(runtime_id, action, path='', **kwargs):
    try:
        with sandbox_runtime.runtime_operation(runtime_id):
            code, raw, error = sandbox_runtime.docker_operation(runtime_id,
                ['/opt/venv/bin/python', '-I', '/opt/lumen/sandbox_files.py'],
                json.dumps({'action': action, 'path': path, **kwargs}).encode(),
                timeout=60, limit=70*1024*1024 if action == 'download' else 1024*1024)
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
    import workspace_service
    if not store.load(conv_id):
        return {'error': 'Conversation not found'}, 404
    saved = []
    limit = min(workspace_service._file_limits()[2], 50*1024*1024)
    for item in files:
        if not item or not item.filename:
            continue
        data = item.stream.read(limit + 1)
        if len(data) > limit:
            return {'error': 'Upload exceeds size limit'}, 413
        result, status = operation(store.runtime_id(conv_id), 'upload', name=workspace_service.safe_upload_name(item.filename), data=base64.b64encode(data).decode())
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
