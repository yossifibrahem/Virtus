"""Long-running chat turn orchestration."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from collections.abc import Callable
from openai import OpenAI

import app_config
import container_service
import mcp_service
import store
import streaming as stream_module
import title_service
import memory_service
import tool_approval
import builtin_tools
import sandbox_runtime
import tool_media


def _positive_float_env(name: str, default: float) -> float:
    """Read a positive seconds value without making a bad env var fatal."""
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


MAX_TOOL_ARGUMENT_BYTES = 3 * 1024 * 1024

OPENAI_TIMEOUT_SECONDS = _positive_float_env("LUMEN_OPENAI_TIMEOUT", 120.0)


def _inject_memory(api_messages: list) -> list:
    """Prepend or extend the system message with memory contents if any exist."""
    memory = memory_service.read()
    if not memory:
        return api_messages

    block = f"## Persistent Memory\n{memory}"

    messages = list(api_messages)
    if messages and messages[0].get("role") == "system":
        messages[0] = {
            **messages[0],
            "content": messages[0]["content"] + "\n\n" + block,
        }
    else:
        messages.insert(0, {"role": "system", "content": block})
    return messages

Publish = Callable[[dict], None]

# Keep approval resolution on the service API used by the chat routes.
resolve_tool_approval = tool_approval.resolve_tool_approval


def openai_client(body: dict | None = None) -> OpenAI:
    cfg = app_config.load_config()
    return OpenAI(
        api_key=cfg.get("api_key") or "sk-placeholder",
        base_url=cfg.get("api_base") or app_config.DEFAULT_API_BASE,
        timeout=OPENAI_TIMEOUT_SECONDS,
    )


def _parse_stream_payload(raw_event) -> dict | None:
    """Normalize a streaming event.

    streaming.stream_chat_completion yields typed dicts internally; SSE
    serialization is only performed at the HTTP boundary.
    """
    return raw_event if isinstance(raw_event, dict) else None


def _save_turn(conv_id: str, title: str, messages: list, display_log: list, *, stream_id: str = "") -> None:
    if not conv_id:
        return
    data = store.load(conv_id) or {"id": conv_id, "created_at": ""}
    data.update({
        "title": title or data.get("title") or "Untitled",
        "messages": messages,
        "displayLog": display_log,
        "streaming": bool(stream_id),
        "working_directory": str(container_service.conversation_workspace(store.runtime_id(conv_id, data))),
    })
    if stream_id:
        data["active_stream_id"] = stream_id
    else:
        data.pop("active_stream_id", None)
    store.save(conv_id, data)


def _log_with_partial(base_log: list, reasoning: str, text: str) -> list:
    return [
        *base_log,
        *([ {"type": "thinking", "content": reasoning}] if reasoning else []),
        *([ {"type": "message", "role": "assistant", "content": text}] if text else []),
    ]


def _tool_meta_by_name(body: dict) -> dict:
    return {tool.get("name"): tool for tool in body.get("mcp_tool_meta", []) if tool.get("name")}



def _tool_call_message(calls: list, content: str | None) -> dict:
    return {
        "role": "assistant",
        "content": content or None,
        "tool_calls": [
            {
                "id": call.get("id"),
                "type": "function",
                "function": {
                    "name": call.get("function", {}).get("name", ""),
                    "arguments": call.get("function", {}).get("arguments", "{}"),
                },
            }
            for call in calls
        ],
    }


def _safe_tool_args(raw_args: str) -> dict:
    """Reject invalid arguments rather than executing a tool with guessed defaults."""
    if not isinstance(raw_args, str) or len(raw_args.encode()) > MAX_TOOL_ARGUMENT_BYTES:
        raise ValueError("Tool arguments must be JSON text of at most 3 MiB")
    parsed = json.loads(raw_args or "{}")
    if not isinstance(parsed, dict):
        raise ValueError("Tool arguments must be a JSON object")
    return parsed


def _run_mcp_call(tool_meta: dict, name: str, args: dict, session_pool) -> str:
    server_name = tool_meta.get("server", "")
    server_config = mcp_service.find_server(server_name)
    if not server_config:
        raise ValueError(f"MCP server '{server_name}' not found")
    return session_pool.invoke_tool(server_name, server_config, name, args)


class TurnRecorder:
    """Throttled persistence for a single assistant turn."""

    def __init__(self, conv_id: str, title: str, messages: list, stream_id: str) -> None:
        self.conv_id = conv_id
        self.title = title
        self.messages = messages
        self.stream_id = stream_id
        self.last_saved_at = 0.0

    def update_title(self, title: str) -> None:
        self.title = title

    def save(self, display_log: list, reasoning: str = "", text: str = "", *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self.last_saved_at < 0.75:
            return
        self.last_saved_at = now
        _save_turn(
            self.conv_id,
            self.title,
            self.messages,
            _log_with_partial(display_log, reasoning, text),
            stream_id=self.stream_id,
        )

    def finalize(self, display_log: list) -> None:
        _save_turn(self.conv_id, self.title, self.messages, display_log)


def run_persistent_chat_turn(body: dict, cancel_event: threading.Event, stream_id: str, publish: Publish) -> None:
    conv_id = body.get("conv_id", "")
    conversation = store.load(conv_id) if isinstance(conv_id, str) and re.fullmatch(r"[a-fA-F0-9-]{36}", conv_id) else None
    if not conversation:
        publish({"type": "error", "message": "A valid conversation is required"})
        return
    runtime_id = store.runtime_id(conv_id, conversation)
    title = body.get("title") or "Untitled"
    turn_messages = list(body.get("conversation_messages") or [])
    display_log = list(body.get("display_log") or [])
    api_messages = list(body.get("messages") or [])
    tool_meta = _tool_meta_by_name(body)
    if builtin_tools.NAMES.intersection(tool_meta):
        publish({"type": "error", "message": "MCP tool names conflict with reserved built-in tools: bash, view, write, edit"})
        return
    tools = [t for t in body.get("tools", []) if t.get("function", {}).get("name") not in builtin_tools.NAMES]
    tools.extend(builtin_tools.SCHEMAS)
    is_first_message = sum(m.get("role") == "user" for m in turn_messages) == 1
    api_messages = _inject_memory(api_messages)
    recorder = TurnRecorder(conv_id, title, turn_messages, stream_id)
    assistant_completed = False
    session_pool: mcp_service.McpSessionPool | None = None
    client = None
    acc_text = acc_reasoning = ""

    def finalize_partial_answer(reasoning: str, text: str) -> bool:
        nonlocal assistant_completed
        if assistant_completed:
            return False
        if reasoning:
            display_log.append({"type": "thinking", "content": reasoning})
        if text:
            display_log.append({"type": "message", "role": "assistant", "content": text})
            turn_messages.append({"role": "assistant", "content": text})
        assistant_completed = bool(reasoning or text)
        return assistant_completed

    recorder.save(display_log, force=True)

    try:
        client = openai_client(body)
        if conv_id and tool_meta:
            session_pool = mcp_service.get_persistent_pool(runtime_id)

        while not cancel_event.is_set():
            acc_text = ""
            acc_reasoning = ""
            tool_calls = []

            for raw in stream_module.stream_chat_completion(
                client,
                model=body.get("model", "gpt-4o"),
                messages=tool_media.expand(api_messages, conv_id),
                tools=tools,
                cancel_event=cancel_event,
                temperature=float(body.get("temperature", 0.7)),
                timeout=OPENAI_TIMEOUT_SECONDS,
            ):
                event = _parse_stream_payload(raw)
                if not event:
                    continue
                etype = event.get("type")
                if etype == "error":
                    raise RuntimeError(event.get("message", "Model request failed"))
                if etype == "done":
                    break
                if etype == "tool_calls":
                    tool_calls = event.get("calls") or []
                    continue

                if etype == "reasoning":
                    acc_reasoning += event.get("content", "")
                    recorder.save(display_log, acc_reasoning, acc_text)
                elif etype == "text":
                    acc_text += event.get("content", "")
                    recorder.save(display_log, acc_reasoning, acc_text)

                publish(event)

            if cancel_event.is_set():
                if finalize_partial_answer(acc_reasoning, acc_text):
                    recorder.save(display_log, force=True)
                break

            if tool_calls:
                if acc_reasoning:
                    display_log.append({"type": "thinking", "content": acc_reasoning})
                if acc_text:
                    display_log.append({"type": "message", "role": "assistant", "content": acc_text})
                tool_message = _tool_call_message(tool_calls, acc_text or None)
                turn_messages.append(tool_message)
                api_messages.append(tool_message)

                for call in tool_calls:
                    name = call.get("function", {}).get("name", "")
                    meta = tool_meta.get(name, {})
                    call_id = call.get("id", "")
                    args = {}
                    refs = []
                    denied = False
                    try:
                        args = _safe_tool_args(call.get("function", {}).get("arguments", "{}"))
                        if cancel_event.is_set():
                            raise ValueError("Tool call cancelled")
                        if name not in builtin_tools.NAMES and meta and not meta.get("autoApprove", False):
                            denied = not tool_approval.request_tool_approval(
                                stream_id, call_id, name, args, publish, cancel_event)
                        if cancel_event.is_set():
                            raise ValueError("Tool call cancelled")
                        if denied:
                            result = "Tool call denied by user."
                        else:
                            publish({"type": "tool_running", "call_id": call_id, "name": name, "args": args})
                            if name in builtin_tools.NAMES:
                                native_result = builtin_tools.invoke(runtime_id, name, args, cancel_event)
                                result = native_result["text"]
                                refs = tool_media.save(conv_id, native_result.get("images", []))
                            elif meta and session_pool:
                                with sandbox_runtime.runtime_operation(runtime_id, cancel_event):
                                    result = _run_mcp_call(meta, name, args, session_pool)
                            else:
                                raise ValueError(f"Unknown tool {name}")
                    except Exception as exc:
                        result = f"Error: {str(exc)[:1000]}"
                    event = {
                        "type": "tool_result",
                        "call_id": call_id,
                        "name": name,
                        "args": args,
                        "result": result,
                        "media": [f"/api/conversations/{conv_id}/tool-media/{ref}" for ref in refs],
                    }
                    if denied:
                        event["denied"] = True
                    display_log.append(event)
                    response = {"role": "tool", "tool_call_id": call_id, "content": result}
                    if refs:
                        response["_tool_media"] = refs
                    turn_messages.append(response)
                    api_messages.append(response)
                    recorder.save(display_log, force=True)
                    publish(event)
                acc_text = acc_reasoning = ""
                continue

            finalize_partial_answer(acc_reasoning, acc_text)
            break

        if assistant_completed and is_first_message and not cancel_event.is_set() \
                and body.get("auto_generate_titles", True):
            recorder.save(display_log, force=True)
            generated_title = title_service.generate_title(client, body, turn_messages)
            if generated_title:
                recorder.update_title(generated_title)
                publish({"type": "title", "title": generated_title})

    except Exception as exc:
        finalize_partial_answer(acc_reasoning, acc_text)
        display_log.append({"type": "status", "content": f"Error: {exc}"})
        publish({"type": "error", "message": str(exc)})
    finally:
        try:
            if cancel_event.is_set():
                display_log.append({"type": "status", "content": "Response stopped."})
            recorder.finalize(display_log)
            # All outcomes send authoritative history before the reader may save branches.
            publish({"type": "assistant_done", "messages": turn_messages, "displayLog": display_log})
        finally:
            if callable(close := getattr(client, "close", None)):
                close()
