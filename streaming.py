"""
SSE streaming — event formatting and OpenAI stream consumption.

`stream_chat_completion` is a pure generator; it knows nothing about
Flask.  `make_streaming_response` wraps it into a Flask Response.
"""
from __future__ import annotations

import json
from io import StringIO
import threading
from typing import Any, Generator

from flask import Response, stream_with_context
from openai import OpenAI


def sse_event(payload: dict) -> str:
    """Encode a dict as a Server-Sent Event data line."""
    return f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"


def _merge_tool_call_chunk(store: dict[int, dict], chunk) -> None:
    """Accumulate a streaming tool-call delta into *store* (keyed by chunk index)."""
    idx = chunk.index
    if idx not in store:
        if len(store) >= 128:
            raise ValueError('Model tool-call batch exceeds 128 calls')
        store[idx] = {"id": chunk.id or "", "function": {"name": "", "arguments": StringIO()}}
    if chunk.id:
        store[idx]["id"] = chunk.id
    if chunk.function:
        store[idx]["function"]["name"]      += chunk.function.name      or ""
        store[idx]["function"]["arguments"].write(chunk.function.arguments or "")


def stream_chat_completion(
    client: OpenAI,
    model: str,
    messages: list[dict],
    tools: list[dict],
    cancel_event: threading.Event,
    temperature: float = 0.7,
    timeout: float | None = None,
) -> Generator[dict, None, None]:
    """Yield typed event dictionaries for a streaming OpenAI chat completion.

    SSE serialization belongs at the HTTP boundary. Keeping this generator
    dict-based avoids an internal encode/decode round-trip and makes tests
    simpler. The cancel_event is checked before every yielded chunk.
    """
    openai_stream = None
    finished = threading.Event()
    def interrupt_read():
        while not finished.wait(.1):
            if cancel_event.is_set():
                if openai_stream is not None:
                    try:
                        openai_stream.close()
                    except Exception:
                        pass
                return
    monitor = threading.Thread(target=interrupt_read, daemon=True)
    monitor.start()
    try:
        request_kwargs: dict[str, Any] = {
            "model": model, "messages": messages, "stream": True,
            "temperature": temperature,
        }
        if timeout:
            request_kwargs["timeout"] = timeout
        if tools:
            request_kwargs["tools"] = tools
            request_kwargs["tool_choice"] = "auto"

        accumulated_tool_calls: dict[int, dict] = {}
        announced_tool_indices: set[int] = set()
        openai_stream = client.chat.completions.create(**request_kwargs)

        response_bytes = 0
        for chunk in openai_stream:
            if cancel_event.is_set():
                break

            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue

            response_bytes += len((delta.content or '').encode('utf-8'))
            response_bytes += len((getattr(delta, 'reasoning_content', None) or '').encode('utf-8'))
            for call in delta.tool_calls or []:
                if call.function:
                    response_bytes += len((call.function.arguments or '').encode('utf-8'))
                    response_bytes += len((call.function.name or '').encode('utf-8'))
            if response_bytes > 2 * 1024 * 1024:
                raise ValueError('Model response output budget exhausted (2 MiB)')
            reasoning = getattr(delta, 'reasoning_content', None)
            if reasoning:
                yield {"type": "reasoning", "content": reasoning}

            if delta.content:
                yield {"type": "text", "content": delta.content}

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    _merge_tool_call_chunk(accumulated_tool_calls, tc)
                    idx = tc.index
                    tool_name = accumulated_tool_calls[idx]["function"]["name"]
                    if idx not in announced_tool_indices and tool_name:
                        announced_tool_indices.add(idx)
                        yield {"type": "tool_start", "name": tool_name}

            if chunk.choices and chunk.choices[0].finish_reason == "tool_calls":
                yield {"type": "tool_calls", "calls": [
                    {**call, "function": {**call["function"],
                        "arguments": call["function"]["arguments"].getvalue()}}
                    for call in accumulated_tool_calls.values()
                ]}

        yield {"type": "done"}

    except Exception as exc:
        yield {"type": "error", "message": str(exc)}
        yield {"type": "done"}
    finally:
        finished.set()
        monitor.join(timeout=1)
        if openai_stream is not None:
            try:
                openai_stream.close()
            except Exception:
                pass


def make_streaming_response(generator: Generator) -> Response:
    """Wrap a generator in a Server-Sent Events Flask Response."""
    return Response(
        stream_with_context(generator),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
