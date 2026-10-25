"""Conversation-scoped tool images, kept out of persisted model text."""
import base64
import hashlib
import re
from pathlib import Path
import store


def directory(conv_id):
    if not isinstance(conv_id, str) or not re.fullmatch(r'[a-fA-F0-9-]{36}', conv_id) or not store.load(conv_id):
        raise ValueError('Conversation not found')
    return store.IMAGES_DIR / 'tools' / conv_id


def save(conv_id, images):
    refs = []
    for image in images:
        raw = base64.b64decode(image['data'], validate=True)
        if len(raw) > 1024 * 1024:
            raise ValueError('Image too large')
        ref = hashlib.sha256(raw).hexdigest() + '.jpeg'
        root = directory(conv_id)
        root.mkdir(parents=True, exist_ok=True)
        (root / ref).write_bytes(raw)
        refs.append(ref)
    return refs


def path(conv_id, ref):
    if not isinstance(ref, str) or not re.fullmatch(r'[a-f0-9]{64}\.jpeg', ref):
        raise ValueError('Invalid media reference')
    target = directory(conv_id) / ref
    if not target.is_file():
        raise ValueError('Tool image no longer available')
    return target


def expand(messages, conv_id):
    """Emit visual context only after ALL tool responses in a batch."""
    output, pending = [], []
    def flush():
        if pending:
            output.append({'role': 'user', 'content': [
                {'type': 'text', 'text': 'Visual results from the preceding Read tool calls (not a new user request).'}, *pending]})
            pending.clear()
    for message in messages:
        if message.get('role') != 'tool':
            flush()
        clean = {k: v for k, v in message.items() if k != '_tool_media'}
        output.append(clean)
        for ref in message.get('_tool_media', [])[:5]:
            try:
                raw = path(conv_id, ref).read_bytes()
                if len(raw) <= 1024 * 1024:
                    pending.append({'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + base64.b64encode(raw).decode()}})
            except (ValueError, OSError):
                pass
    flush()
    return output
