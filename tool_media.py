"""Conversation-scoped tool images, kept out of persisted model text."""
import base64
import hashlib
import re
import store


def directory(conv_id):
    if not isinstance(conv_id, str) or not re.fullmatch(r'[a-fA-F0-9-]{36}', conv_id) or not store.load(conv_id):
        raise ValueError('Conversation not found')
    return store.IMAGES_DIR / 'tools' / conv_id


def save(conv_id, images):
    refs = []
    root = None
    for image in images:
        raw = base64.b64decode(image['data'], validate=True)
        if len(raw) > 1024 * 1024:
            raise ValueError('Image too large')
        ref = hashlib.sha256(raw).hexdigest() + '.jpeg'
        if root is None:
            root = directory(conv_id)
            root.mkdir(parents=True, exist_ok=True)
        (root / ref).write_bytes(raw)
        refs.append(ref)
    return refs


def path(conv_id, ref):
    return _media_path(directory(conv_id), ref)


def _media_path(root, ref):
    if not isinstance(ref, str) or not re.fullmatch(r'[a-f0-9]{64}\.jpeg', ref):
        raise ValueError('Invalid media reference')
    target = root / ref
    if not target.is_file():
        raise ValueError('Tool image no longer available')
    return target


def expand(messages, conv_id):
    """Emit visual context only after ALL tool responses in a batch."""
    output, pending = [], []
    root = None
    def flush():
        if pending:
            output.append({'role': 'user', 'content': [
                {'type': 'text', 'text': 'Visual results from the preceding View tool calls (not a new user request).'}, *pending]})
            pending.clear()
    for message in messages:
        if message.get('role') != 'tool':
            flush()
        clean = {k: v for k, v in message.items() if k != '_tool_media'}
        if isinstance(clean.get('content'), list):
            clean['content'] = [_expand_image(part) for part in clean['content']]
        output.append(clean)
        for ref in message.get('_tool_media', [])[:5]:
            try:
                if root is None:
                    root = directory(conv_id)
                with _media_path(root, ref).open('rb') as source:
                    raw = source.read(1024 * 1024 + 1)
                if len(raw) <= 1024 * 1024:
                    pending.append({'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + base64.b64encode(raw).decode()}})
            except (ValueError, OSError):
                pass
    flush()
    return output


def _expand_image(part):
    if part.get('type') != 'image_ref':
        return part
    target = store.get_image_path(part.get('ref', ''))
    if target is None:
        raise ValueError('Attached image no longer available')
    mime = 'image/' + target.suffix[1:]
    return {'type': 'image_url', 'image_url': {
        'url': f'data:{mime};base64,' + base64.b64encode(target.read_bytes()).decode()}}
