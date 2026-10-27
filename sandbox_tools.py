"""Container-only worker. Never import this module to perform host file operations."""
from __future__ import annotations
import base64
import io
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile

TEXT_LIMIT = 32768
FILE_LIMIT = 1024 * 1024
MEDIA_LIMIT = 20 * FILE_LIMIT


def bounded(text, limit=TEXT_LIMIT):
    raw = text.encode('utf-8')
    if len(raw) <= limit:
        return text
    return raw[:limit - 160].decode('utf-8', 'ignore') + '\n[truncated; request a narrower range]'


def regular(path):
    # O_NONBLOCK prevents a swapped FIFO from hanging before fstat.
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ValueError('Only regular files are supported')
    return os.fdopen(fd, 'rb')


def read_bytes(path, limit):
    with regular(path) as f:
        if os.fstat(f.fileno()).st_size > limit:
            raise ValueError(f'File exceeds {limit} byte limit; use Bash for larger files')
        data = f.read(limit + 1)
    if len(data) > limit:
        raise ValueError('File grew beyond the input limit')
    return data


def selection(value, default, maximum):
    if value is None:
        return 1, default
    if not isinstance(value, str) or not re.fullmatch(r'[1-9][0-9]*:[1-9][0-9]*', value):
        raise ValueError('range must be inclusive start:end, starting at 1')
    start, end = map(int, value.split(':'))
    if end < start or end - start + 1 > maximum or end > 10000000:
        raise ValueError(f'Invalid range; select at most {maximum} items')
    return start, end


def image_block(raw):
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = 25000000
    with Image.open(io.BytesIO(raw)) as image:
        if image.width * image.height > 25000000:
            raise ValueError('Image exceeds 25 million pixels')
        image.seek(0)
        image.thumbnail((1600, 1600))
        output = io.BytesIO()
        image.convert('RGB').save(output, format='JPEG', quality=85)
    return {'mime': 'image/jpeg', 'data': base64.b64encode(output.getvalue()).decode('ascii')}


def view(path, range_value=None):
    path = Path(path)
    if path.is_dir():
        if range_value is not None:
            raise ValueError('range is not supported for directories')
        lines = []
        def visit(directory, depth):
            # scandir avoids materializing enormous directories.
            with os.scandir(directory) as entries:
                for entry in entries:
                    if entry.name.startswith('.') or entry.name == 'node_modules':
                        continue
                    if len(lines) >= 200:
                        return False
                    is_dir = entry.is_dir(follow_symlinks=False)
                    lines.append('  ' * depth + entry.name + ('/' if is_dir else ''))
                    if is_dir and depth == 0 and not visit(entry.path, 1):
                        return False
            return True
        complete = visit(path, 0)
        return {'text': bounded('\n'.join(lines) + ('' if complete else '\n[truncated at 200 entries]'))}
    suffix = path.suffix.lower()
    if suffix in {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tiff'}:
        if range_value is not None:
            raise ValueError('range is not supported for images')
        return {'text': 'Image attached. Requires a vision-capable model.', 'images': [image_block(read_bytes(path, MEDIA_LIMIT))]}
    if suffix == '.pdf':
        import fitz
        start, end = selection(range_value, 1, 5)
        with fitz.open(stream=read_bytes(path, MEDIA_LIMIT), filetype='pdf') as doc:
            if doc.needs_pass:
                raise ValueError('PDF is encrypted')
            if start > len(doc):
                raise ValueError(f'PDF has {len(doc)} pages')
            texts, images = [], []
            for number in range(start - 1, min(end, len(doc))):
                page = doc[number]
                texts.append(f'Page {number + 1}:\n' + bounded(page.get_text()))
                scale = min(1.5, 1600 / max(page.rect.width, page.rect.height, 1))
                pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                images.append(image_block(pix.tobytes('png')))
            return {'text': bounded('\n'.join(texts) + f'\n[{len(doc)} pages total]'), 'images': images,
                    'start': start, 'end': min(end, len(doc)), 'total': len(doc)}
    if suffix == '.ipynb':
        start, end = selection(range_value, 20, 200)
        notebook = json.loads(read_bytes(path, MEDIA_LIMIT))
        cells = notebook.get('cells')
        if not isinstance(cells, list):
            raise ValueError('Invalid notebook: missing cells')
        if start > len(cells) and cells:
            raise ValueError(f'Notebook has {len(cells)} cells')
        texts, images = [], []
        for number, cell in enumerate(cells[start - 1:end], start):
            source = cell.get('source', '')
            texts.append(f'Cell {number} ({cell.get("cell_type", "unknown")}):\n' + bounded(''.join(source)))
            for output in cell.get('outputs', []):
                data = output.get('data', {})
                texts.append(bounded(''.join(output.get('text', data.get('text/plain', '')))))
                if 'image/png' in data and len(images) < 5:
                    images.append(image_block(base64.b64decode(''.join(data['image/png']), validate=True)))
        return {'text': bounded('\n'.join(texts) + f'\n[{len(cells)} cells total]'), 'images': images,
                'start': start, 'end': min(end, len(cells)), 'total': len(cells)}
    start, end = selection(range_value, 200, 200)
    lines, used = [], 0
    with regular(path) as f:
        number = 0
        while number < end:
            # Read each logical line in bounded chunks, including when skipping.
            chunk = f.readline(TEXT_LIMIT + 1)
            if not chunk:
                break
            number += 1
            prefix = chunk[:TEXT_LIMIT]
            long_line = len(chunk.rstrip(b'\n')) > TEXT_LIMIT
            while chunk and not chunk.endswith(b'\n'):
                chunk = f.readline(TEXT_LIMIT + 1)
                # EOF (or just the line terminator) discards no text.
                long_line = long_line or bool(chunk.rstrip(b'\n'))
            if number < start:
                continue
            if b'\x00' in prefix:
                raise ValueError('Unsupported binary file')
            try:
                text = prefix.decode('utf-8')
            except UnicodeDecodeError:
                if long_line:
                    text = prefix.decode('utf-8', 'ignore')
                else:
                    raise ValueError('File is not UTF-8 text')
            entry = f'{number}: {text.rstrip(chr(10))}' + (' [line truncated]' if long_line else '')
            lines.append(entry)
            used += len(entry.encode('utf-8'))
            if used >= TEXT_LIMIT:
                break
        more = bool(f.read(1))
    if more:
        lines.append(f'[more content; continue with range {number + 1}:{number + 200}]')
    return {'text': bounded('\n'.join(lines))}


def fingerprint(path):
    s = path.stat()
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns


def write(path, content, old_text=None, replace_all=False):
    path = Path(path).resolve()
    original = None
    mode = 0o644
    if path.exists():
        if not path.is_file():
            raise ValueError('Target must be a regular file')
        original = fingerprint(path)
        mode = stat.S_IMODE(path.stat().st_mode) & 0o777
    count = None
    if old_text is not None:
        if not old_text:
            raise ValueError('old_text must not be empty')
        raw = read_bytes(path, FILE_LIMIT)
        text = raw.decode('utf-8')
        count = text.count(old_text)
        if not count:
            raise ValueError('Exact text not found; reread the file')
        if count != 1 and not replace_all:
            raise ValueError(f'{count} matches found; provide unique text or replace_all=true')
        content = text.replace(old_text, content, -1 if replace_all else 1)
    data = content.encode('utf-8')
    if len(data) > FILE_LIMIT:
        raise ValueError('Text exceeds 1 MiB; use Bash for larger files')
    path.parent.mkdir(parents=True, exist_ok=True)
    def unchanged():
        if (fingerprint(path) if path.exists() else None) != original:
            raise ValueError('File changed concurrently; reread it before editing')
    if str(path) == '/memory.md':
        unchanged()
        fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(fd, 'wb') as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                raise ValueError('Memory must be a regular file')
            unchanged()
            f.write(data)
            f.truncate()
            f.flush()
            os.fsync(f.fileno())
    else:
        fd, temp = tempfile.mkstemp(prefix='.lumen-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(data)
                os.fchmod(f.fileno(), mode)
                f.flush()
                os.fsync(f.fileno())
            unchanged()
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
    return {'text': f'{"Edited" if count is not None else "Wrote"} {path} ({len(data)} bytes)' + (f'; {count} replacement(s)' if count is not None else '')}


def execute(name, args):
    if name == 'view':
        return view(args['path'], args.get('range'))
    if name == 'write':
        return write(args['path'], args['content'])
    if name == 'edit':
        return write(args['path'], args['new_text'], args['old_text'], args.get('replace_all', False))
    raise ValueError('Unknown tool')


if __name__ == '__main__':
    try:
        request = json.loads(sys.stdin.buffer.read(12 * FILE_LIMIT + 65536))
        print(json.dumps(execute(request['name'], request['args'])))
    except Exception as exc:
        print(json.dumps({'text': f'Error: {str(exc)[:1000]}', 'error': True}))
