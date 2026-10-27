"""Server-owned native tool contracts and bounded Docker execution."""
from __future__ import annotations
import base64
import json

import container_service
import docker_cli
import sandbox_runtime

TEXT_LIMIT = 32768
FILE_LIMIT = 1024 * 1024
NAMES = frozenset({'bash', 'view', 'write', 'edit'})


def schema(name, description, properties, required):
    return {'type': 'function', 'function': {'name': name, 'description': description,
        'parameters': {'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False}}}


STRING = {'type': 'string'}
SCHEMAS = [
    schema('bash', 'Run noninteractive Bash inside the container at /workspace. Files persist; shell state does not. Supports apt/pip/npm. Foreground only; default timeout 120 seconds, maximum 600. Output capped at 32 KiB; redirect to a file for full logs.',
           {'command': STRING, 'timeout': {'type': 'integer', 'minimum': 1, 'maximum': 600}}, ['command']),
    schema('view', 'View a container path, relative to /workspace. Text: numbered UTF-8 lines, max 200 lines/32 KiB. Optional inclusive 1-based range start:end selects lines, PDF pages (max 5, default 1), or notebook cells (max 200, default 20). Images returned visually. Directories list two levels, skipping dotfiles/node_modules (max 200 entries); no range for images/directories. Does not execute notebooks.',
           {'path': STRING, 'range': STRING}, ['path']),
    schema('write', 'Create or overwrite a UTF-8 file inside the container; creates parents. Relative paths use /workspace. Maximum 1 MiB of text, including empty text.',
           {'path': STRING, 'content': STRING}, ['path', 'content']),
    schema('edit', 'Replace exact text in a container UTF-8 file (max 1 MiB). Relative paths use /workspace. Requires one match unless replace_all=true. Empty old_text is invalid. Preserve original line endings in supplied text.',
           {'path': STRING, 'old_text': STRING, 'new_text': STRING, 'replace_all': {'type': 'boolean'}}, ['path', 'old_text', 'new_text']),
]


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
    with sandbox_runtime.runtime_operation(runtime_id, cancel):
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
            return {'text': text + f'\n[exit status {code}]'}
        code, raw, error = sandbox_runtime.docker_operation(runtime_id, ['/opt/venv/bin/python', '-I', '/opt/lumen/sandbox_tools.py'],
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
        return {'text': result['text'], 'images': images, 'error': bool(result.get('error'))}
