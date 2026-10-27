"""App-owned, bounded global memory with optimistic revisions and atomic writes.

Memory is stored as a JSON object in ~/.lumen/memory.json.
No container receives this path.
The local application is single-process; one lock serializes all chats and UI edits.
"""
from __future__ import annotations
import shutil
import hashlib
import json
import threading
import uuid
from pathlib import Path
from fs_utils import atomic_replace

MEMORY_FILE = Path.home() / '.lumen' / 'memory.json'
LIMIT = 32768
# JSON escapes can use six bytes per content byte, plus object/formatting overhead.
FILE_LIMIT = LIMIT * 6 + 1024
_LOCK = threading.RLock()


class Conflict(ValueError):
    pass


def _atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    try:
        tmp.write_text(text, encoding='utf-8')
        atomic_replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def enabled():
    with _LOCK:
        try:
            return json.loads(MEMORY_FILE.with_suffix('.settings.json').read_text())['enabled'] is not False
        except (OSError, ValueError, KeyError, TypeError):
            return True


def set_enabled(value):
    if type(value) is not bool:
        raise ValueError('enabled must be boolean')
    with _LOCK:
        _atomic(MEMORY_FILE.with_suffix('.settings.json'), json.dumps({'enabled': value}))


def _raw():
    try:
        with MEMORY_FILE.open('rb') as source:
            raw = source.read(FILE_LIMIT + 1)
    except FileNotFoundError:
        return b''
    if len(raw) > FILE_LIMIT:
        raise ValueError('Memory JSON file is too large')
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ValueError('Memory file must contain valid JSON') from exc
    if not isinstance(data, dict) or not isinstance(data.get('content'), str):
        raise ValueError('Memory JSON must be an object with a string content field')
    return data['content'].encode('utf-8')


def snapshot():
    with _LOCK:
        raw = _raw()
        return {'content': raw[:LIMIT].decode('utf-8', 'replace'),
                'revision': hashlib.sha256(raw).hexdigest(),
                'oversized': len(raw) > LIMIT, 'enabled': enabled()}


def read():
    if not enabled():
        return ''
    state = snapshot()
    return state['content'].strip() + ('\n[Memory truncated at 32 KiB]' if state['oversized'] else '')


def update(content, revision, *, require_enabled=False):
    if not isinstance(content, str) or len(content.encode('utf-8')) > LIMIT:
        raise ValueError('Memory must be UTF-8 text of at most 32 KiB')
    if not isinstance(revision, str) or len(revision) != 64:
        raise ValueError('A valid memory revision is required')
    with _LOCK:
        if require_enabled and not enabled():
            raise ValueError('Global memory is disabled')
        state = snapshot()
        if revision != state['revision']:
            raise Conflict('Memory changed; reload the current content and revision before editing')
        if state['oversized'] and content == '':
            shutil.copyfile(MEMORY_FILE, MEMORY_FILE.with_suffix('.oversized.json'))
        elif state['oversized']:
            raise ValueError('Memory exceeds 32 KiB; preserve/reduce the original file before editing in the app')
        history = MEMORY_FILE.with_suffix('.history.json')
        try:
            with history.open('rb') as source:
                history_bytes = source.read(LIMIT * 16 + 1)
            versions = json.loads(history_bytes) if len(history_bytes) <= LIMIT * 16 else []
            if not isinstance(versions, list):
                versions = []
        except (OSError, ValueError):
            versions = []
        versions = [v for v in versions[-7:] if isinstance(v, dict) and len(str(v)) <= LIMIT * 2]
        versions.append({'revision': state['revision'], 'content': state['content']})
        _atomic(history, json.dumps(versions, ensure_ascii=False))
        _atomic(MEMORY_FILE, json.dumps({'content': content}, ensure_ascii=False, indent=2))
        return snapshot()


def ensure_file():
    with _LOCK:
        if not MEMORY_FILE.exists():
            _atomic(MEMORY_FILE, json.dumps({'content': ''}, indent=2))
    return MEMORY_FILE
