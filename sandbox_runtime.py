"""Shared sandbox execution and activity tracking, independent of tool families."""
from __future__ import annotations
from contextlib import contextmanager
import subprocess
import threading
import weakref
import time

import container_service
import docker_cli

_LOCKS = weakref.WeakValueDictionary()
_GUARD = threading.Lock()
_ACTIVE = {}


def runtime_lock(runtime_id):
    with _GUARD:
        lock = _LOCKS.get(runtime_id)
        if lock is None:
            lock = _LOCKS[runtime_id] = threading.RLock()
        return lock


@contextmanager
def runtime_operation(runtime_id, cancel=None):
    lock = runtime_lock(runtime_id)
    while not lock.acquire(timeout=.1):
        if cancel and cancel.is_set():
            raise ValueError('Operation cancelled')
    registered = False
    try:
        if cancel and cancel.is_set():
            raise ValueError('Operation cancelled')
        with _GUARD:
            _ACTIVE[runtime_id] = _ACTIVE.get(runtime_id, 0) + 1
            registered = True
        yield
    finally:
        if registered:
            with _GUARD:
                remaining = _ACTIVE[runtime_id] - 1
                if remaining:
                    _ACTIVE[runtime_id] = remaining
                else:
                    _ACTIVE.pop(runtime_id)
        container_service._touch(runtime_id)
        lock.release()


def is_active(runtime_id):
    with _GUARD:
        return runtime_id in _ACTIVE


class Capture:
    def __init__(self, limit, tail=False):
        self.limit, self.tail = limit, tail
        self.first = bytearray()
        self.last = bytearray()
        self.total = 0
    def add(self, chunk):
        self.total += len(chunk)
        first_limit = self.limit // 2 if self.tail else self.limit
        needed = max(0, first_limit - len(self.first))
        self.first.extend(chunk[:needed])
        if self.tail:
            self.last.extend(chunk[needed:])
            del self.last[:max(0, len(self.last) - self.limit // 2)]
    def value(self):
        if self.total > self.limit and self.tail:
            return bytes(self.first[:-80]) + b'\n[output truncated; redirect to a file for full logs]\n' + bytes(self.last)
        return bytes(self.first) + bytes(self.last) if self.last else bytes(self.first)


def docker_operation(runtime_id, argv, payload=b'', timeout=30, cancel=None, limit=8*1024*1024, tail=False):
    """Bound both transport output and wall time; never invoke a host shell."""
    container_service.ensure_container(runtime_id)
    command = ['exec', '-i', '--workdir', '/workspace', container_service.container_name(runtime_id), *argv]
    proc = subprocess.Popen(docker_cli.argv(command), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, **docker_cli._windowless_process_kwargs())
    output, errors = Capture(limit, tail), Capture(4096)
    def drain(pipe, capture):
        try:
            while chunk := pipe.read(8192):
                capture.add(chunk)
        finally:
            pipe.close()
    def feed():
        try:
            for chunk in payload if isinstance(payload, tuple) else (payload,):
                proc.stdin.write(chunk)
        except (BrokenPipeError, OSError):
            pass
        finally:
            proc.stdin.close()
    threads = [threading.Thread(target=drain, args=(proc.stdout, output), daemon=True),
               threading.Thread(target=drain, args=(proc.stderr, errors), daemon=True),
               threading.Thread(target=feed, daemon=True)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + timeout
    interrupted = None
    while proc.poll() is None:
        if cancel and cancel.is_set():
            interrupted = 'Operation cancelled'
        elif time.monotonic() >= deadline:
            interrupted = 'Operation timed out'
        elif not tail and output.total > limit:
            interrupted = 'Worker output exceeded limit'
        if interrupted:
            # Killing docker-exec alone leaves container children running. Kill the
            # entire runtime, including detached descendants, before returning.
            try:
                result = docker_cli.run(['kill', container_service.container_name(runtime_id)], timeout=10)
                if result.returncode:
                    interrupted += '; could not confirm container shutdown'
            except Exception:
                interrupted += '; could not confirm container shutdown'
            proc.kill()
            break
        time.sleep(.05)
    proc.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=2)
    if any(thread.is_alive() for thread in threads):
        docker_cli.run(['kill', container_service.container_name(runtime_id)], timeout=10)
        interrupted = interrupted or 'Worker descendants retained transport pipes'
    if interrupted:
        container_service._invalidate_mcp_pool(runtime_id)
        raise ValueError(interrupted + '; shared runtime interrupted')
    if output.total > limit and not tail:
        raise ValueError('Worker output exceeded limit')
    return proc.returncode, output.value(), errors.value()
