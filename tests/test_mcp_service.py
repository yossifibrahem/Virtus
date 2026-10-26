"""
Tests for mcp_service.py — configuration and MCP session lifecycle.

MCP_CONFIG_FILE is redirected to a temp file for every test via the
`isolated_mcp_config` fixture so no mcp.json in the project root is
ever touched.

MCP sessions are faked so these tests do not require Docker or a running server.
"""
from __future__ import annotations

import asyncio
import json
import sys
import threading
import types
from datetime import timedelta
import pytest

import mcp_service


def test_windowed_app_routes_mcp_stderr_to_valid_sink(monkeypatch):
    captured = {}

    def fake_stdio_client(params, **kwargs):
        captured["params"] = params
        captured.update(kwargs)
        return object()

    fake_module = types.ModuleType("mcp.client.stdio")
    fake_module.stdio_client = fake_stdio_client
    monkeypatch.setitem(sys.modules, "mcp.client.stdio", fake_module)
    monkeypatch.setattr(mcp_service.sys, "stderr", None)
    monkeypatch.setattr(mcp_service, "_mcp_stderr_handle", None)

    transport = mcp_service._stdio_client({"command": "docker"})

    assert transport is not None
    assert captured["params"] == {"command": "docker"}
    assert captured["errlog"].fileno() >= 0
    captured["errlog"].close()


def test_close_all_persistent_pools_runs_in_parallel(monkeypatch):
    release = threading.Event()
    both_started = threading.Event()
    started = []
    guard = threading.Lock()

    class Pool:
        def close(self, timeout=None):
            with guard:
                started.append(timeout)
                if len(started) == 2:
                    both_started.set()
            assert release.wait(timeout=2)

    monkeypatch.setattr(mcp_service, "_persistent_pools", {"one": Pool(), "two": Pool()})
    worker = threading.Thread(target=mcp_service.close_all_persistent_pools)
    worker.start()
    assert both_started.wait(timeout=2)
    release.set()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert mcp_service._persistent_pools == {}


# ---------------------------------------------------------------------------
# Fixture: isolate MCP_CONFIG_FILE
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_mcp_config(tmp_path, monkeypatch):
    """Point MCP_CONFIG_FILE at a writable temp path for every test."""
    config_path = tmp_path / "mcp.json"
    monkeypatch.setattr("mcp_service.MCP_CONFIG_FILE", config_path)
    monkeypatch.setattr("mcp_service._config_cache", None)
    monkeypatch.setattr("mcp_service._config_cache_at", 0.0)
    monkeypatch.setattr("mcp_service._config_cache_path", None)
    return config_path


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------

class TestLoadConfig:

    def test_returns_empty_servers_when_file_absent(self):
        result = mcp_service.load_config()
        assert result == {"mcpServers": {}}

    def test_returns_config_dict_when_file_exists(self, isolated_mcp_config):
        config = {"mcpServers": {"fs": {"command": "npx", "args": ["-y", "@fs"]}}}
        isolated_mcp_config.write_text(json.dumps(config))
        assert mcp_service.load_config() == config

    def test_returns_empty_on_corrupt_json(self, isolated_mcp_config):
        isolated_mcp_config.write_text("not valid json {{{")
        assert mcp_service.load_config() == {"mcpServers": {}}

    def test_returns_empty_when_file_is_a_list(self, isolated_mcp_config):
        isolated_mcp_config.write_text(json.dumps(["not", "a", "dict"]))
        assert mcp_service.load_config() == {"mcpServers": {}}

    def test_returns_empty_when_mcp_servers_is_list(self, isolated_mcp_config):
        isolated_mcp_config.write_text(json.dumps({"mcpServers": ["bad"]}))
        assert mcp_service.load_config() == {"mcpServers": {}}

    def test_returns_empty_when_file_is_empty(self, isolated_mcp_config):
        isolated_mcp_config.write_text("")
        assert mcp_service.load_config() == {"mcpServers": {}}

    def test_preserves_extra_top_level_keys(self, isolated_mcp_config):
        config = {"mcpServers": {}, "globalEnv": {"FOO": "bar"}}
        isolated_mcp_config.write_text(json.dumps(config))
        result = mcp_service.load_config()
        assert result.get("globalEnv") == {"FOO": "bar"}

    def test_effective_config_has_no_automatic_servers(self):
        assert mcp_service.load_config() == {"mcpServers": {}}

    def test_effective_config_is_independent_snapshot(self, isolated_mcp_config):
        config = {"mcpServers": {"python_tools": {"command": "python", "args": ["server.py"]}}}
        isolated_mcp_config.write_text(json.dumps(config))
        loaded = mcp_service.load_config()
        loaded["mcpServers"]["python_tools"]["args"].append("--changed")
        assert mcp_service.load_config() == config


def test_tool_input_schema_supports_mcp_2_field_name():
    from types import SimpleNamespace

    schema = {"type": "object", "properties": {"path": {"type": "string"}}}
    assert mcp_service._tool_input_schema(SimpleNamespace(input_schema=schema)) == schema


def test_tool_input_schema_supports_camel_case_field_name():
    from types import SimpleNamespace

    schema = {"type": "object", "properties": {"command": {"type": "string"}}}
    assert mcp_service._tool_input_schema(SimpleNamespace(inputSchema=schema)) == schema


# ---------------------------------------------------------------------------
# save_config
# ---------------------------------------------------------------------------

class TestSaveConfig:

    def test_saves_valid_config(self, isolated_mcp_config):
        config = {"mcpServers": {"srv": {"command": "node", "args": []}}}
        mcp_service.save_config(config)
        assert isolated_mcp_config.exists()
        assert json.loads(isolated_mcp_config.read_text()) == config

    def test_adds_default_mcp_servers_key_if_absent(self, isolated_mcp_config):
        mcp_service.save_config({})
        saved = json.loads(isolated_mcp_config.read_text())
        assert "mcpServers" in saved

    def test_raises_value_error_for_non_dict(self):
        with pytest.raises(ValueError, match="JSON object"):
            mcp_service.save_config(["not", "a", "dict"])

    def test_raises_value_error_when_mcp_servers_not_dict(self):
        with pytest.raises(ValueError, match="mcpServers"):
            mcp_service.save_config({"mcpServers": "wrong-type"})

    def test_raises_value_error_when_mcp_servers_is_list(self):
        with pytest.raises(ValueError, match="mcpServers"):
            mcp_service.save_config({"mcpServers": [1, 2, 3]})

    def test_server_names_are_user_configurable(self, isolated_mcp_config):
        config = {"mcpServers": {"agent_tools": {"command": "python", "args": ["server.py"]}}}
        mcp_service.save_config(config)
        assert mcp_service.load_config() == config
        assert json.loads(isolated_mcp_config.read_text()) == config

    def test_write_is_atomic_no_tmp_files_left(self, isolated_mcp_config):
        mcp_service.save_config({"mcpServers": {}})
        residual = list(isolated_mcp_config.parent.glob("*.tmp-*"))
        assert residual == []

    def test_save_then_load_roundtrip(self, isolated_mcp_config):
        config = {"mcpServers": {"bash": {"command": "bash", "args": ["-c"]}}}
        mcp_service.save_config(config)
        assert mcp_service.load_config() == config

    def test_overwrites_existing_file(self, isolated_mcp_config):
        mcp_service.save_config({"mcpServers": {"old": {}}})
        mcp_service.save_config({"mcpServers": {"new": {}}})
        saved = json.loads(isolated_mcp_config.read_text())
        assert "new" in saved["mcpServers"]
        assert "old" not in saved["mcpServers"]


# ---------------------------------------------------------------------------
# find_server
# ---------------------------------------------------------------------------

class TestFindServer:

    def test_finds_existing_server(self, isolated_mcp_config):
        config = {"mcpServers": {
            "bash": {"command": "bash", "args": ["-c"]},
            "fs":   {"command": "npx",  "args": ["-y", "@fs"]},
        }}
        isolated_mcp_config.write_text(json.dumps(config))
        result = mcp_service.find_server("bash")
        assert result == {"command": "bash", "args": ["-c"]}

    def test_returns_none_for_missing_server(self, isolated_mcp_config):
        isolated_mcp_config.write_text(json.dumps({"mcpServers": {}}))
        assert mcp_service.find_server("nonexistent") is None

    def test_returns_none_when_config_file_absent(self):
        assert mcp_service.find_server("anything") is None

    def test_returns_full_server_config(self, isolated_mcp_config):
        server_cfg = {
            "command": "node",
            "args": ["/path/to/custom-server/index.js"],
            "env": {"SOME_VAR": "some_val"},
        }
        isolated_mcp_config.write_text(json.dumps({"mcpServers": {"custom_tools": server_cfg}}))
        result = mcp_service.find_server("custom_tools")
        assert result == server_cfg

    def test_loads_user_server_without_rewriting_disk(self, isolated_mcp_config):
        server = {"command": "python", "args": ["/workspace/server.py"]}
        disk_config = {"mcpServers": {"agent_tools": server}}
        isolated_mcp_config.write_text(json.dumps(disk_config))

        assert mcp_service.find_server("agent_tools") == server
        assert mcp_service.load_config() == disk_config
        assert json.loads(isolated_mcp_config.read_text()) == disk_config


# ---------------------------------------------------------------------------
# run_async (sync bridge)
# ---------------------------------------------------------------------------

class TestRunAsync:

    def test_runs_simple_coroutine(self):
        import asyncio

        async def _coro():
            return 42

        assert mcp_service.run_async(_coro()) == 42

    def test_coroutine_exception_propagates(self):
        import asyncio

        async def _failing():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            mcp_service.run_async(_failing())


# ---------------------------------------------------------------------------
# config cache
# ---------------------------------------------------------------------------

class TestConfigCache:

    def test_load_config_uses_short_lived_cache(self, isolated_mcp_config, monkeypatch):
        first = {"mcpServers": {"one": {"command": "a"}}}
        second = {"mcpServers": {"two": {"command": "b"}}}
        isolated_mcp_config.write_text(json.dumps(first))
        monkeypatch.setattr("mcp_service._CONFIG_TTL_SECONDS", 60)

        assert mcp_service.load_config() == first
        isolated_mcp_config.write_text(json.dumps(second))
        assert mcp_service.load_config() == first
        assert mcp_service.load_config(refresh=True) == second


# ---------------------------------------------------------------------------
# McpSessionPool
# ---------------------------------------------------------------------------

class TestMcpSessionPool:

    def _install_fake_mcp(self, monkeypatch):
        import sys
        import types
        from types import SimpleNamespace

        created_stdio = []
        created_sessions = []

        class FakeStdioContext:
            def __init__(self, params):
                self.params = params
                self.entered = 0
                self.exited = 0
                created_stdio.append(self)

            async def __aenter__(self):
                self.entered += 1
                self.enter_task = asyncio.current_task()
                return "reader", "writer"

            async def __aexit__(self, exc_type, exc, tb):
                assert asyncio.current_task() is self.enter_task
                self.exited += 1

        class FakeClientSession:
            def __init__(self, reader, writer, read_timeout_seconds=None):
                self.reader = reader
                self.writer = writer
                self.read_timeout_seconds = read_timeout_seconds
                self.entered = 0
                self.exited = 0
                self.initialized = 0
                self.calls = []
                created_sessions.append(self)

            async def __aenter__(self):
                self.entered += 1
                self.enter_task = asyncio.current_task()
                return self

            async def __aexit__(self, exc_type, exc, tb):
                assert asyncio.current_task() is self.enter_task
                self.exited += 1

            async def initialize(self):
                # The SDK calls total_seconds() when sending initialize.
                self.read_timeout_seconds.total_seconds()
                self.initialized += 1

            async def list_tools(self):
                return SimpleNamespace(tools=[SimpleNamespace(
                    name="tool", description="A test tool", inputSchema={"type": "object"},
                )])

            async def call_tool(self, tool_name, arguments):
                self.calls.append((tool_name, arguments))
                return SimpleNamespace(content=[SimpleNamespace(text=f"{tool_name}:{arguments.get('n')}")])

        def fake_stdio_client(params):
            return FakeStdioContext(params)

        mcp_module = types.ModuleType("mcp")
        mcp_module.ClientSession = FakeClientSession
        mcp_module.StdioServerParameters = lambda **kwargs: kwargs
        mcp_client_module = types.ModuleType("mcp.client")
        mcp_stdio_module = types.ModuleType("mcp.client.stdio")
        mcp_stdio_module.stdio_client = fake_stdio_client

        monkeypatch.setitem(sys.modules, "mcp", mcp_module)
        monkeypatch.setitem(sys.modules, "mcp.client", mcp_client_module)
        monkeypatch.setitem(sys.modules, "mcp.client.stdio", mcp_stdio_module)
        monkeypatch.setattr(mcp_service, "_build_server_params", lambda name, cfg, conv_id="": {"server": name, "conv_id": conv_id})
        return created_stdio, created_sessions

    def test_reuses_one_session_for_repeated_calls_to_same_server(self, monkeypatch):
        created_stdio, created_sessions = self._install_fake_mcp(monkeypatch)

        pool = mcp_service.McpSessionPool(conv_id="conv-1")
        try:
            assert pool.invoke_tool("srv", {}, "tool", {"n": 1}) == "tool:1"
            assert pool.invoke_tool("srv", {}, "tool", {"n": 2}) == "tool:2"
        finally:
            pool.close()

        assert len(created_stdio) == 1
        assert len(created_sessions) == 1
        assert created_sessions[0].initialized == 1
        assert created_sessions[0].calls == [("tool", {"n": 1}), ("tool", {"n": 2})]
        assert created_sessions[0].exited == 1
        assert created_stdio[0].exited == 1

    def test_uses_separate_sessions_for_different_servers(self, monkeypatch):
        created_stdio, created_sessions = self._install_fake_mcp(monkeypatch)

        with mcp_service.McpSessionPool(conv_id="conv-1") as pool:
            assert pool.invoke_tool("one", {}, "tool", {"n": 1}) == "tool:1"
            assert pool.invoke_tool("two", {}, "tool", {"n": 2}) == "tool:2"

        assert len(created_stdio) == 2
        assert len(created_sessions) == 2
        assert all(session.initialized == 1 for session in created_sessions)
        assert all(session.exited == 1 for session in created_sessions)
        assert all(stdio.exited == 1 for stdio in created_stdio)

    def test_closed_pool_rejects_new_invocations(self, monkeypatch):
        self._install_fake_mcp(monkeypatch)
        pool = mcp_service.McpSessionPool(conv_id="conv-1")
        pool.close()

        with pytest.raises(RuntimeError, match="closed"):
            pool.invoke_tool("srv", {}, "tool", {})

    def test_lock_not_held_while_waiting_for_result(self, monkeypatch):
        """
        Regression: invoke_tool() must release self._lock before blocking on
        future.result().  If the lock were held across the wait, any code that
        acquires self._lock while a call is in-flight (e.g. a concurrent
        close()) would deadlock.

        This test proves the lock is free during the wait by acquiring it from
        a second thread while the pool is processing a (slightly delayed) tool
        call.  Under the old code the second thread would hang forever; under
        the fix it succeeds immediately.
        """
        import threading

        created_stdio, created_sessions = self._install_fake_mcp(monkeypatch)

        # Patch _build_server_params to return something the fake accepts.
        delay_event = threading.Event()
        original_invoke = mcp_service.McpSessionPool._invoke

        async def slow_invoke(self_pool, server_name, server_config, tool_name, arguments):
            # Briefly yield so the main thread has time to try acquiring the lock.
            import asyncio
            await asyncio.sleep(0.05)
            return await original_invoke(self_pool, server_name, server_config, tool_name, arguments)

        monkeypatch.setattr(mcp_service.McpSessionPool, "_invoke", slow_invoke)

        pool = mcp_service.McpSessionPool(conv_id="conv-1")
        pool.start()

        lock_acquired_while_in_flight = threading.Event()
        lock_acquire_error = []

        def try_acquire_lock():
            # Give invoke_tool() a moment to have submitted the job and be
            # blocking on future.result().
            import time
            time.sleep(0.02)
            acquired = pool._lock.acquire(timeout=1.0)
            if acquired:
                pool._lock.release()
                lock_acquired_while_in_flight.set()
            else:
                lock_acquire_error.append("timed out acquiring lock — possible deadlock")

        probe = threading.Thread(target=try_acquire_lock, daemon=True)
        probe.start()

        result = pool.invoke_tool("srv", {}, "tool", {"n": 1})
        probe.join(timeout=2.0)
        pool.close()

        assert result == "tool:1"
        assert not lock_acquire_error, lock_acquire_error[0]
        assert lock_acquired_while_in_flight.is_set(), (
            "Probe thread could not acquire lock while invoke_tool() was in flight — "
            "lock was still held across future.result() (deadlock regression)"
        )

    def test_session_receives_configured_tool_timeout(self, monkeypatch):
        _, created_sessions = self._install_fake_mcp(monkeypatch)
        pool = mcp_service.McpSessionPool(conv_id="conv-1", tool_timeout=2.5)
        try:
            assert pool.invoke_tool("srv", {}, "tool", {"n": 1}) == "tool:1"
        finally:
            pool.close()

        assert created_sessions[0].read_timeout_seconds == timedelta(seconds=2.5)

    @pytest.mark.parametrize("operation", ["fetch_tools", "invoke_tool"])
    @pytest.mark.parametrize("configured_timeout, expected_seconds", [
        (None, 120), ("2.5", 2.5), ("invalid", 120), ("0", 120), ("-1", 120),
    ])
    def test_one_shot_sessions_use_sdk_timeout_type(
        self, monkeypatch, operation, configured_timeout, expected_seconds,
    ):
        created_stdio, created_sessions = self._install_fake_mcp(monkeypatch)
        if configured_timeout is None:
            monkeypatch.delenv("LUMEN_MCP_TOOL_TIMEOUT", raising=False)
        else:
            monkeypatch.setenv("LUMEN_MCP_TOOL_TIMEOUT", configured_timeout)

        if operation == "fetch_tools":
            result = mcp_service.run_async(mcp_service.fetch_tools("srv", {}))
            assert result == [{
                "server": "srv", "name": "tool", "description": "A test tool",
                "inputSchema": {"type": "object"},
            }]
        else:
            result = mcp_service.run_async(
                mcp_service.invoke_tool("srv", {}, "tool", {"n": 1})
            )
            assert result == "tool:1"

        assert created_sessions[0].read_timeout_seconds == timedelta(seconds=expected_seconds)
        assert created_sessions[0].initialized == 1
        assert created_sessions[0].exited == 1
        assert created_stdio[0].exited == 1

    def test_sdk_timeout_is_reported_without_stale_session_retry(self, monkeypatch):
        attempts = {"count": 0}

        class TimeoutSession:
            async def call_tool(self, tool_name, arguments):
                attempts["count"] += 1
                raise RuntimeError("Timed out while waiting for response")

        async def get_timeout_session(self_pool, server_name, server_config):
            return TimeoutSession()

        monkeypatch.setattr(mcp_service.McpSessionPool, "_get_session", get_timeout_session)
        pool = mcp_service.McpSessionPool(conv_id="conv-1", tool_timeout=0.02)
        try:
            with pytest.raises(TimeoutError, match="timed out"):
                pool.invoke_tool("srv", {}, "stuck_tool", {})
        finally:
            pool.close()

        assert attempts["count"] == 1

    def test_failed_call_is_not_replayed_and_next_call_reopens_session(self, monkeypatch):
        import sys
        import types
        from types import SimpleNamespace

        attempt = {"count": 0}

        class FlakySession:
            def __init__(self):
                self.entered = 0
                self.exited = 0
                self.initialized = 0
                self.enter_task = None

            async def __aenter__(self):
                self.entered += 1
                self.enter_task = asyncio.current_task()
                return self

            async def __aexit__(self, *a):
                assert asyncio.current_task() is self.enter_task
                self.exited += 1

            async def initialize(self):
                self.initialized += 1

            async def call_tool(self, tool_name, arguments):
                attempt["count"] += 1
                if attempt["count"] == 1:
                    raise OSError("broken pipe")
                return SimpleNamespace(content=[SimpleNamespace(text="ok")])

        sessions_created = []

        class FakeStdioCtx:
            def __init__(self, params):
                self.entered = 0
                self.exited = 0
                self.enter_task = None

            async def __aenter__(self):
                self.entered += 1
                self.enter_task = asyncio.current_task()
                return "reader", "writer"

            async def __aexit__(self, *a):
                assert asyncio.current_task() is self.enter_task
                self.exited += 1

        def fake_stdio_client(params):
            return FakeStdioCtx(params)

        def fake_client_session(reader, writer, **kwargs):
            s = FlakySession()
            sessions_created.append(s)
            return s

        mcp_module = types.ModuleType("mcp")
        mcp_module.ClientSession = fake_client_session
        mcp_module.StdioServerParameters = lambda **kwargs: kwargs
        mcp_client_module = types.ModuleType("mcp.client")
        mcp_stdio_module = types.ModuleType("mcp.client.stdio")
        mcp_stdio_module.stdio_client = fake_stdio_client

        monkeypatch.setitem(sys.modules, "mcp", mcp_module)
        monkeypatch.setitem(sys.modules, "mcp.client", mcp_client_module)
        monkeypatch.setitem(sys.modules, "mcp.client.stdio", mcp_stdio_module)
        monkeypatch.setattr(mcp_service, "_build_server_params", lambda name, cfg, conv_id="": {})

        with mcp_service.McpSessionPool(conv_id="conv-1") as pool:
            with pytest.raises(OSError, match="broken pipe"):
                pool.invoke_tool("srv", {}, "tool", {})
            assert attempt["count"] == 1
            result = pool.invoke_tool("srv", {}, "tool", {})

        assert result == "ok"
        assert attempt["count"] == 2
        assert len(sessions_created) == 2, "expected a fresh session to be opened after the stale one"


# ---------------------------------------------------------------------------
# Persistent cross-turn pool registry
# ---------------------------------------------------------------------------

class TestPersistentPool:

    def _install_fake_mcp(self, monkeypatch):
        """Same minimal fake as TestMcpSessionPool._install_fake_mcp."""
        import sys
        import types
        from types import SimpleNamespace

        class FakeStdioCtx:
            async def __aenter__(self): return "r", "w"
            async def __aexit__(self, *a): pass

        class FakeSession:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def initialize(self): pass
            async def call_tool(self, name, args):
                return SimpleNamespace(content=[SimpleNamespace(text="result")])

        mcp_module = types.ModuleType("mcp")
        mcp_module.ClientSession = lambda r, w, **kwargs: FakeSession()
        mcp_module.StdioServerParameters = lambda **kwargs: kwargs
        mcp_client_module = types.ModuleType("mcp.client")
        mcp_stdio_module = types.ModuleType("mcp.client.stdio")
        mcp_stdio_module.stdio_client = lambda params: FakeStdioCtx()

        monkeypatch.setitem(sys.modules, "mcp", mcp_module)
        monkeypatch.setitem(sys.modules, "mcp.client", mcp_client_module)
        monkeypatch.setitem(sys.modules, "mcp.client.stdio", mcp_stdio_module)
        monkeypatch.setattr(mcp_service, "_build_server_params", lambda name, cfg, conv_id="": {})

    def test_get_persistent_pool_returns_same_instance_for_same_conv(self, monkeypatch):
        self._install_fake_mcp(monkeypatch)
        pool_a = mcp_service.get_persistent_pool("conv-1")
        pool_b = mcp_service.get_persistent_pool("conv-1")
        assert pool_a is pool_b
        pool_a.close()

    def test_get_persistent_pool_returns_different_instances_for_different_convs(self, monkeypatch):
        self._install_fake_mcp(monkeypatch)
        pool_a = mcp_service.get_persistent_pool("conv-1")
        pool_b = mcp_service.get_persistent_pool("conv-2")
        assert pool_a is not pool_b
        pool_a.close()
        pool_b.close()

    def test_close_persistent_pool_removes_entry(self, monkeypatch):
        self._install_fake_mcp(monkeypatch)
        mcp_service.get_persistent_pool("conv-1")
        mcp_service.close_persistent_pool("conv-1")
        assert "conv-1" not in mcp_service._persistent_pools

    def test_close_persistent_pool_is_idempotent_for_unknown_conv(self):
        # Must not raise even if no pool exists for the conv_id.
        mcp_service.close_persistent_pool("never-existed")

    def test_get_persistent_pool_creates_fresh_pool_after_close(self, monkeypatch):
        self._install_fake_mcp(monkeypatch)
        pool_a = mcp_service.get_persistent_pool("conv-1")
        mcp_service.close_persistent_pool("conv-1")
        pool_b = mcp_service.get_persistent_pool("conv-1")
        assert pool_a is not pool_b
        pool_b.close()

    def test_get_persistent_pool_replaces_closed_pool_automatically(self, monkeypatch):
        """If a pool was closed externally, get_persistent_pool creates a fresh one."""
        self._install_fake_mcp(monkeypatch)
        pool_a = mcp_service.get_persistent_pool("conv-1")
        # Manually mark it closed without going through close_persistent_pool.
        pool_a._closed = True
        pool_b = mcp_service.get_persistent_pool("conv-1")
        assert pool_b is not pool_a
        assert not pool_b._closed
        pool_b.close()

    def test_close_all_persistent_pools_clears_registry(self, monkeypatch):
        self._install_fake_mcp(monkeypatch)
        mcp_service.get_persistent_pool("conv-1")
        mcp_service.get_persistent_pool("conv-2")
        mcp_service.close_all_persistent_pools()
        assert mcp_service._persistent_pools == {}

    def test_pool_survives_across_simulated_turns(self, monkeypatch):
        """
        Simulate two consecutive turns for the same conversation.
        The same pool instance must be returned both times, and sessions
        inside must be reused (opened only once, not twice).
        """
        self._install_fake_mcp(monkeypatch)

        open_counts = {"srv": 0}
        original_open = mcp_service.McpSessionPool._open_session

        async def counting_open(self_pool, server_name, server_config):
            open_counts[server_name] = open_counts.get(server_name, 0) + 1
            return await original_open(self_pool, server_name, server_config)

        monkeypatch.setattr(mcp_service.McpSessionPool, "_open_session", counting_open)

        # Turn 1
        pool1 = mcp_service.get_persistent_pool("conv-1")
        pool1.invoke_tool("srv", {}, "tool", {})

        # Turn 2 — same pool, no close in between
        pool2 = mcp_service.get_persistent_pool("conv-1")
        pool2.invoke_tool("srv", {}, "tool", {})

        assert pool1 is pool2
        assert open_counts["srv"] == 1, (
            f"expected session opened exactly once across two turns, got {open_counts['srv']}"
        )
        mcp_service.close_persistent_pool("conv-1")


def test_failed_initialization_closes_partial_session(monkeypatch):
    import sys
    stdio, sessions = TestMcpSessionPool()._install_fake_mcp(monkeypatch)
    async def fail(self):
        raise RuntimeError('initialization failed')
    monkeypatch.setattr(sys.modules['mcp'].ClientSession, 'initialize', fail)
    with mcp_service.McpSessionPool(conv_id='chat') as pool:
        with pytest.raises(RuntimeError, match='initialization failed'):
            pool.invoke_tool('server', {}, 'tool', {})
        assert not pool._sessions
    assert stdio[0].exited == sessions[0].exited == 1
