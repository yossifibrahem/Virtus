"""Exercise the actual container file worker and bounded host transport."""
import base64
import io
import json
import os
from unittest.mock import Mock

import pytest
from werkzeug.datastructures import FileStorage

import container_files
import sandbox_files as worker
import sandbox_runtime
import store


@pytest.fixture
def root(tmp_path, monkeypatch):
    path = tmp_path / 'workspace'
    path.mkdir()
    monkeypatch.setattr(worker, 'ROOT', path)
    return path


@pytest.mark.parametrize('value', ['', '.', '/', '/workspace', None])
def test_root_paths(root, value):
    assert worker.resolve(value) == root


@pytest.mark.parametrize('value', ['../escape', 'a/../../x', '/etc/passwd', '/workspace/../x'])
def test_invalid_paths(root, value):
    with pytest.raises(ValueError):
        worker.resolve(value)


def test_normalized_paths(root):
    assert worker.resolve('/workspace/a/b') == root / 'a/b'
    assert worker.resolve('a\\b') == root / 'a/b'


def test_symlinks_never_leave_workspace(root, tmp_path):
    (root / 'escape').symlink_to(tmp_path)
    (root / 'broken').symlink_to(root / 'missing')
    with pytest.raises(ValueError, match='escapes'):
        worker.resolve('escape/secret')
    assert worker.execute({'action': 'list'})['entries'] == []


def test_listing_contract_and_order(root):
    (root / 'z.txt').write_text('z')
    (root / 'dir').mkdir()
    listing = worker.execute({'action': 'list'})
    assert listing['path'] == '/workspace' and listing['parent'] is None
    assert [e['name'] for e in listing['entries']] == ['dir', 'z.txt']
    assert not listing['truncated']
    nested = worker.execute({'action': 'list', 'path': 'dir'})
    assert nested['path'] == '/workspace/dir' and nested['parent'] == '/workspace'


def test_configured_listing_limit_and_exact_boundary(root):
    for name in ['a', 'b', 'c']:
        (root / name).touch()
    result = worker.execute({'action': 'list', 'limits': {'max_file_list_entries': 2}})
    assert result['limit'] == 2 and len(result['entries']) == 2 and result['truncated']
    result = worker.execute({'action': 'list', 'limits': {'max_file_list_entries': 3}})
    assert not result['truncated']


@pytest.mark.parametrize('raw', [b'hello', b'', 'مرحبا'.encode()])
def test_preview_and_download(root, raw):
    (root / 'data.txt').write_bytes(raw)
    result = worker.execute({'action': 'preview', 'path': 'data.txt'})
    assert result['previewable'] and result['content'] == raw.decode()
    result = worker.execute({'action': 'download', 'path': 'data.txt'})
    assert base64.b64decode(result['data']) == raw and result['name'] == 'data.txt'


@pytest.mark.parametrize('raw', [b'\xff', b'a\0b', b'x'*11])
def test_non_previewable_files(root, raw):
    (root / 'data').write_bytes(raw)
    result = worker.execute({'action': 'preview', 'path': 'data', 'limits': {'max_file_preview_bytes': 10}})
    assert result['content'] is None and not result['previewable']


def test_large_preview_setting_is_honored(root):
    (root / 'large.txt').write_bytes(b'x' * 600000)
    result = worker.execute({'action': 'preview', 'path': 'large.txt',
                             'limits': {'max_file_preview_bytes': 1024*1024}})
    assert len(result['content']) == 600000


@pytest.mark.skipif(not hasattr(os, 'mkfifo'), reason='Requires POSIX FIFO')
def test_special_files_do_not_block(root):
    os.mkfifo(root / 'pipe')
    assert worker.execute({'action': 'list'})['entries'] == []
    with pytest.raises(ValueError, match='regular'):
        worker.execute({'action': 'preview', 'path': 'pipe'})


def test_upload_collision_and_size_limit(root):
    request = {'action': 'upload', 'name': 'report.txt', 'data': 'aGk='}
    assert worker.execute(request)['path'] == '/workspace/uploads/report.txt'
    assert worker.execute(request)['path'] == '/workspace/uploads/report-1.txt'
    assert (root / 'uploads/report.txt').read_bytes() == b'hi'
    with pytest.raises(ValueError, match='size limit'):
        worker.execute({**request, 'limits': {'max_upload_bytes': 1}})
    assert len(list((root / 'uploads').iterdir())) == 2


def test_upload_symlink_escape(root, tmp_path):
    (root / 'uploads').symlink_to(tmp_path)
    with pytest.raises(ValueError, match='escapes'):
        worker.execute({'action': 'upload', 'name': 'x', 'data': 'aGk='})


@pytest.mark.parametrize(('name', 'expected'), [
    ('report.pdf', 'report.pdf'), ('a/b/c.txt', 'c.txt'),
    ('C:\\private\\report.txt', 'report.txt'), ('...hidden', 'hidden'),
    ('', 'file'), ('evil;rm.sh', 'evil_rm.sh'),
])
def test_safe_upload_name(name, expected):
    assert container_files.safe_upload_name(name) == expected


def test_transport_sends_limits_and_allows_json_escaping(monkeypatch):
    call = Mock(return_value=(0, b'{"content":"ok"}', b''))
    monkeypatch.setattr(sandbox_runtime, 'docker_operation', call)
    monkeypatch.setattr(container_files, 'file_limits', lambda: {
        'max_file_preview_bytes': 1024*1024, 'max_file_list_entries': 12, 'max_upload_bytes': 10})
    result, status = container_files.operation('chat', 'preview', 'data.txt')
    assert status == 200 and result == {'content': 'ok'}
    request = json.loads(call.call_args.args[2])
    assert request['limits']['max_file_preview_bytes'] == 1024*1024
    assert call.call_args.kwargs['limit'] > 6*1024*1024


@pytest.mark.parametrize('raw', [b'[]', b'not JSON'])
def test_invalid_transport_response(monkeypatch, raw):
    monkeypatch.setattr(sandbox_runtime, 'docker_operation', lambda *a, **k: (0, raw, b''))
    assert container_files.operation('chat', 'list')[1] == 400


def test_upload_host_transport_checks_size_before_docker(tmp_lumen, monkeypatch):
    conv = store.create()
    monkeypatch.setattr(container_files, 'file_limits', lambda: {'max_upload_bytes': 2})
    call = Mock()
    monkeypatch.setattr(container_files, 'operation', call)
    item = FileStorage(stream=io.BytesIO(b'big'), filename='file')
    assert container_files.uploads(conv['id'], [item])[1] == 413
    call.assert_not_called()
