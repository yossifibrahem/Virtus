"""Opt-in real Docker integration: VIRTUS_RUN_DOCKER_TESTS=1 pytest tests/test_builtin_docker.py."""
import json
import os
import threading
import time
import uuid
import pytest
import builtin_tools as native
import container_files
import container_service as containers
import docker_cli
import advanced_config
import build_info

pytestmark = pytest.mark.skipif(os.getenv('VIRTUS_RUN_DOCKER_TESTS') != '1', reason='Opt-in Docker integration')


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    image = os.getenv('VIRTUS_TEST_SANDBOX_IMAGE', 'virtus-sandbox:builtins-test')
    monkeypatch.setattr(containers, 'CONTAINERS_ROOT', tmp_path / 'workspaces')
    monkeypatch.setattr(containers, 'CONTAINER_PREFIX', 'virtus-integration-')
    monkeypatch.setattr(advanced_config, 'load_advanced_config', lambda: {
        'sandbox_image': image, 'container_memory': '512m', 'container_cpus': '1',
        'container_network': 'bridge', 'container_idle_timeout': 600,
        'max_file_preview_bytes': 512*1024, 'max_file_list_entries': 500,
        'max_upload_bytes': 50*1024*1024})
    rid = uuid.uuid4().hex
    try:
        containers.ensure_container(rid)
        yield rid
    finally:
        containers.stop_container(rid)


def test_native_tools_media_files_and_isolation(runtime, tmp_path):
    rid = runtime
    assert 'Wrote' in native.invoke(rid, 'write', {'path': 'nested/a.txt', 'content': 'alpha\nbeta\n'})['text']
    assert 'beta' in native.invoke(rid, 'view', {'path': 'nested/a.txt', 'range': '2:2'})['text']
    native.invoke(rid, 'edit', {'path': 'nested/a.txt', 'old_text': 'beta', 'new_text': 'gamma'})
    assert 'gamma' in native.invoke(rid, 'bash', {'command': 'cat nested/a.txt'})['text']
    script = """python - <<'INNER'
from PIL import Image
import fitz,json
Image.new('RGB',(20,20),'red').save('/workspace/picture.png')
doc=fitz.open(); page=doc.new_page(); page.insert_text((72,72),'Hello PDF'); doc.save('/workspace/document.pdf')
open('/workspace/book.ipynb','w').write(json.dumps({'cells':[{'cell_type':'code','source':['print(1)'],'outputs':[{'text':['1']}]}]}))
INNER
"""
    assert 'exit status 0' in native.invoke(rid, 'bash', {'command': script})['text']
    assert native.invoke(rid, 'view', {'path': 'picture.png'})['images']
    pdf = native.invoke(rid, 'view', {'path': 'document.pdf'})
    assert 'Hello PDF' in pdf['text'] and pdf['images']
    assert 'print(1)' in native.invoke(rid, 'view', {'path': 'book.ipynb'})['text']
    result, status = container_files.operation(rid, 'download', '/workspace/nested/a.txt')
    assert status == 200 and result['data'] == b'alpha\ngamma\n'
    result, status = container_files.operation(rid, 'upload', name='test.txt', data=b'upload')
    assert status == 200
    assert 'upload' in native.invoke(rid, 'view', {'path': result['path']})['text']
    native.invoke(rid, 'bash', {'command': 'ln -s /etc /workspace/escape; rm -rf /workspace/uploads; ln -s /etc /workspace/uploads'})
    assert container_files.operation(rid, 'preview', '/workspace/escape/passwd')[1] == 400
    assert container_files.operation(rid, 'upload', name='blocked', data=b'x')[1] == 400
    inspect = docker_cli.run(['inspect', containers.container_name(rid)])
    info = json.loads(inspect.stdout)[0]
    assert {m['Destination'] for m in info['Mounts']} == {'/workspace'}
    assert info['HostConfig']['PidsLimit'] == 256
    assert not info['HostConfig']['Privileged']
    assert 'no-new-privileges' in info['HostConfig']['SecurityOpt']
    assert not native.invoke(rid, 'bash', {'command': 'test -e /var/run/docker.sock'})['text'].endswith('[exit status 0]')


def test_bash_timeout_cancel_flood_and_background_cleanup(runtime):
    rid = runtime
    result = native.invoke(rid, 'bash', {'command': "python -c \"print('HEAD'+'x'*1000000+'TAIL')\""})['text']
    assert 'HEAD' in result and 'TAIL' in result and 'truncated' in result
    assert len(result.encode()) < 33000
    native.invoke(rid, 'bash', {'command': 'setsid sleep 150 >/dev/null 2>&1 & echo started'})
    check = native.invoke(rid, 'bash', {'command': "pgrep -f '^sleep 150$'"})['text']
    assert 'exit status 1' in check
    with pytest.raises(ValueError, match='timed out'):
        native.invoke(rid, 'bash', {'command': 'sleep 150 & wait', 'timeout': 1})
    assert containers.get_status(rid) == 'stopped'
    cancel = threading.Event()
    timer = threading.Timer(1.5, cancel.set); timer.start()
    try:
        with pytest.raises(ValueError, match='cancelled'):
            native.invoke(rid, 'bash', {'command': 'sleep 150 & wait'}, cancel)
    finally:
        timer.cancel()
    assert containers.get_status(rid) == 'stopped'


def test_package_installs_and_pid_limit(runtime):
    result = native.invoke(runtime, 'bash', {'command': 'apt-get update -qq && apt-get install -y --no-install-recommends tree && pip install --disable-pip-version-check six && npm install --prefix /tmp/package-check is-number', 'timeout': 300})
    assert 'exit status 0' in result['text'], result['text']
    result = native.invoke(runtime, 'bash', {'command': "python - <<'INNER'\nimport subprocess\nchildren=[]\ntry:\n for i in range(300): children.append(subprocess.Popen(['sleep','10']))\nexcept BlockingIOError: print('pid-limit-enforced')\nfinally:\n for child in children: child.kill()\n for child in children: child.wait()\nINNER"})
    assert 'pid-limit-enforced' in result['text']


def test_malformed_and_oversized_document_inputs(runtime):
    for filename in ('bad.pdf', 'bad.png', 'bad.ipynb'):
        native.invoke(runtime, 'write', {'path': filename, 'content': 'not a valid document'})
        result = native.invoke(runtime, 'view', {'path': filename})
        assert result['error'] and 'Error:' in result['text']
    native.invoke(runtime, 'bash', {'command': 'truncate -s 22000000 oversized.png; mkfifo pipe'})
    assert native.invoke(runtime, 'view', {'path': 'oversized.png'})['error']
    assert native.invoke(runtime, 'view', {'path': 'pipe'})['error']
    assert native.invoke(runtime, 'view', {'path': '/workspace', 'range': '1:2'})['error']
