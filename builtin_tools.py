"""Server-owned native tool contracts and bounded Docker execution."""
from __future__ import annotations
import base64
import json

import container_service
import memory_service
import docker_cli
import sandbox_runtime

TEXT_LIMIT = 32768
FILE_LIMIT = 1024 * 1024
NAMES = frozenset({'bash', 'view', 'write', 'edit', 'update_memory'})


def schema(name, description, properties, required):
    return {
        'type': 'function',
        'function': {
            'name': name,
            'description': description,
            'parameters': {
                'type': 'object',
                'properties': properties,
                'required': required,
                'additionalProperties': False,
            },
        },
    }


# Chat/UI guidance belongs in app_policy.js; event-specific recovery guidance
# belongs in runtime results, rather than being repeated in these schemas.
CONTAINER_PATH = {
    'type': 'string',
    'description': 'Literal container path, absolute or relative to /workspace. No shell expansion of ~, variables, or wildcards.',
    'minLength': 1,
    'maxLength': 4096,
}

SCHEMAS = [
    schema(
        'bash',
        'Run commands or scripts in a noninteractive Linux Bash shell, starting at /workspace. '
        'Use for searches, builds, package installation (apt/pip/npm), and operations unsupported by view/write/edit. '
        'Working directory and shell variables do not carry between calls. Background processes are terminated when the command ends. '
        'Returns combined stdout/stderr and exit status; output is capped at 32 KiB, retaining the beginning and end. '
        'Timeout or cancellation may stop the runtime.',
        {
            'command': {
                'type': 'string',
                'description': 'Bash command or multiline script to execute, at most 1 MiB encoded as UTF-8.',
                'minLength': 1,
                'maxLength': FILE_LIMIT,
            },
            'timeout': {
                'type': 'integer',
                'description': 'Maximum execution time in seconds.',
                'minimum': 1,
                'maximum': 600,
                'default': 120,
            },
        },
        ['command'],
    ),
    schema(
        'view',
        'Inspect a file or directory. UTF-8 text includes line numbers; text output is capped at 32 KiB. '
        'Directories list two levels and at most 200 entries, skipping dotfiles and node_modules; use bash to inspect omitted entries. '
        'Images return resized visual previews; PDFs return extracted text and page images; notebooks return cell source and saved outputs without execution. '
        'Image, PDF, and notebook inputs are limited to 20 MiB.',
        {
            'path': dict(CONTAINER_PATH),
            'range': {
                'type': 'string',
                'description': 'Inclusive, 1-based start:end, e.g. 20:40. Text: up to 200 lines, default 1:200. PDF: up to 5 pages, default 1:1. '
                               'Notebook: up to 200 cells, default 1:20. End must be at least start and at most 10000000. Omit for images and directories.',
                'pattern': '^[1-9][0-9]*:[1-9][0-9]*$',
            },
        },
        ['path'],
    ),
    schema(
        'write',
        'Create or fully overwrite a UTF-8 text file, creating missing parent directories. '
        'Use edit for targeted replacements and bash for binary files. '
        'Returns the resolved path and number of bytes written.',
        {
            'path': dict(CONTAINER_PATH),
            'content': {
                'type': 'string',
                'description': 'Text to write, at most 1 MiB encoded as UTF-8. An empty string creates or truncates the file to zero bytes.',
                'maxLength': FILE_LIMIT,
            },
        },
        ['path', 'content'],
    ),
    schema(
        'edit',
        'Replace text in an existing UTF-8 file. '
        'Both the original and resulting file must fit within 1 MiB. '
        'Returns the resolved path, resulting byte count, and replacement count.',
        {
            'path': dict(CONTAINER_PATH),
            'old_text': {
                'type': 'string',
                'description': 'Exact text to find, including whitespace and line endings. No regex or View line-number prefixes.',
                'minLength': 1,
                'maxLength': FILE_LIMIT,
            },
            'new_text': {
                'type': 'string',
                'description': 'Replacement text. An empty string deletes the matched text.',
                'maxLength': FILE_LIMIT,
            },
            'replace_all': {
                'type': 'boolean',
                'description': 'Replace every non-overlapping occurrence of old_text; otherwise exactly one match is required. No matches is always an error.',
                'default': False,
            },
        },
        ['path', 'old_text', 'new_text'],
    ),
]

MEMORY_SCHEMA = schema(
    'update_memory',
    'Replace the global memory shared across chats. Keep concise, stable user facts and preferences; correct outdated entries. '
    'Do not store histories, secrets, transient tasks, or command state. Preserve still-valid entries.',
    {
        'content': {
            'type': 'string',
            'description': 'UTF-8 text, at most 32 KiB. An empty string clears all global memory.',
            'maxLength': memory_service.LIMIT,
        },
        'revision': {
            'type': 'string',
            'description': 'Current memory revision, copied unchanged from the injected memory context or a conflict response.',
            'minLength': 64,
            'maxLength': 64,
            'pattern': '^[0-9a-f]{64}$',
        },
    },
    ['content', 'revision'],
)
SCHEMAS.append(MEMORY_SCHEMA)


def available_schemas():
    return [s for s in SCHEMAS if s is not MEMORY_SCHEMA or memory_service.enabled()]


def validate(name, args):
    if name not in NAMES or not isinstance(args, dict):
        raise ValueError('Unknown tool or arguments must be an object')
    spec = next(s['function']['parameters'] for s in SCHEMAS if s['function']['name'] == name)
    if set(args) - set(spec['properties']) or set(spec['required']) - set(args):
        raise ValueError('Missing required arguments or unknown arguments')
    for key, value in args.items():
        kind = spec['properties'][key]['type']
        if kind == 'string' and (not isinstance(value, str) or len(value.encode('utf-8')) > FILE_LIMIT):
            raise ValueError(f'{key} must be text of at most 1 MiB')
        if kind == 'boolean' and type(value) is not bool:
            raise ValueError(f'{key} must be boolean')
        if kind == 'integer' and (type(value) is not int or not 1 <= value <= 600):
            raise ValueError('timeout must be an integer from 1 to 600 seconds')
    for key in ('path', 'command', 'old_text'):
        if key in args and (not args[key] or (key != 'old_text' and not args[key].strip())):
            raise ValueError(f'{key} must not be empty')
    if 'path' in args and ('\0' in args['path'] or len(args['path']) > 4096):
        raise ValueError('Invalid path')


# A separate process group lets the supervisor remove ordinary background
# descendants after Bash exits. Timeout/cancel additionally kills the container.
BASH_RUNNER = '''import ctypes,os,signal,subprocess,sys,time
# Adopt orphaned grandchildren, including those that call setsid().
ctypes.CDLL(None).prctl(36,1,0,0,0)
p=subprocess.Popen(["/bin/bash","--noprofile","--norc","-c",sys.stdin.read()],start_new_session=True,stderr=subprocess.STDOUT)
code=p.wait()
for attempt in range(100):
    try:
        children=open(f"/proc/self/task/{os.getpid()}/children").read().split()
    except OSError:
        children=[]
    if not children: break
    for pid in children:
        try: os.kill(int(pid),signal.SIGKILL)
        except ProcessLookupError: pass
    while True:
        try:
            if os.waitpid(-1,os.WNOHANG)[0]==0: break
        except ChildProcessError: break
    time.sleep(.01)
else:
    # The host detects the failed cleanup and stops the runtime.
    sys.exit(125)
sys.exit(code)
'''



def invoke(runtime_id, name, args, cancel=None):
    validate(name, args)
    if cancel and cancel.is_set():
        raise ValueError('Operation cancelled')
    if name == 'update_memory':
        try:
            state = memory_service.update(args['content'], args['revision'], require_enabled=True)
            return {'text': json.dumps({'revision': state['revision'], 'saved': True}, ensure_ascii=False)}
        except memory_service.Conflict:
            # Data is returned only while enabled, including stale tool calls.
            if not memory_service.enabled():
                raise ValueError('Global memory is disabled')
            return {'text': json.dumps({'error': 'Memory changed; merge current content before retrying', **memory_service.snapshot()}, ensure_ascii=False)}
    with sandbox_runtime.runtime_operation(runtime_id, cancel):
        info = container_service.ensure_container(runtime_id)
        notice = info.reset_reason + '\n' if info.reset_reason else ''
        if name == 'bash':
            code, raw, error = sandbox_runtime.docker_operation(runtime_id, ['/opt/venv/bin/python', '-I', '-c', BASH_RUNNER],
                args['command'].encode(), args.get('timeout', 120), cancel, TEXT_LIMIT - 512, True)
            if code == 125 or code >= 128:
                docker_cli.run(['kill', container_service.container_name(runtime_id)], timeout=10)
                container_service._invalidate_mcp_pool(runtime_id)
                raise ValueError(f'Command terminated abnormally (exit {code}); shared runtime stopped for cleanup')
            text = raw.decode('utf-8', 'replace') + error[:400].decode('utf-8', 'replace')
            # Invalid UTF-8 expands on decoding; retain both ends if necessary.
            encoded = text.encode()
            if len(encoded) > TEXT_LIMIT - 80:
                text = encoded[:16000].decode('utf-8', 'ignore') + '\n[output truncated]\n' + encoded[-16000:].decode('utf-8', 'ignore')
            return {'text': notice + text + f'\n[exit status {code}]'}
        code, raw, error = sandbox_runtime.docker_operation(runtime_id, ['/opt/venv/bin/python', '-I', '/opt/virtus/sandbox_tools.py'],
            json.dumps({'name': name, 'args': args}, ensure_ascii=False).encode(), 30, cancel)
        if code:
            raise ValueError('Container worker failed: ' + error.decode('utf-8', 'replace'))
        result = json.loads(raw)
        if not isinstance(result, dict) or not isinstance(result.get('text'), str):
            raise ValueError('Invalid worker response')
        result['text'] = result['text'].encode()[:TEXT_LIMIT].decode('utf-8', 'ignore')
        images = result.get('images', [])
        if not isinstance(images, list) or len(images) > 5:
            raise ValueError('Invalid worker media')
        for image in images:
            if not isinstance(image, dict) or image.get('mime') != 'image/jpeg' or not isinstance(image.get('data'), str):
                raise ValueError('Invalid worker image')
            raw_image = base64.b64decode(image['data'], validate=True)
            if len(raw_image) > FILE_LIMIT or not raw_image.startswith(b'\xff\xd8\xff'):
                raise ValueError('Invalid or oversized image')
        return {'text': notice + result['text'], 'images': images, 'error': bool(result.get('error'))}
