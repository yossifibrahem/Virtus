"""Opt-in real Docker integration: LUMEN_RUN_DOCKER_TESTS=1 pytest tests/test_builtin_docker.py."""
import base64
import io
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

pytestmark = pytest.mark.skipif(os.getenv('LUMEN_RUN_DOCKER_TESTS') != '1', reason='Opt-in Docker integration')


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    image = os.getenv('LUMEN_TEST_SANDBOX_IMAGE', 'lumen-sandbox:builtins-test')
    memory = tmp_path / 'memory.md'; memory.write_text('Memory\n')
    monkeypatch.setattr(containers, 'CONTAINERS_ROOT', tmp_path / 'workspaces')
    monkeypatch.setattr(containers, 'CONTAINER_PREFIX', 'lumen-integration-')
    monkeypatch.setattr(containers, '_memory_volume_spec', lambda: f'{memory}:/memory.md:rw')
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
    assert 'beta' in native.invoke(rid, 'read', {'path': 'nested/a.txt', 'range': '2:2'})['text']
    native.invoke(rid, 'edit', {'path': 'nested/a.txt', 'old_text': 'beta', 'new_text': 'gamma'})
    native.invoke(rid, 'edit', {'path': '/memory.md', 'old_text': 'Memory', 'new_text': 'Updated'})
    assert (tmp_path / 'memory.md').read_text() == 'Updated\n'
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
    assert native.invoke(rid, 'read', {'path': 'picture.png'})['images']
    pdf = native.invoke(rid, 'read', {'path': 'document.pdf'})
    assert 'Hello PDF' in pdf['text'] and pdf['images']
    assert 'print(1)' in native.invoke(rid, 'read', {'path': 'book.ipynb'})['text']
    result, status = container_files.operation(rid, 'download', '/workspace/nested/a.txt')
    assert status == 200 and base64.b64decode(result['data']) == b'alpha\ngamma\n'
    result, status = container_files.operation(rid, 'upload', name='test.txt', data=base64.b64encode(b'upload').decode())
    assert status == 200
    assert 'upload' in native.invoke(rid, 'read', {'path': result['path']})['text']
    native.invoke(rid, 'bash', {'command': 'ln -s /etc /workspace/escape; rm -rf /workspace/uploads; ln -s /etc /workspace/uploads'})
    assert container_files.operation(rid, 'preview', '/workspace/escape/passwd')[1] == 400
    assert container_files.operation(rid, 'upload', name='blocked', data='eA==')[1] == 400
    inspect = docker_cli.run(['inspect', containers.container_name(rid)])
    info = json.loads(inspect.stdout)[0]
    assert {m['Destination'] for m in info['Mounts']} == {'/workspace', '/memory.md'}
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
        result = native.invoke(runtime, 'read', {'path': filename})
        assert result['error'] and 'Error:' in result['text']
    native.invoke(runtime, 'bash', {'command': 'truncate -s 22000000 oversized.png; mkfifo pipe'})
    assert native.invoke(runtime, 'read', {'path': 'oversized.png'})['error']
    assert native.invoke(runtime, 'read', {'path': 'pipe'})['error']
    assert native.invoke(runtime, 'read', {'path': '/workspace', 'range': '1:2'})['error']


def test_folder_host_mounts_readonly_write_through_and_isolation(runtime, tmp_path, monkeypatch):
    import folder_mounts
    import store
    monkeypatch.setattr(store, 'FOLDERS_FILE', tmp_path / 'folders.json')
    source = tmp_path / 'host project, quoted'; source.mkdir()
    (source / 'original.txt').write_text('host data')
    mounts = folder_mounts.normalize_mounts([{'source': str(source)}])
    folder = store.create_folder('Mounted project', mounts=mounts)
    rid = f"folder_{folder['id']}"
    target = f"/workspace/mounts/{mounts[0]['name']}"
    try:
        containers.ensure_container(rid)
        assert 'host data' in native.invoke(rid, 'read', {'path': target + '/original.txt'})['text']
        result = native.invoke(rid, 'bash', {'command': f"echo denied > '{target}/denied.txt'"})['text']
        assert 'Read-only file system' in result
        assert not (source / 'denied.txt').exists()
        listing, status = container_files.operation(rid, 'list', target)
        assert status == 200 and 'original.txt' in json.dumps(listing), listing
        info = json.loads(docker_cli.run(['inspect', containers.container_name(runtime)]).stdout)[0]
        assert not any(m['Source'] == str(source) for m in info['Mounts'])
        mounts[0]['read_only'] = False
        store.update_folder(folder['id'], mounts=mounts)
        # ensure_container must notice the changed RO permission and recreate.
        containers.ensure_container(rid)
        result = native.invoke(rid, 'bash', {'command': f"echo written > '{target}/created.txt'"})['text']
        assert 'exit status 0' in result
        assert (source / 'created.txt').read_text() == 'written\n'
        store.update_folder(folder['id'], mounts=[])
        containers.ensure_container(rid)
        result = native.invoke(rid, 'bash', {'command': f"test ! -e '{target}/original.txt'"})['text']
        assert 'exit status 0' in result
    finally:
        containers.stop_container(rid)
        containers.delete_workspace(rid)
    assert (source / 'original.txt').read_text() == 'host data'
    assert (source / 'created.txt').read_text() == 'written\n'
