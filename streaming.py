"""
SSE streaming — event formatting and OpenAI stream consumption.

`stream_chat_completion` is a pure generator; it knows nothing about
Flask.  `make_streaming_response` wraps it into a Flask Response.
"""
from __future__ import annotations

import asyncio
import json
from io import StringIO
import queue
import threading
from typing import Any, Generator

from flask import Response, stream_with_context
from openai import AsyncOpenAI, OpenAI


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


def _async_client(client):
    """Use the configured endpoint with a transport that supports task cancellation."""
    return AsyncOpenAI(
        api_key=client.api_key, base_url=client.base_url,
        timeout=client.timeout, max_retries=0,
    )


def _cancellable_chunks(client, request_kwargs, cancel_event):
    """Bridge Flask's synchronous consumer to an owned, cancellable HTTP task.

    Only the event-loop thread reads and closes the provider connection. Cancelling
    its task interrupts a pending connect/read rather than closing a sync socket
    from another thread. The bridge joins its worker before returning.
    """
    if cancel_event.is_set():
        return
    chunks = queue.Queue(maxsize=16)
    stopped = threading.Event()
    finished = threading.Event()
    errors = []

    async def produce(provider):
        stream = await provider.chat.completions.create(**request_kwargs)
        if not request_kwargs.get('stream'):
            chunks.put_nowait(stream)
            return
        async with stream:
            async for chunk in stream:
                while chunks.full():
                    await asyncio.sleep(.01)
                chunks.put_nowait(chunk)

    async def interrupt(task):
        while not cancel_event.is_set() and not stopped.is_set():
            await asyncio.sleep(.05)
        task.cancel()

    async def run():
        async with _async_client(client) as provider:
            task = asyncio.create_task(produce(provider))
            watcher = asyncio.create_task(interrupt(task))
            try:
                await task
            except asyncio.CancelledError:
                if not cancel_event.is_set() and not stopped.is_set():
                    raise
            finally:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)

    def worker():
        try:
            asyncio.run(run())
        except Exception as exc:
            errors.append(exc)
        finally:
            finished.set()

    reader = threading.Thread(target=worker, daemon=True)
    reader.start()
    try:
        while True:
            try:
                chunk = chunks.get(timeout=.05)
            except queue.Empty:
                if finished.is_set():
                    if errors and not cancel_event.is_set():
                        raise errors[0]
                    break
                continue
            if not cancel_event.is_set():
                yield chunk
    finally:
        stopped.set()
        reader.join()


def complete_chat(client, request_kwargs, cancel_event):
    """Run a non-streaming request (such as a title) with the same cancellation."""
    response = _cancellable_chunks(client, request_kwargs, cancel_event)
    try:
        return next(response, None)
    finally:
        response.close()


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
        response_bytes = 0
        for chunk in _cancellable_chunks(client, request_kwargs, cancel_event):
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


def make_streaming_response(generator: Generator) -> Response:
    """Wrap a generator in a Server-Sent Events Flask Response."""
    return Response(
        stream_with_context(generator),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
