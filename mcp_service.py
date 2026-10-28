"""MCP service layer — config persistence, tool discovery, tool invocation."""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from mcp_adapters import apply_workspace_process_options
from fs_utils import atomic_replace
from mcp_session_pool import McpSessionPool

_MCP_CONFIG_DIR = Path.home() / ".virtus"
_MCP_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
MCP_CONFIG_FILE = Path(os.getenv("VIRTUS_MCP_CONFIG_FILE", str(_MCP_CONFIG_DIR / "mcp.json")))
log = logging.getLogger(__name__)

_config_cache: dict | None = None
_config_cache_at = 0.0
_config_cache_path: Path | None = None
_config_cache_lock = threading.Lock()
_CONFIG_TTL_SECONDS = float(os.getenv("VIRTUS_MCP_CONFIG_CACHE_TTL", "5"))
_mcp_stderr_handle = None
_mcp_stderr_lock = threading.Lock()


def _mcp_stderr_sink():
    """Return a valid long-lived stderr handle for frozen GUI processes."""
    global _mcp_stderr_handle
    with _mcp_stderr_lock:
        if _mcp_stderr_handle is None or _mcp_stderr_handle.closed:
            _mcp_stderr_handle = open(os.devnull, "w", encoding="utf-8")
        return _mcp_stderr_handle


def _stdio_client(params):
    """Create MCP stdio without inheriting an absent PyInstaller stream."""
    from mcp.client.stdio import stdio_client

    if sys.stderr is not None:
        return stdio_client(params)
    return stdio_client(params, errlog=_mcp_stderr_sink())


def _mcp_timeout_seconds() -> float:
    try:
        value = float(os.getenv("VIRTUS_MCP_TOOL_TIMEOUT", "120"))
    except (TypeError, ValueError):
        value = 120.0
    return value if value > 0 else 120.0


def _tool_input_schema(tool: Any) -> dict:
    """Read a tool schema through either MCP model field spelling."""
    schema = getattr(tool, "inputSchema", None)
    if not schema:
        schema = getattr(tool, "input_schema", None)
    return schema if isinstance(schema, dict) else {}


# ── Config ────────────────────────────────────────────────────────────────────

def _write_config_file(config: dict) -> None:
    MCP_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = MCP_CONFIG_FILE.with_suffix(f".tmp-{uuid.uuid4().hex}")
    tmp_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    atomic_replace(tmp_path, MCP_CONFIG_FILE)


def load_config(*, refresh: bool = False) -> dict:
    """Load configured MCP servers, returning an independent snapshot."""
    global _config_cache, _config_cache_at, _config_cache_path
    now = time.monotonic()
    with _config_cache_lock:
        if (
            not refresh
            and _config_cache is not None
            and _config_cache_path == MCP_CONFIG_FILE
            and now - _config_cache_at < _CONFIG_TTL_SECONDS
        ):
            return copy.deepcopy(_config_cache)

        if not MCP_CONFIG_FILE.exists():
            config = {"mcpServers": {}}
        else:
            try:
                config = json.loads(MCP_CONFIG_FILE.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                log.warning("[mcp] could not read %s: %s", MCP_CONFIG_FILE, exc)
                config = {"mcpServers": {}}
        if not isinstance(config, dict) or not isinstance(config.get("mcpServers", {}), dict):
            config = {"mcpServers": {}}
        else:
            config.setdefault("mcpServers", {})
        _config_cache = config
        _config_cache_at = time.monotonic()
        _config_cache_path = MCP_CONFIG_FILE
        return copy.deepcopy(config)


def save_config(config: dict) -> None:
    global _config_cache, _config_cache_at, _config_cache_path
    if not isinstance(config, dict):
        raise ValueError("MCP config must be a JSON object")
    config.setdefault("mcpServers", {})
    if not isinstance(config["mcpServers"], dict):
        raise ValueError("mcpServers must be a JSON object")

    saved = copy.deepcopy(config)
    with _config_cache_lock:
        _write_config_file(saved)
        _config_cache = saved
        _config_cache_at = time.monotonic()
        _config_cache_path = MCP_CONFIG_FILE


def find_server(server_name: str) -> dict | None:
    return load_config().get("mcpServers", {}).get(server_name)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _build_server_params(
    server_name: str,
    server_config: dict,
    *,
    conv_id: str = "",
) -> Any:
    from mcp import StdioServerParameters  # optional dependency

    env = {}
    params = {
        "command": server_config.get("command", ""),
        "args": server_config.get("args", []),
        "env": env,
    }
    apply_workspace_process_options(
        params,
        env,
        server_name=server_name,
        server_config=server_config,
        conv_id=conv_id,
    )
    return StdioServerParameters(**params)


# ── Async operations ──────────────────────────────────────────────────────────

async def fetch_tools(server_name: str, server_config: dict, conv_id: str = "") -> list[dict]:
    """Connect to an MCP server and return its tool definitions."""
    from mcp import ClientSession

    params = _build_server_params(server_name, server_config, conv_id=conv_id)
    tools: list[dict] = []
    try:
        async with _stdio_client(params) as (reader, writer):
            async with ClientSession(
                reader,
                writer,
                read_timeout_seconds=_mcp_timeout_seconds(),
            ) as session:
                await session.initialize()
                for tool in (await session.list_tools()).tools:
                    tools.append({
                        "server":      server_name,
                        "name":        tool.name,
                        "description": tool.description or "",
                        "inputSchema": _tool_input_schema(tool),
                    })
    except Exception:
        log.exception("[mcp] failed to list tools from %r", server_name)
    return tools


async def invoke_tool(server_name: str, server_config: dict, tool_name: str, arguments: dict, *, conv_id: str = "") -> str:
    """Call a single MCP tool and return its text output."""
    from mcp import ClientSession

    params = _build_server_params(server_name, server_config, conv_id=conv_id)
    try:
        async with _stdio_client(params) as (reader, writer):
            async with ClientSession(
                reader,
                writer,
                read_timeout_seconds=_mcp_timeout_seconds(),
            ) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                text = "\n".join(
                    c.text if hasattr(c, "text") else str(c)
                    for c in result.content
                )
                return text
    except Exception as exc:
        return f"Error calling tool '{tool_name}': {exc}"


# ── Persistent cross-turn session pools ──────────────────────────────────────

_persistent_pools: dict[str, McpSessionPool] = {}
_persistent_pools_lock = threading.Lock()


def get_persistent_pool(conv_id: str) -> McpSessionPool:
    """Return the long-lived McpSessionPool for this conversation, creating it if needed."""
    with _persistent_pools_lock:
        pool = _persistent_pools.get(conv_id)
        if pool is None or pool._closed:
            pool = McpSessionPool(conv_id)
            pool.start()
            _persistent_pools[conv_id] = pool
        return pool


def close_persistent_pool(conv_id: str) -> None:
    """Close and discard the pool for a conversation.

    Call this whenever the conversation's container is stopped or recreated so
    the next tool call opens fresh sessions against the new container process.
    Safe to call even if no pool exists for the conversation.
    """
    with _persistent_pools_lock:
        pool = _persistent_pools.pop(conv_id, None)
    if pool is not None:
        try:
            pool.close()
        except Exception:
            log.exception("[mcp] error closing persistent pool for conv %s", conv_id)


def close_all_persistent_pools(*, deadline: float | None = None) -> None:
    """Close every persistent pool concurrently within one deadline."""
    with _persistent_pools_lock:
        items = list(_persistent_pools.items())
        _persistent_pools.clear()
    if not items:
        return

    def _close(conv_id: str, pool: McpSessionPool) -> None:
        try:
            timeout = None if deadline is None else max(0.01, deadline - time.monotonic())
            pool.close(timeout=timeout)
        except Exception:
            log.exception("[mcp] error closing persistent pool for conv %s on shutdown", conv_id)

    threads = [
        threading.Thread(
            target=_close,
            args=(conv_id, pool),
            name=f"mcp-close-{conv_id}",
            daemon=True,
        )
        for conv_id, pool in items
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        timeout = None if deadline is None else max(0.0, deadline - time.monotonic())
        thread.join(timeout=timeout)
    unfinished = [thread.name for thread in threads if thread.is_alive()]
    if unfinished:
        log.warning("[mcp] shutdown deadline reached with pools still closing: %s", unfinished)


# ── Sync bridge ───────────────────────────────────────────────────────────────

# Shared executor for bridging async MCP calls into sync Flask code.
_async_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="mcp-async")


def run_async(coro) -> Any:
    """Run an async coroutine from sync code without spawning a new thread unless one is needed."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    return _async_executor.submit(asyncio.run, coro).result()
