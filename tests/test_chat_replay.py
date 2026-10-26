"""Replay snapshots and approval lifecycle without a model or Docker."""
import json
import threading

import routes_chat


def test_replay_freezes_payloads_and_does_not_repeat_resolved_approvals():
    state = {'events': [], 'approvals': {}, 'condition': threading.Condition()}
    messages = [{'role': 'assistant', 'content': 'Original'}]
    routes_chat._publish_stream_event(state, {'type': 'assistant_done', 'messages': messages})
    messages[0]['content'] = 'Changed later'
    replay = json.loads(state['events'][0].removeprefix('data: '))
    assert replay['messages'][0]['content'] == 'Original'
    routes_chat._publish_stream_event(state, {'type': 'tool_approval_required', 'call_id': 'one'})
    routes_chat._publish_stream_event(state, {'type': 'tool_result', 'call_id': 'one'})
    assert len(state['events']) == 3
    assert state['events'][1].startswith(':')
    assert not state['approvals']
