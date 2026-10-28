"""Session-protected terminals attached to the same runtime as workspace files."""
from flask import Blueprint, jsonify, request, session

from routes_files import _bad_conv_id
import store
import terminal_service

blueprint = Blueprint('terminal', __name__)


@blueprint.route('/api/conversations/<workspace_id>/terminal', defaults={'scope': 'conversation'}, methods=['POST'])
@blueprint.route('/api/folders/<workspace_id>/terminal', defaults={'scope': 'folder'}, methods=['POST'])
@blueprint.route('/api/conversations/<workspace_id>/terminal/<terminal_id>', defaults={'scope': 'conversation'}, methods=['GET', 'POST', 'DELETE'])
@blueprint.route('/api/folders/<workspace_id>/terminal/<terminal_id>', defaults={'scope': 'folder'}, methods=['GET', 'POST', 'DELETE'])
def terminal(scope, workspace_id, terminal_id=None):
    if err := _bad_conv_id(workspace_id):
        return err
    data = store.load(workspace_id) if scope == 'conversation' else store.get_folder(workspace_id)
    if not data:
        return jsonify(error='Workspace not found'), 404
    runtime_id = store.runtime_id(workspace_id, data) if scope == 'conversation' else 'folder_' + workspace_id
    owner = session.get('client_id')
    if not owner:
        return jsonify(error='Open the application before using its terminal'), 403
    body = request.get_json(silent=True)
    if body is None:
        body = {}
    if not isinstance(body, dict):
        return jsonify(error='Expected a JSON object'), 400
    try:
        if terminal_id is None:
            return jsonify(terminal_service.create(runtime_id, owner, body)), 201
        active = terminal_service.get(terminal_id, runtime_id, owner)
        if active is None:
            return jsonify(error='Terminal session not found; reconnect to start a new shell'), 404
        if request.method == 'DELETE':
            terminal_service.remove(active)
            return jsonify(ok=True)
        if request.method == 'GET':
            response = jsonify(active.read())
            response.headers['Cache-Control'] = 'no-store'
            return response
        message = {}
        if 'input' in body:
            if not isinstance(body['input'], str) or len(body['input'].encode('utf-8')) > 16384:
                raise ValueError('Terminal input exceeds 16 KiB')
            message['input'] = body['input']
        if 'cols' in body or 'rows' in body:
            message.update(terminal_service.dimensions(body))
        active.send(message)
        return jsonify(ok=True)
    except (ValueError, OSError) as exc:
        return jsonify(error=str(exc)[:500]), 400
    except RuntimeError as exc:
        return jsonify(error=str(exc)[:500]), 503
