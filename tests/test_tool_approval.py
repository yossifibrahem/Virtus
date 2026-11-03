"""Bounded approval requests with explicit synchronization and worker cleanup."""
import threading
from types import SimpleNamespace

import pytest

import tool_approval as svc


@pytest.fixture
def approval_request():
    workers = []

    def start(stream_id="stream", call_id="call"):
        cancel, ready = threading.Event(), threading.Event()
        events, result = [], []

        def publish(event):
            events.append(event)
            ready.set()

        def request():
            result.append(svc.request_tool_approval(
                stream_id, call_id, "bash", {"command": "ls"}, publish, cancel,
                timeout_seconds=2,
            ))

        thread = threading.Thread(target=request, daemon=True)
        workers.append((thread, cancel, stream_id, call_id))
        thread.start()
        assert ready.wait(timeout=1)
        return SimpleNamespace(thread=thread, cancel=cancel, events=events, result=result)

    yield start
    for thread, cancel, stream_id, call_id in workers:
        cancel.set()
        svc.resolve_tool_approval(stream_id, call_id, approved=False)
        thread.join(timeout=2)
        assert not thread.is_alive()
    assert not svc._pending_approvals


@pytest.mark.parametrize("approved", [True, False])
def test_approval_publishes_decision_and_cleans_pending_slot(approval_request, approved):
    request = approval_request()
    assert request.events == [{
        "type": "tool_approval_required", "call_id": "call",
        "name": "bash", "args": {"command": "ls"},
    }]
    svc.resolve_tool_approval("unknown-stream", "call", approved=True)
    svc.resolve_tool_approval("stream", "unknown-call", approved=True)
    with svc._pending_approvals_lock:
        assert not svc._pending_approvals["stream"]["call"]["event"].is_set()
    svc.resolve_tool_approval("stream", "call", approved=approved)
    request.thread.join(timeout=2)
    assert request.result == [approved]
    assert "stream" not in svc._pending_approvals
    svc.resolve_tool_approval("stream", "call", approved=approved)


def test_cancelled_approval_is_denied_and_cleaned(approval_request):
    request = approval_request()
    request.cancel.set()
    request.thread.join(timeout=2)
    assert request.result == [False]
    assert "stream" not in svc._pending_approvals


def test_expired_approval_is_denied_and_cleaned():
    events = []
    assert svc.request_tool_approval(
        "expired", "call", "bash", {}, events.append, threading.Event(),
        timeout_seconds=0,
    ) is False
    assert len(events) == 1
    assert "expired" not in svc._pending_approvals


@pytest.mark.parametrize("other_stream,other_call", [("stream", "other-call"), ("other-stream", "call")])
def test_concurrent_approvals_resolve_independently(approval_request, other_stream, other_call):
    first = approval_request()
    second = approval_request(other_stream, other_call)
    svc.resolve_tool_approval("stream", "call", approved=False)
    first.thread.join(timeout=2)
    assert first.result == [False]
    assert second.thread.is_alive()
    svc.resolve_tool_approval(other_stream, other_call, approved=True)
    second.thread.join(timeout=2)
    assert second.result == [True]
