"""Workspace transport, executed only inside Docker. All paths confined to /workspace."""
import base64
import json
import mimetypes
import os
from pathlib import Path
import stat
import sys

ROOT = Path('/workspace')
MAX_UPLOAD = 50 * 1024 * 1024


def resolve(value):
    raw = str(value or '').replace('\\', '/')
    if raw in ('', '.', '/', '/workspace'):
        raw = ''
    elif raw.startswith('/workspace/'):
        raw = raw[len('/workspace/'):]
    elif raw.startswith('/'):
        raise ValueError('Only /workspace paths are available')
    if '..' in raw.split('/'):
        raise ValueError('Parent traversal is not allowed')
    path = (ROOT / raw).resolve()
    if path != ROOT and ROOT not in path.parents:
        raise ValueError('Path escapes workspace')
    return path


def workspace_path(path):
    rel = path.relative_to(ROOT).as_posix()
    return '/workspace' + ('' if rel == '.' else '/' + rel)


def entry(path, preview_limit):
    target = path.resolve()
    if target != ROOT and ROOT not in target.parents:
        raise ValueError('Path escapes workspace')
    s = target.stat()
    is_dir = stat.S_ISDIR(s.st_mode)
    if not is_dir and not stat.S_ISREG(s.st_mode):
        raise ValueError('Special files are not supported')
    rel = path.relative_to(ROOT).as_posix()
    mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
    return {'name': path.name, 'path': '/workspace/' + rel, 'relative_path': rel,
            'type': 'directory' if is_dir else 'file', 'size': None if is_dir else s.st_size,
            'modified': s.st_mtime, 'mime_type': mime, 'previewable': not is_dir and s.st_size <= preview_limit}


def execute(req):
    limits = req.get('limits', {})
    preview_limit = min(max(1, int(limits.get('max_file_preview_bytes', 512*1024))), 4*1024*1024)
    list_limit = min(max(1, int(limits.get('max_file_list_entries', 500))), 5000)
    upload_limit = min(max(1, int(limits.get('max_upload_bytes', MAX_UPLOAD))), MAX_UPLOAD)
    action = req['action']
    target = resolve(req.get('path'))
    if action == 'list':
        entries = []
        with os.scandir(target) as scan:
            for child in scan:
                if len(entries) >= list_limit:
                    break
                try:
                    entries.append(entry(Path(child.path), preview_limit))
                except (ValueError, OSError):
                    continue
            else:
                scan = None
        entries.sort(key=lambda e: (e['type'] != 'directory', e['name'].lower()))
        rel = target.relative_to(ROOT).as_posix()
        return {'path': workspace_path(target), 'relative_path': '' if rel == '.' else rel,
                'parent': None if target == ROOT else workspace_path(target.parent), 'entries': entries,
                'limit': list_limit, 'truncated': scan is not None}
    if action in ('preview', 'download'):
        fd = os.open(target, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                raise ValueError('Only regular files are supported')
            limit = preview_limit if action == 'preview' else MAX_UPLOAD
            data = f.read(limit + 1)
        if action == 'download':
            if len(data) > limit:
                raise ValueError('Download exceeds 50 MiB limit')
            return {'name': target.name, 'data': base64.b64encode(data).decode()}
        meta = entry(target, preview_limit)
        try:
            if len(data) > limit or b'\0' in data:
                raise ValueError('Not previewable')
            content = data.decode('utf-8')
        except (ValueError, UnicodeDecodeError):
            content = None
        return {**meta, 'content': content, 'previewable': content is not None}
    if action == 'upload':
        upload = resolve('uploads')
        upload.mkdir(parents=True, exist_ok=True)
        filename = str(req['name']).replace('\\', '/').rsplit('/', 1)[-1]
        if filename in ('', '.', '..'):
            raise ValueError('Invalid upload name')
        data = base64.b64decode(req['data'], validate=True)
        if len(data) > upload_limit:
            raise ValueError('Upload exceeds size limit')
        for n in range(1000):
            name = filename if n == 0 else f'{Path(filename).stem}-{n}{Path(filename).suffix}'
            path = upload / name
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
            except FileExistsError:
                continue
            with os.fdopen(fd, 'wb') as f:
                f.write(data)
            return {'name': name, 'size': len(data), 'path': '/workspace/uploads/' + name}
        raise ValueError('Unable to allocate upload name')
    raise ValueError('Unknown file operation')


if __name__ == '__main__':
    try:
        req = json.loads(sys.stdin.buffer.read(70 * 1024 * 1024))
        print(json.dumps(execute(req)))
    except FileNotFoundError:
        print(json.dumps({'error': 'File not found', 'status': 404}))
    except Exception as exc:
        print(json.dumps({'error': str(exc)[:1000], 'status': 400}))
