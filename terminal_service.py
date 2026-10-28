"""Interactive Docker shells with bounded polling transport and session ownership.

The PTY lives inside the Linux container, so this also works on Windows hosts.
Only JSON is written to docker-exec's stdin; no host shell is ever invoked.
"""
import atexit
import codecs
import json
import os
import secrets
import subprocess
import threading
import time

import container_service
import docker_cli
import sandbox_runtime
import storage_safeguards

MAX_BUFFER = 256 * 1024
IDLE_SECONDS = 90
MAX_SESSIONS = 8
_sessions = {}
_lock = threading.RLock()

# Trusted bridge sent directly to the container's isolated Python interpreter.
BRIDGE = r'''
import errno, fcntl, json, os, pty, select, signal, struct, sys, termios
pid, master = pty.fork()
if pid == 0:
    os.chdir('/workspace')
    os.environ['TERM'] = 'xterm-256color'
    os.environ['PS1'] = r'\u@workspace:\w\$ '
    os.execv('/bin/bash', ['bash', '--noprofile', '--norc', '-i'])
pending = b''
try:
    while True:
        ready, _, _ = select.select([master, 0], [], [], 1)
        if master in ready:
            try:
                data = os.read(master, 16384)
            except OSError as exc:
                if exc.errno == errno.EIO:
                    break
                raise
            if not data:
                break
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
        if 0 in ready:
            data = os.read(0, 65536)
            if not data:
                break
            pending += data
            while b'\n' in pending:
                line, pending = pending.split(b'\n', 1)
                msg = json.loads(line)
                if 'input' in msg:
                    data = msg['input'].encode('utf-8')
                    while data:
                        count = os.write(master, data)
                        data = data[count:]
                if 'cols' in msg:
                    fcntl.ioctl(master, termios.TIOCSWINSZ,
                                struct.pack('HHHH', msg['rows'], msg['cols'], 0, 0))
finally:
    # Bash puts background jobs in separate groups. They still belong to the
    # PTY's session, so clean them up too without touching model/tool processes.
    for entry in os.scandir('/proc'):
        if entry.name.isdigit():
            try:
                child = int(entry.name)
                if os.getsid(child) == pid:
                    os.kill(child, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
    # Foreground jobs have their own process group; terminate those as well.
    try:
        foreground = os.tcgetpgrp(master)
        if foreground != os.getpgrp():
            os.killpg(foreground, signal.SIGKILL)
    except OSError:
        pass
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass
    os.close(master)
    os.waitpid(pid, 0)
'''


def dimensions(body):
    cols, rows = body.get('cols', 80), body.get('rows', 24)
    if (type(cols) is not int or type(rows) is not int
            or not 2 <= cols <= 500 or not 1 <= rows <= 200):
        raise ValueError('Invalid terminal dimensions')
    return {'cols': cols, 'rows': rows}


class Terminal:
    def __init__(self, runtime_id, owner, size):
        self.id = secrets.token_urlsafe(32)
        self.runtime_id, self.owner = runtime_id, owner
        self.last_seen = time.monotonic()
        self.buffer = bytearray()
        self.truncated = False
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.closed = threading.Event()
        self.decoder = codecs.getincrementaldecoder('utf-8')('replace')
        with sandbox_runtime.runtime_operation(runtime_id):
            container_service.ensure_container(runtime_id)
            storage_safeguards.check(runtime_id, docker=True)
            self.proc = subprocess.Popen(docker_cli.argv([
                'exec', '-i', '--workdir', '/workspace',
                container_service.container_name(runtime_id),
                '/opt/venv/bin/python', '-I', '-u', '-c', BRIDGE,
            ]), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                **docker_cli._windowless_process_kwargs())
        try:
            self.send(size)
        except Exception:
            self.close()
            raise
        threading.Thread(target=self._read, daemon=True, name='terminal-output').start()
        threading.Thread(target=self._watch, daemon=True, name='terminal-lifetime').start()

    def _append(self, chunk):
        with self.lock:
            self.buffer.extend(chunk)
            if len(self.buffer) > MAX_BUFFER:
                del self.buffer[:-MAX_BUFFER]
                self.truncated = True

    def _read(self):
        try:
            while chunk := os.read(self.proc.stdout.fileno(), 16384):
                self._append(chunk)
        finally:
            self.proc.stdout.close()
            self.close()

    def _watch(self):
        with sandbox_runtime.runtime_activity(self.runtime_id):
            while not self.closed.wait(2):
                if time.monotonic() - self.last_seen > IDLE_SECONDS:
                    self._append(b'\r\n[Terminal disconnected after inactivity]\r\n')
                    break
                try:
                    storage_safeguards.check(self.runtime_id, docker=True)
                except Exception:
                    self._append(b'\r\n[Terminal stopped: workspace storage limit reached]\r\n')
                    break
            self.close()
        # Retain final output briefly so the client can observe shell exit.
        with _lock:
            if time.monotonic() - self.last_seen > IDLE_SECONDS:
                _sessions.pop(self.id, None)

    def send(self, body):
        if self.closed.is_set() or self.proc.poll() is not None:
            raise ValueError('Terminal has exited; reconnect to start a new shell')
        with self.write_lock:
            self.proc.stdin.write(json.dumps(body).encode() + b'\n')
            self.proc.stdin.flush()

    def read(self):
        with self.lock:
            output = self.decoder.decode(bytes(self.buffer))
            self.buffer.clear()
            truncated, self.truncated = self.truncated, False
        return {'output': output, 'closed': self.closed.is_set(), 'truncated': truncated}

    def close(self):
        if self.closed.is_set():
            return
        self.closed.set()
        # EOF makes the bridge kill its PTY process groups before exiting.
        with self.write_lock:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=3)


def create(runtime_id, owner, body):
    size = dimensions(body)
    with _lock:
        # Drop old exited sessions and enforce a global resource bound.
        for key, terminal in list(_sessions.items()):
            if terminal.closed.is_set():
                _sessions.pop(key, None)
        if len(_sessions) >= MAX_SESSIONS:
            raise ValueError('Too many terminals are open; close an existing terminal first')
        terminal = Terminal(runtime_id, owner, size)
        _sessions[terminal.id] = terminal
    return {'id': terminal.id}


def get(terminal_id, runtime_id, owner):
    with _lock:
        terminal = _sessions.get(terminal_id)
        if not terminal or terminal.owner != owner or terminal.runtime_id != runtime_id:
            return None
        terminal.last_seen = time.monotonic()
        return terminal


def remove(terminal):
    terminal.close()
    with _lock:
        _sessions.pop(terminal.id, None)


def close_all():
    with _lock:
        terminals = list(_sessions.values())
        _sessions.clear()
    for terminal in terminals:
        terminal.close()


atexit.register(close_all)
