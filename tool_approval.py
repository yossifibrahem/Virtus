"""Tool approval gate — blocking approval requests with per-stream state."""
from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable

Publish = Callable[[dict], None]

# Keyed by stream_id → { call_id → {"event": Event, "approved": bool} }
_pending_approvals: dict[str, dict] = {}
_pending_approvals_lock = threading.Lock()


def _approval_timeout() -> float:
    try:
        value = float(os.getenv("VIRTUS_TOOL_APPROVAL_TIMEOUT", "600"))
    except (TypeError, ValueError):
        return 600.0
    return value if value > 0 else 600.0


def resolve_tool_approval(stream_id: str, call_id: str, approved: bool) -> None:
    """Called from the /api/chat/approve route to unblock a waiting tool call."""
    with _pending_approvals_lock:
        slot = _pending_approvals.get(stream_id, {}).get(call_id)
    if slot:
        slot["approved"] = approved
        slot["event"].set()


def request_tool_approval(
    stream_id: str,
    call_id: str,
    name: str,
    args: dict,
    publish: Publish,
    cancel_event: threading.Event,
    timeout_seconds: float | None = None,
) -> bool:
    """Wait for approval until resolved, cancelled, or the deadline expires."""
    wait_event = threading.Event()
    slot: dict = {"event": wait_event, "approved": False}
    timeout = _approval_timeout() if timeout_seconds is None else max(0.0, timeout_seconds)
    deadline = time.monotonic() + timeout

    with _pending_approvals_lock:
        _pending_approvals.setdefault(stream_id, {})[call_id] = slot

    publish({"type": "tool_approval_required", "call_id": call_id, "name": name, "args": args})

    while not wait_event.is_set() and not cancel_event.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        wait_event.wait(timeout=min(0.5, remaining))

    with _pending_approvals_lock:
        pending_for_stream = _pending_approvals.get(stream_id)
        if pending_for_stream is not None:
            pending_for_stream.pop(call_id, None)
            if not pending_for_stream:
                _pending_approvals.pop(stream_id, None)

    if cancel_event.is_set():
        return False
    return bool(slot["approved"])
