"""Opt-in Docker tests for real PTY behavior, workspace sharing, and cleanup."""
import os
import time

import pytest

import container_files
import docker_cli
import container_service
import terminal_service as terminals
from tests.test_builtin_docker import runtime  # isolated Docker runtime fixture

pytestmark = pytest.mark.skipif(os.getenv('VIRTUS_RUN_DOCKER_TESTS') != '1', reason='Opt-in Docker integration')


def wait_for(active, marker, timeout=10):
    output = ''
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = active.read()
        output += result['output']
        if marker in output:
            return output
        time.sleep(.05)
    raise AssertionError(f'Missing {marker!r}: {output!r}')


def test_interactive_shell_workspace_resize_interrupt_and_cleanup(runtime):
    active = terminals.Terminal(runtime, 'test-browser', {'cols': 90, 'rows': 30})
    try:
        wait_for(active, '/workspace')
        active.send({'input': "printf 'shared terminal file' > terminal.txt\n"})
        wait_for(active, '/workspace')
        result, status = container_files.operation(runtime, 'preview', '/workspace/terminal.txt')
        assert status == 200 and result['content'] == 'shared terminal file'
        active.send({'input': 'mkdir -p subdir; cd subdir\n'})
        wait_for(active, '/workspace/subdir')
        active.send({'input': "printf 'CWD=%s\\n' \"$PWD\"\n"})
        assert 'CWD=/workspace/subdir' in wait_for(active, 'CWD=/workspace/subdir')
        active.send({'cols': 100, 'rows': 35})
        active.send({'input': 'stty size\n'})
        wait_for(active, '35 100')
        active.send({'input': 'sleep 120\n'})
        time.sleep(.2)
        active.send({'input': '\x03'})
        wait_for(active, '^C')
        active.send({'input': "printf 'INTERRUPT_OK\\n'\n"})
        wait_for(active, '\rINTERRUPT_OK\r\n')
        # Closing a terminal must kill its running foreground job as well as Bash.
        active.send({'input': 'sleep 124 & sleep 123\n'})
        time.sleep(.2)
    finally:
        active.close()
    assert active.proc.poll() is not None
    result = docker_cli.run(['exec', container_service.container_name(runtime), 'pgrep', '-f', '^sleep 12[34]$'])
    assert result.returncode == 1


def test_idle_terminal_expires(runtime, monkeypatch):
    active = terminals.Terminal(runtime, 'test-browser', {'cols': 80, 'rows': 24})
    try:
        active.last_seen = time.monotonic() - terminals.IDLE_SECONDS - 1
        assert active.closed.wait(6)
        active.proc.wait(timeout=5)
        assert active.proc.poll() is not None
    finally:
        active.close()
