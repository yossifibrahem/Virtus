"""Persistent cross-turn MCP stdio session pool.

AnyIO-backed MCP context managers must be exited from the same asyncio Task
that entered them. McpSessionPool uses one dedicated worker coroutine for the
whole lifetime of the pool so open, invoke, and close all happen in the same
Task. Do not replace the worker pattern with run_coroutine_threadsafe() per
call unless you also preserve same-task cleanup semantics.

Design note -- why asyncio.Queue + call_soon_threadsafe, not threading.Queue:
The worker coroutine must never block the event loop thread. The original code
used asyncio.to_thread(threading_queue.get) to avoid that, but to_thread()
offloads onto the loop's default ThreadPoolExecutor whose threads are
non-daemon -- an unclosed pool would leave a thread blocked on queue.get()
forever, preventing process exit.

The correct bridge between sync callers and an async worker is:
  - asyncio.Queue for the worker to consume (pure coroutine await, no threads)
  - loop.call_soon_threadsafe() for sync callers to enqueue jobs safely

This eliminates executor threads entirely. The worker thread itself is already
daemon=True, so process exit is never blocked.
"""
from __future__ import annotations

import asyncio
import storage_safeguards
import copy
import logging
import os
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeoutError

log = logging.getLogger(__name__)


def _tool_timeout() -> float:
    try:
        value = float(os.getenv("VIRTUS_MCP_TOOL_TIMEOUT", "120"))
    except (TypeError, ValueError):
        return 120.0
    return value if value > 0 else 120.0


class McpSessionPool:
    """Reuse MCP stdio sessions for multiple tool calls across turns."""

    def __init__(self, conv_id: str = "", tool_timeout: float | None = None) -> None:
        self.conv_id = conv_id
        self.tool_timeout = _tool_timeout() if tool_timeout is None else max(0.01, tool_timeout)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._aqueue: asyncio.Queue | None = None
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._sessions: dict[str, dict] = {}
        self._closed = False
        self._current = None
        self._lock = threading.Lock()

    def __enter__(self) -> "McpSessionPool":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def start(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("MCP session pool is closed")
            if not self._thread:
                self._thread = threading.Thread(target=self._thread_main, name="mcp-session-pool", daemon=True)
                self._thread.start()
        self._ready.wait()  # block until the event loop and queue are initialised

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._worker())
        except Exception:
            log.exception("[mcp] session pool worker crashed")

    async def _worker(self) -> None:
        # Initialise the asyncio.Queue and capture the running loop, then
        # signal start() that it is safe to call call_soon_threadsafe().
        self._aqueue = asyncio.Queue()
        self._loop = asyncio.get_running_loop()
        self._ready.set()

        while True:
            # Pure coroutine await -- no executor threads, no blocking.
            job = await self._aqueue.get()
            op = job[0]
            if op == "invoke":
                _, server_name, server_config, tool_name, arguments, future = job
                # A caller that timed out while queued must never execute later.
                if not future.set_running_or_notify_cancel():
                    continue
                try:
                    # Persistent stdio sessions retain AnyIO task-group scopes
                    # between calls. A per-call CancelScope would exit out of
                    # nesting order. Cancel the owning asyncio task once instead.
                    self._current = (future, asyncio.current_task())
                    result = await self._invoke(server_name, server_config, tool_name, arguments)
                except asyncio.CancelledError:
                    await self._close_async()
                    if not future.done():
                        future.set_exception(RuntimeError('MCP call cancelled; shared runtime interrupted'))
                except TimeoutError:
                    await self._close_one_session(server_name)
                    if not future.done():
                        future.set_exception(TimeoutError(
                            f"MCP tool '{tool_name}' timed out after {self.tool_timeout:g} seconds"
                        ))
                except Exception as exc:
                    if not future.done():
                        future.set_exception(exc)
                else:
                    if not future.done():
                        future.set_result(result)
                finally:
                    self._current = None
            elif op == "close":
                _, future = job
                try:
                    await self._close_async()
                except Exception as exc:
                    future.set_exception(exc)
                else:
                    future.set_result(None)
                break

    async def _get_session(self, server_name: str, server_config: dict):
        if entry := self._sessions.get(server_name):
            if entry["config"] == server_config:
                return entry["session"]
            # Contexts are nested in opening order; unwind them in reverse order.
            await self._close_async()
        return await self._open_session(server_name, server_config)

    async def _open_session(self, server_name: str, server_config: dict):
        """Open a brand-new stdio session and cache it."""
        from mcp import ClientSession
        from mcp_service import _build_server_params, _stdio_client

        params = _build_server_params(server_name, server_config, conv_id=self.conv_id)
        stdio_cm = _stdio_client(params)
        reader, writer = await stdio_cm.__aenter__()
        session_cm = ClientSession(
            reader,
            writer,
            read_timeout_seconds=self.tool_timeout,
        )
        try:
            session = await session_cm.__aenter__()
        except BaseException:
            await stdio_cm.__aexit__(None, None, None)
            raise
        self._sessions[server_name] = {
            "config": copy.deepcopy(server_config),
            "session": session,
            "session_cm": session_cm,
            "stdio_cm": stdio_cm,
        }
        try:
            await session.initialize()
        except BaseException:
            await self._close_one_session(server_name)
            raise
        return session

    async def _invoke(self, server_name: str, server_config: dict, tool_name: str, arguments: dict) -> str:
        session = await self._get_session(server_name, server_config)
        try:
            result = await session.call_tool(tool_name, arguments)
        except Exception as exc:
            if "timed out while waiting" in str(exc).lower():
                raise TimeoutError(
                    f"MCP tool '{tool_name}' timed out after {self.tool_timeout:g} seconds"
                ) from exc
            # A failed transport may have already executed a tool with side effects.
            # Drop the connection; let the caller decide whether to try again.
            await self._close_one_session(server_name)
            raise
        parts, remaining = [], 65536
        for item in result.content:
            value = item.text if hasattr(item, 'text') else str(item)
            value = value.encode('utf-8')[:remaining].decode('utf-8', 'ignore')
            parts.append(value)
            remaining -= len(value.encode('utf-8')) + 1
            if remaining <= 0:
                break
        return '\n'.join(parts)

    async def _close_one_session(self, server_name: str) -> None:
        """Tear down and remove the cached session for a single server."""
        entry = self._sessions.pop(server_name, None)
        if entry is None:
            return
        try:
            await entry["session_cm"].__aexit__(None, None, None)
        except Exception:
            log.exception("[mcp] error closing stale client session for %r", server_name)
        try:
            await entry["stdio_cm"].__aexit__(None, None, None)
        except Exception:
            log.exception("[mcp] error closing stale stdio session for %r", server_name)

    def invoke_tool(self, server_name: str, server_config: dict, tool_name: str, arguments: dict, cancel_event=None) -> str:
        if self._closed:
            raise RuntimeError("MCP session pool is closed")
        self.start()
        if self._loop is None or self._aqueue is None:
            raise RuntimeError("MCP session pool failed to start")

        from concurrent.futures import Future

        future: Future = Future()
        with self._lock:
            if self._closed:
                raise RuntimeError("MCP session pool is closed")
            # call_soon_threadsafe is the correct way to enqueue work from a
            # sync thread onto a running asyncio event loop without blocking it.
            self._loop.call_soon_threadsafe(
                self._aqueue.put_nowait,
                ("invoke", server_name, server_config, tool_name, arguments, future),
            )
        # Block *outside* the lock. Holding a lock while waiting on a future is a
        # deadlock risk: any code path that needs self._lock while this call is
        # in-flight (e.g. a concurrent close()) would be permanently blocked.
        deadline = time.monotonic() + self.tool_timeout + 5
        next_disk_check = time.monotonic() + 2
        reason = None
        while True:
            try:
                return future.result(timeout=.1)
            except FutureTimeoutError:
                if future.done():
                    raise
            if cancel_event is not None and cancel_event.is_set():
                reason = 'MCP call cancelled'
            elif time.monotonic() >= deadline:
                reason = 'MCP worker response timeout'
            if self.conv_id and time.monotonic() >= next_disk_check:
                try:
                    storage_safeguards.check(self.conv_id, docker=True)
                except Exception as exc:
                    reason = str(exc)
                next_disk_check = time.monotonic() + 2
            if reason:
                # Cancel queued calls before they can run; cancel active work in
                # its owning task so AnyIO context managers unwind correctly.
                if not future.cancel():
                    def interrupt():
                        if self._current and self._current[0] is future:
                            self._current[1].cancel()
                    self._loop.call_soon_threadsafe(interrupt)
                    import container_service
                    if self.conv_id:
                        container_service.stop_container_process(self.conv_id)
                    try:
                        future.result(timeout=6)
                    except Exception:
                        pass
                raise RuntimeError(reason + '; shared runtime interrupted')

    async def _close_async(self) -> None:
        for server_name in reversed(list(self._sessions)):
            await self._close_one_session(server_name)

    def close(self, timeout: float | None = None) -> None:
        from concurrent.futures import Future

        future: Future = Future()
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if not self._thread:
                return
        self._ready.wait()
        with self._lock:
            self._loop.call_soon_threadsafe(self._aqueue.put_nowait, ("close", future))
        wait_seconds = self.tool_timeout + 5 if timeout is None else max(0.01, timeout)
        deadline = time.monotonic() + wait_seconds
        try:
            future.result(timeout=wait_seconds)
        except FutureTimeoutError:
            log.warning("[mcp] timed out closing session pool for %s", self.conv_id)
        self._thread.join(timeout=max(0.0, min(2.0, deadline - time.monotonic())))
