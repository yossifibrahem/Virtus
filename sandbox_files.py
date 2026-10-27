"""Workspace transport, executed only inside Docker. All paths confined to /workspace."""
import json
import importlib.util
import hashlib
import mimetypes
import os
from pathlib import Path
import re
import stat
import shutil
import sys

ROOT = Path('/workspace')
MAX_UPLOAD = 50 * 1024 * 1024
VISUAL_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tiff', '.pdf', '.ipynb'}
PDF_LIMIT = 20 * 1024 * 1024


def pdf_file(target, req, *, metadata_only=False):
    fd = os.open(target, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('Only regular files are supported')
        if info.st_size > PDF_LIMIT:
            raise ValueError('PDF exceeds 20 MiB preview limit')
        if target.suffix.lower() != '.pdf' or source.read(5) != b'%PDF-':
            raise ValueError('Invalid PDF file')
        if metadata_only:
            return
        version = f'{info.st_dev}:{info.st_ino}:{info.st_size}:{info.st_mtime_ns}:{info.st_ctime_ns}'
        etag = '"' + hashlib.sha256(version.encode()).hexdigest() + '"'
        meta = {'name': target.name, 'total': info.st_size, 'etag': etag}
        if req.get('if_none_match') in (etag, '*'):
            return {**meta, 'status': 304}
        start, end = 0, info.st_size - 1
        byte_range = req.get('byte_range')
        if req.get('if_range') and req['if_range'] != etag:
            byte_range = None
        if byte_range:
            match = re.fullmatch(r'bytes=([0-9]*)-([0-9]*)', byte_range)
            if not match or not any(match.groups()):
                return {**meta, 'status': 416}
            first, last = match.groups()
            if first:
                start = int(first)
                end = min(int(last), end) if last else end
            else:
                start = max(0, info.st_size - int(last))
                if int(last) == 0:
                    return {**meta, 'status': 416}
            if start > end:
                return {**meta, 'status': 416}
        source.seek(start)
        data = source.read(end - start + 1)
        if len(data) != end - start + 1:
            raise ValueError('PDF changed while reading; retry preview')
        return {**meta, 'start': start, 'end': end, 'data': data,
                'status': 206 if byte_range else 200}


def visual_preview(target, meta, req):
    if target.suffix.lower() == '.pdf':
        try:
            pdf_file(target, req, metadata_only=True)
        except ValueError as exc:
            return {**meta, 'content': None, 'previewable': False, 'preview_error': str(exc)}
        return {**meta, 'content': None, 'previewable': True, 'preview_kind': 'pdf'}
    # Load the trusted sibling explicitly: Python -I excludes the script directory.
    spec = importlib.util.spec_from_file_location('lumen_view_worker', Path(__file__).with_name('sandbox_tools.py'))
    viewer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(viewer)
    try:
        result = viewer.view(target, req.get('range'))
    except Exception as exc:
        return {**meta, 'content': None, 'previewable': False, 'preview_error': str(exc)[:1000]}
    kind = 'notebook' if target.suffix.lower() == '.ipynb' else 'image'
    return {**meta, 'content': result['text'] if kind == 'notebook' else None,
            'previewable': True, 'preview_kind': kind, 'images': result.get('images', []),
            **{key: result[key] for key in ('start', 'end', 'total') if key in result}}


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


def _workspace_mounts():
    """Inspect actual mount locations, including renamed mount ancestors."""
    try:
        with open('/proc/self/mountinfo') as source:
            mounts = []
            for line in source:
                raw = line.split()[4]
                path = Path(re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), raw))
                if path != ROOT and ROOT in path.parents:
                    mounts.append(path)
            return mounts
    except FileNotFoundError:
        if sys.platform.startswith('linux'):
            raise ValueError('Cannot verify mounted directories before deletion')
        return []  # Native worker tests; the deployed worker runs inside Linux.


def delete_managed(value):
    """Delete an explicitly selected managed path without following symlinks.

    Descriptor-relative traversal keeps concurrent symlink swaps from redirecting
    deletion into host shares. The workspace root and reserved mounts tree are
    never deletion targets. This worker runs inside Docker, not on the host.
    """
    raw = str(value or '').replace('\\', '/')
    if raw.startswith('/workspace/'):
        raw = raw[len('/workspace/'):]
    elif raw.startswith('/'):
        raise ValueError('Only managed workspace paths can be deleted')
    parts = [part for part in raw.split('/') if part != '.']
    if not parts or any(part in ('', '..') for part in parts) or parts[0].lower() == 'mounts':
        raise ValueError('The workspace root and mounted host directories cannot be deleted')
    target = ROOT.joinpath(*parts)
    for mount in _workspace_mounts():
        if target == mount or mount in target.parents or target in mount.parents:
            raise ValueError('Mounted host directories and their ancestors cannot be deleted')
    parent = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
        name = parts[-1]
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            if not shutil.rmtree.avoids_symlink_attacks:
                raise ValueError('Safe directory deletion is unavailable')
            shutil.rmtree(name, dir_fd=parent)
        else:
            os.unlink(name, dir_fd=parent)
    finally:
        os.close(parent)
    return {'deleted': '/workspace/' + '/'.join(parts)}


def entry(path, preview_limit, *, with_version=False):
    target = path.resolve()
    if target != ROOT and ROOT not in target.parents:
        raise ValueError('Path escapes workspace')
    s = target.stat()
    is_dir = stat.S_ISDIR(s.st_mode)
    if not is_dir and not stat.S_ISREG(s.st_mode):
        raise ValueError('Only regular files and directories are supported')
    rel = path.relative_to(ROOT).as_posix()
    mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
    return {'name': path.name, 'path': '/workspace/' + rel, 'relative_path': rel,
            'type': 'directory' if is_dir else 'file', 'size': None if is_dir else s.st_size,
            'modified': s.st_mtime, 'mime_type': mime, 'previewable': not is_dir and s.st_size <= preview_limit,
            **({'version': f'{s.st_dev}:{s.st_ino}:{s.st_size}:{s.st_mtime_ns}:{s.st_ctime_ns}'} if with_version else {})}


def execute(req):
    limits = req.get('limits', {})
    preview_limit = min(max(1, int(limits.get('max_file_preview_bytes', 512*1024))), 4*1024*1024)
    list_limit = min(max(1, int(limits.get('max_file_list_entries', 500))), 5000)
    upload_limit = min(max(1, int(limits.get('max_upload_bytes', MAX_UPLOAD))), MAX_UPLOAD)
    action = req['action']
    if action == 'delete':
        return delete_managed(req.get('path'))
    target = resolve(req.get('path'))
    if action == 'pdf':
        return pdf_file(target, req)
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
        if action == 'preview':
            meta = entry(target, preview_limit, with_version=True)
            meta['version'] += f':{preview_limit}:{req.get("range") or ""}'
            if req.get('version') == meta['version']:
                return {'unchanged': True}
        if action == 'preview' and target.suffix.lower() in VISUAL_SUFFIXES:
            return visual_preview(target, meta, req)
        fd = os.open(target, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as f:
            info = os.fstat(f.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('Only regular files are supported')
            limit = preview_limit if action == 'preview' else MAX_UPLOAD
            if info.st_size > limit:
                if action == 'download':
                    raise ValueError('Download exceeds 50 MiB limit')
                return {**meta, 'content': None, 'previewable': False}
            data = f.read(info.st_size + 1)
            if len(data) != info.st_size:
                raise ValueError('File changed while reading; retry')
        if action == 'download':
            return {'name': target.name, 'data': data}
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
        data = req['data']
        if not isinstance(data, bytes):
            raise ValueError('Invalid upload data')
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


def write_result(result, stream):
    data = result.get('data')
    if isinstance(data, bytes):
        result = {**result, 'length': len(data)}
    stream.write(json.dumps({key: value for key, value in result.items() if key != 'data'}).encode() + b'\n')
    if isinstance(data, bytes):
        stream.write(data)


def read_request(stream):
    # Only uploads have a binary body after their bounded JSON header.
    req = json.loads(stream.readline(65536))
    if req.get('action') == 'upload':
        req['data'] = stream.read(MAX_UPLOAD + 1)
    return req


if __name__ == '__main__':
    try:
        write_result(execute(read_request(sys.stdin.buffer)), sys.stdout.buffer)
    except FileNotFoundError:
        print(json.dumps({'error': 'File not found', 'status': 404}))
    except Exception as exc:
        print(json.dumps({'error': str(exc)[:1000], 'status': 400}))
