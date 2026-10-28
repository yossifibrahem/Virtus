import store
import routes_chat


def test_session_and_csrf_required(app):
    app.config['DISABLE_REQUEST_SECURITY'] = False
    client = app.test_client()
    assert client.get('/api/conversations').status_code == 403
    assert client.get('/').status_code == 200
    assert client.get('/api/conversations').status_code == 200
    assert client.post('/api/conversations', json={}).status_code == 403
    with client.session_transaction() as session:
        token = session['csrf']
    assert client.post('/api/conversations', json={}, headers={'X-Virtus-CSRF': token}).status_code == 201
    assert client.get('/api/conversations', headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.get('/api/conversations', headers={'Host': 'evil.example'}).status_code == 403
    assert client.get('/api/conversations', headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403


def test_stream_owner(app, tmp_virtus):
    client = app.test_client()
    conv = store.create()
    routes_chat._active_streams['foreign'] = {'owner': 'another-client', 'conv_id': conv['id']}
    try:
        assert client.post('/api/chat/stream', json={'conv_id': conv['id'], 'stream_id': 'foreign', 'attach': True}).status_code == 404
        assert client.post('/api/chat/cancel', json={'stream_id': 'foreign'}).status_code == 404
        assert client.post('/api/chat/approve', json={'stream_id': 'foreign'}).status_code == 404
    finally:
        routes_chat._active_streams.pop('foreign')
