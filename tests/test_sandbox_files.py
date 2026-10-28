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
import storage_safeguards
import container_service
import advanced_config


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


@pytest.mark.parametrize('low_space', [False, True])
def test_file_panel_recovers_over_limit_workspaces_without_allowing_uploads(tmp_lumen, monkeypatch, low_space):
    rid = 'storage-recovery'
    workspace = container_service.conversation_workspace(rid)
    monkeypatch.setattr(worker, 'ROOT', workspace)
    (workspace / 'report.txt').write_text('keep or export this result')
    advanced_config.save_advanced_config({'max_workspace_bytes': 1})
    if low_space:
        monkeypatch.setattr(storage_safeguards.shutil, 'disk_usage', lambda path: type('Usage', (), {'free': 0})())

    checks = []
    def transport(runtime_id, argv, payload, **kwargs):
        # Exercise the same nested checks that runtime creation and execution use.
        checks.append(storage_safeguards.check(runtime_id, docker=True))
        result = worker.execute(json.loads(payload))
        output = io.BytesIO()
        worker.write_result(result, output)
        return 0, output.getvalue(), b''

    monkeypatch.setattr(sandbox_runtime, 'docker_operation', transport)
    listing, status = container_files.operation(rid, 'list')
    assert status == 200 and listing['entries'][0]['name'] == 'report.txt'
    result, status = container_files.operation(rid, 'download', '/workspace/report.txt')
    assert status == 200 and result['data'] == b'keep or export this result'
    assert all(result['storage_recovery'] for result in checks)
    # A recovery request must not leave a bypass active for a later upload.
    before_upload = len(checks)
    result, status = container_files.operation(rid, 'upload', name='more.txt', data=b'more')
    assert status == 400
    assert 'storage' in result['error'].lower() or 'free space' in result['error']
    assert len(checks) == before_upload
    assert not (workspace / 'uploads').exists()


def test_recovery_context_is_request_local_and_restored_after_error(tmp_lumen):
    from concurrent.futures import ThreadPoolExecutor
    workspace = container_service.conversation_workspace('context')
    (workspace / 'large').write_bytes(b'large')
    advanced_config.save_advanced_config({'max_workspace_bytes': 1})
    with pytest.raises(RuntimeError, match='test failure'):
        with storage_safeguards.recovery_operation():
            assert storage_safeguards.check('context')['storage_recovery']
            with ThreadPoolExecutor(1) as executor:
                with pytest.raises(storage_safeguards.StorageLimit):
                    executor.submit(storage_safeguards.check, 'context').result()
            raise RuntimeError('test failure')
    with pytest.raises(storage_safeguards.StorageLimit):
        storage_safeguards.check('context')


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
    assert result['data'] == raw and result['name'] == 'data.txt'


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


@pytest.mark.parametrize('name', ['notes.txt', 'page.html', 'image.png', 'book.ipynb'])
def test_unchanged_previews_skip_reads_and_rendering(root, monkeypatch, name):
    path = root / name
    path.write_bytes(b'first')
    render = Mock(side_effect=lambda target, meta, req: {**meta, 'content': 'visual', 'previewable': True})
    monkeypatch.setattr(worker, 'visual_preview', render)
    first = worker.execute({'action': 'preview', 'path': name})
    render.reset_mock()
    with monkeypatch.context() as check:
        check.setattr(worker.os, 'open', Mock(side_effect=AssertionError('Unchanged file must not be opened')))
        cached = worker.execute({'action': 'preview', 'path': name, 'version': first['version']})
    assert cached == {'unchanged': True}
    render.assert_not_called()
    path.write_bytes(b'other')  # Same length; a size-only cache would be stale.
    updated = worker.execute({'action': 'preview', 'path': name, 'version': first['version']})
    assert updated['version'] != first['version'] and not updated.get('unchanged')


def test_binary_transport_preserves_arbitrary_bytes_without_base64(root, monkeypatch):
    raw = bytes(range(256)) * 100
    (root / 'data.bin').write_bytes(raw)
    def transport(runtime_id, argv, payload, **kwargs):
        output = io.BytesIO()
        worker.write_result(worker.execute(json.loads(payload)), output)
        assert output.getvalue().endswith(raw)
        return 0, output.getvalue(), b''
    monkeypatch.setattr(sandbox_runtime, 'docker_operation', transport)
    result, status = container_files.operation('binary-test', 'download', 'data.bin')
    assert status == 200 and result['data'] == raw


@pytest.mark.parametrize('raw', [b'{"name":"x","length":10}\nshort', b'{}'])
def test_binary_transport_rejects_truncated_payloads(monkeypatch, raw):
    monkeypatch.setattr(sandbox_runtime, 'docker_operation', lambda *args, **kwargs: (0, raw, b''))
    assert container_files.operation('binary-test', 'download', 'x')[1] == 400


def test_notebook_preview_is_bounded_and_never_executes_cells(root):
    cells = [{'cell_type': 'code', 'source': [f'raise RuntimeError({n})'],
              'outputs': [{'text': [f'saved output {n}']}]} for n in range(25)]
    (root / 'book.ipynb').write_text(json.dumps({'cells': cells}))
    first = worker.execute({'action': 'preview', 'path': 'book.ipynb'})
    assert first['previewable'] and first['preview_kind'] == 'notebook'
    assert (first['start'], first['end'], first['total']) == (1, 20, 25)
    assert 'saved output 0' in first['content']
    last = worker.execute({'action': 'preview', 'path': 'book.ipynb', 'range': '21:40'})
    assert (last['start'], last['end'], last['total']) == (21, 25, 25)
    assert 'Cell 21 (code)' in last['content']
    assert 'Cell 1 (code)' not in last['content']


@pytest.mark.parametrize('name', ['bad.pdf', 'bad.png', 'bad.ipynb'])
def test_failed_visual_preview_retains_download(root, name):
    (root / name).write_bytes(b'invalid file')
    result = worker.execute({'action': 'preview', 'path': name})
    assert not result['previewable'] and result['preview_error']
    assert worker.execute({'action': 'download', 'path': name})['data'] == b'invalid file'


def test_image_preview_rasterizes_and_bounds_dimensions(root):
    Image = pytest.importorskip('PIL.Image')
    Image.new('RGB', (2000, 1000), 'red').save(root / 'image.png')
    result = worker.execute({'action': 'preview', 'path': 'image.png'})
    assert result['previewable'] and result['preview_kind'] == 'image'
    assert result['content'] is None
    raw = base64.b64decode(result['images'][0]['data'])
    with Image.open(io.BytesIO(raw)) as image:
        assert image.format == 'JPEG' and image.size == (1600, 800)


def test_pdf_preview_pages_without_text_and_encryption(root):
    fitz = pytest.importorskip('fitz')
    with fitz.open() as doc:
        for n in range(6):
            doc.new_page().insert_text((72, 72), f'PDF page {n + 1}')
        doc.save(root / 'report.pdf')
        doc.save(root / 'locked.pdf', encryption=fitz.PDF_ENCRYPT_AES_256, user_pw='secret')
    first = worker.execute({'action': 'preview', 'path': 'report.pdf'})
    assert first['preview_kind'] == 'pdf' and first['previewable']
    assert 'images' not in first and first['content'] is None
    last = worker.execute({'action': 'preview', 'path': 'report.pdf', 'range': '2:6'})
    assert 'images' not in last and 'total' not in last
    assert last['content'] is None
    locked = worker.execute({'action': 'preview', 'path': 'locked.pdf'})
    assert locked['previewable'] and locked['preview_kind'] == 'pdf'


@pytest.mark.parametrize(('byte_range', 'start', 'end'), [
    ('bytes=0-4', 0, 4), ('bytes=5-', 5, 19), ('bytes=-5', 15, 19),
    ('bytes=10-100', 10, 19), ('bytes=-100', 0, 19),
])
def test_pdf_reads_only_requested_bytes(root, byte_range, start, end):
    raw = b'%PDF-' + b'x' * 15
    (root / 'report.pdf').write_bytes(raw)
    result = worker.execute({'action': 'pdf', 'path': 'report.pdf', 'byte_range': byte_range})
    assert result['status'] == 206 and result['total'] == len(raw)
    assert (result['start'], result['end']) == (start, end)
    assert result['data'] == raw[start:end + 1]


@pytest.mark.parametrize('byte_range', ['bytes=20-', 'bytes=-0', 'bytes=5-2', 'bytes=0-1,4-5', 'invalid'])
def test_pdf_invalid_ranges_have_no_payload(root, byte_range):
    (root / 'report.pdf').write_bytes(b'%PDF-' + b'x' * 15)
    result = worker.execute({'action': 'pdf', 'path': 'report.pdf', 'byte_range': byte_range})
    assert result['status'] == 416 and 'data' not in result


def test_pdf_version_revalidation_and_if_range(root):
    path = root / 'report.pdf'
    path.write_bytes(b'%PDF-first')
    first = worker.execute({'action': 'pdf', 'path': 'report.pdf'})
    cached = worker.execute({'action': 'pdf', 'path': 'report.pdf', 'if_none_match': first['etag']})
    assert cached['status'] == 304 and 'data' not in cached
    path.write_bytes(b'%PDF-second')
    updated = worker.execute({'action': 'pdf', 'path': 'report.pdf', 'byte_range': 'bytes=0-4',
                              'if_range': first['etag']})
    assert updated['status'] == 200 and updated['etag'] != first['etag']
    assert updated['data'] == b'%PDF-second'


def test_pdf_metadata_reads_only_header_without_loading_renderer(root, monkeypatch):
    path = root / 'report.pdf'
    with path.open('wb') as source:
        source.write(b'%PDF-')
        source.truncate(10 * 1024 * 1024)
    original = worker.os.fdopen
    reads = []
    class TrackedFile:
        def __init__(self, source):
            self.source = source
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.source.close()
        def fileno(self):
            return self.source.fileno()
        def read(self, count):
            reads.append(count)
            return self.source.read(count)
    monkeypatch.setattr(worker.os, 'fdopen', lambda *args: TrackedFile(original(*args)))
    monkeypatch.setattr(worker.importlib.util, 'spec_from_file_location', Mock(side_effect=AssertionError('No renderer needed')))
    result = worker.execute({'action': 'preview', 'path': 'report.pdf'})
    assert result['previewable'] and reads == [5]


@pytest.mark.skipif(not hasattr(os, 'mkfifo'), reason='Requires POSIX FIFO')
def test_special_files_do_not_block(root):
    os.mkfifo(root / 'pipe')
    assert worker.execute({'action': 'list'})['entries'] == []
    with pytest.raises(ValueError, match='regular'):
        worker.execute({'action': 'preview', 'path': 'pipe'})


def test_upload_collision_and_size_limit(root):
    request = {'action': 'upload', 'name': 'report.txt', 'data': b'hi'}
    assert worker.execute(request)['path'] == '/workspace/uploads/report.txt'
    assert worker.execute(request)['path'] == '/workspace/uploads/report-1.txt'
    assert (root / 'uploads/report.txt').read_bytes() == b'hi'
    with pytest.raises(ValueError, match='size limit'):
        worker.execute({**request, 'limits': {'max_upload_bytes': 1}})
    assert len(list((root / 'uploads').iterdir())) == 2


def test_upload_symlink_escape(root, tmp_path):
    (root / 'uploads').symlink_to(tmp_path)
    with pytest.raises(ValueError, match='escapes'):
        worker.execute({'action': 'upload', 'name': 'x', 'data': b'hi'})


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


def test_upload_binary_transport_preserves_bytes_without_full_file_encoding(root, monkeypatch):
    raw = bytes(range(256)) * 100
    def transport(runtime_id, argv, payload, **kwargs):
        # The raw file stays separate from the small metadata header.
        assert isinstance(payload, tuple) and payload[1] is raw
        request = worker.read_request(io.BytesIO(b''.join(payload)))
        result = worker.execute(request)
        output = io.BytesIO()
        worker.write_result(result, output)
        return 0, output.getvalue(), b''
    monkeypatch.setattr(sandbox_runtime, 'docker_operation', transport)
    result, status = container_files.operation('binary-upload', 'upload', name='data.bin', data=raw)
    assert status == 200 and result['size'] == len(raw)
    assert (root / 'uploads/data.bin').read_bytes() == raw


def test_empty_binary_upload_roundtrip(root):
    request = worker.read_request(io.BytesIO(b'{"action":"upload","name":"empty"}\n'))
    assert worker.execute(request)['size'] == 0
    assert (root / 'uploads/empty').read_bytes() == b''


def test_upload_transport_rejects_oversize_before_starting_container(monkeypatch):
    monkeypatch.setattr(container_files, 'file_limits', lambda: {
        'max_file_preview_bytes': 1, 'max_file_list_entries': 1, 'max_upload_bytes': 2})
    operation = Mock()
    monkeypatch.setattr(sandbox_runtime, 'docker_operation', operation)
    assert container_files.operation('binary-upload', 'upload', name='large', data=b'big')[1] == 400
    operation.assert_not_called()


def test_worker_request_header_is_bounded():
    with pytest.raises(ValueError):
        worker.read_request(io.BytesIO(b'{"action":"list","path":"' + b'x' * 65536 + b'"}'))


def test_docker_transport_writes_binary_header_and_body_without_host_shell(monkeypatch):
    import sys
    monkeypatch.setattr(sandbox_runtime.container_service, 'ensure_container', lambda runtime: None)
    monkeypatch.setattr(sandbox_runtime.docker_cli, 'argv', lambda args: [
        sys.executable, '-c', 'import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())'])
    raw = bytes(range(256)) * 100
    code, output, errors = sandbox_runtime.docker_operation('transport-test', [], (b'header\n', raw))
    assert code == 0 and not errors and output == b'header\n' + raw
