"""Terminal routing, ownership, resource bounds, and runtime activity."""
import codecs
import threading
from unittest.mock import Mock

import pytest

import sandbox_runtime
import store
import terminal_service as terminals


@pytest.fixture
def terminal_client(client, monkeypatch):
    monkeypatch.setattr(terminals, '_sessions', {})
    with client.session_transaction() as session:
        session['client_id'] = 'browser-one'
    return client


def test_start_resolves_shared_folder_runtime(terminal_client, monkeypatch):
    folder = store.create_folder('Project')
    chat = store.create('Chat', folder['id'])
    create = Mock(return_value={'id': 'shell'})
    monkeypatch.setattr(terminals, 'create', create)
    response = terminal_client.post(f"/api/conversations/{chat['id']}/terminal", json={'cols': 90, 'rows': 30})
    assert response.status_code == 201
    create.assert_called_once_with('folder_' + folder['id'], 'browser-one', {'cols': 90, 'rows': 30})
    response = terminal_client.post(f"/api/folders/{folder['id']}/terminal", json={})
    assert response.status_code == 201
    assert create.call_args.args[:2] == ('folder_' + folder['id'], 'browser-one')


def test_missing_workspace_never_starts_a_container(terminal_client, monkeypatch):
    create = Mock()
    monkeypatch.setattr(terminals, 'create', create)
    assert terminal_client.post('/api/conversations/not-an-id/terminal').status_code == 404
    assert terminal_client.post('/api/conversations/11111111-1111-1111-1111-111111111111/terminal').status_code == 404
    create.assert_not_called()


def register(chat, owner='browser-one'):
    active = Mock(id='shell', runtime_id=chat['id'], owner=owner)
    active.read.return_value = {'output': 'hello\r\n', 'closed': False, 'truncated': False}
    terminals._sessions['shell'] = active
    return active


def test_read_write_resize_close(terminal_client):
    chat = store.create('Terminal')
    active = register(chat)
    url = f"/api/conversations/{chat['id']}/terminal/shell"
    response = terminal_client.get(url)
    assert response.json['output'] == 'hello\r\n'
    assert response.headers['Cache-Control'] == 'no-store'
    assert terminal_client.post(url, json={'input': 'echo hello\n'}).status_code == 200
    active.send.assert_called_with({'input': 'echo hello\n'})
    assert terminal_client.post(url, json={'cols': 120, 'rows': 40}).status_code == 200
    active.send.assert_called_with({'cols': 120, 'rows': 40})
    assert terminal_client.delete(url).status_code == 200
    active.close.assert_called_once()
    assert terminal_client.get(url).status_code == 404


def test_session_cannot_cross_browser_or_workspace(terminal_client):
    chat, other = store.create('One'), store.create('Two')
    active = register(chat, owner='browser-two')
    url = f"/api/conversations/{chat['id']}/terminal/shell"
    for method in ('get', 'post', 'delete'):
        assert getattr(terminal_client, method)(url).status_code == 404
    active.owner = 'browser-one'
    assert terminal_client.get(f"/api/conversations/{other['id']}/terminal/shell").status_code == 404
    active.send.assert_not_called()
    active.close.assert_not_called()


@pytest.mark.parametrize('body', [[], ['command'], {'input': 3}, {'input': 'a' * 16385},
    {'cols': 0}, {'rows': 201}, {'cols': True}, {'cols': '90'}])
def test_invalid_messages(terminal_client, body):
    chat = store.create('Terminal')
    active = register(chat)
    response = terminal_client.post(f"/api/conversations/{chat['id']}/terminal/shell", json=body)
    # Empty lists are rejected as well as nonempty ones.
    assert response.status_code == 400
    active.send.assert_not_called()


def test_terminal_requires_session_and_csrf(app):
    app.config['DISABLE_REQUEST_SECURITY'] = False
    chat = store.create('Terminal')
    client = app.test_client()
    url = f"/api/conversations/{chat['id']}/terminal"
    assert client.post(url, json={}).status_code == 403
    client.get('/')
    assert client.post(url, json={}).status_code == 403


def test_output_is_bounded_and_utf8_can_span_polls():
    active = terminals.Terminal.__new__(terminals.Terminal)
    active.buffer = bytearray()
    active.lock = threading.Lock()
    active.truncated = False
    active.closed = threading.Event()
    active.decoder = codecs.getincrementaldecoder('utf-8')('replace')
    active._append(b'x' * (terminals.MAX_BUFFER + 100))
    result = active.read()
    assert len(result['output']) == terminals.MAX_BUFFER
    assert result['truncated']
    active._append('☀'.encode()[:2])
    assert active.read()['output'] == ''
    active._append('☀'.encode()[2:])
    assert active.read()['output'] == '☀'
    assert not active.read()['truncated']


def test_session_limit_does_not_spawn(monkeypatch):
    monkeypatch.setattr(terminals, '_sessions', {str(i): Mock() for i in range(terminals.MAX_SESSIONS)})
    for active in terminals._sessions.values():
        active.closed.is_set.return_value = False
    constructor = Mock()
    monkeypatch.setattr(terminals, 'Terminal', constructor)
    with pytest.raises(ValueError, match='Too many terminals'):
        terminals.create('chat', 'owner', {})
    constructor.assert_not_called()


def test_interactive_activity_does_not_lock_out_files():
    with sandbox_runtime.runtime_activity('shell-runtime'):
        assert sandbox_runtime.is_active('shell-runtime')
        acquired = []
        # Avoid filesystem quota setup: check the independent runtime lock directly.
        def check_lock():
            lock = sandbox_runtime.runtime_lock('shell-runtime')
            if lock.acquire(timeout=.1):
                acquired.append(True)
                lock.release()
        worker = threading.Thread(target=check_lock)
        worker.start()
        worker.join(timeout=1)
        assert acquired == [True]
    assert not sandbox_runtime.is_active('shell-runtime')
