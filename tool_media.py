"""Conversation-scoped tool images, kept out of persisted model text."""
import base64
import store


def without_images(messages: list[dict]) -> list[dict]:
    """Build a text-only provider request without changing stored history."""
    output = []
    for message in messages:
        content = message.get('content')
        if isinstance(content, list) and any(part.get('type') == 'image_url' for part in content):
            parts = []
            notice_added = False
            for part in content:
                if part.get('type') == 'image_url':
                    if not notice_added:
                        parts.append('Image unavailable: this model does not support image inputs.')
                        notice_added = True
                else:
                    parts.append(part.get('text', ''))
            message = {**message, 'content': '\n'.join(parts)}
        output.append(message)
    return output


def save(conv_id, images):
    refs = []
    for image in images:
        raw = base64.b64decode(image['data'], validate=True)
        if len(raw) > 1024 * 1024:
            raise ValueError('Image too large')
        ref = store.save_image(conv_id, raw, 'image/jpeg')
        if ref is None:
            raise ValueError('Conversation not found')
        refs.append(ref)
    return refs


def expand(messages, conv_id):
    """Emit visual context only after ALL tool responses in a batch."""
    output, pending = [], []
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
            clean['content'] = [_expand_image(part, conv_id) for part in clean['content']]
        output.append(clean)
        for ref in message.get('_tool_media', [])[:5]:
            image = store.get_image(conv_id, ref)
            if image is not None and len(image['data']) <= 1024 * 1024:
                pending.append({'type': 'image_url', 'image_url': {
                    'url': 'data:' + image['mime_type'] + ';base64,' + base64.b64encode(image['data']).decode()}})
    flush()
    return output


def _expand_image(part, conv_id):
    if part.get('type') != 'image_ref':
        return part
    image = store.get_image(conv_id, part.get('ref', ''))
    if image is None:
        raise ValueError('Attached image no longer available')
    return {'type': 'image_url', 'image_url': {
        'url': 'data:' + image['mime_type'] + ';base64,' + base64.b64encode(image['data']).decode()}}
