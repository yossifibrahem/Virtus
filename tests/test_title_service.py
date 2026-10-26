"""
Tests for title_service.py — stateless, independently importable.

title_service was split out of chat_turn_service.py.  It has no Flask context,
no DB, and no network dependency, so it can be imported and unit-tested in
isolation without any other Lumen module.

Functions under test:
  _messages_to_text  — converts message history to a compact plain-text block
  _extract_title     — extracts a title string from three possible model response shapes
  generate_title     — end-to-end orchestration (mocked client)
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock
from types import SimpleNamespace

import httpx
from openai import BadRequestError

import pytest

import title_service as svc


# ---------------------------------------------------------------------------
# _messages_to_text
# ---------------------------------------------------------------------------

class TestMessagesToText:
    """
    Converts a message list into a compact plain-text block for the title model.
    Only user/assistant roles are included; system, tool, and other roles are
    filtered out.
    """

    def test_simple_user_and_assistant_messages(self):
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        result = svc._messages_to_text(messages)
        assert "user: Hello" in result
        assert "assistant: Hi there" in result

    def test_roles_appear_as_prefixes(self):
        messages = [{"role": "user", "content": "What is 2+2?"}]
        assert svc._messages_to_text(messages).startswith("user:")

    def test_system_messages_excluded(self):
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Question"},
        ]
        result = svc._messages_to_text(messages)
        assert "system" not in result
        assert "Question" in result

    def test_tool_messages_excluded(self):
        messages = [
            {"role": "user", "content": "Run it"},
            {"role": "tool", "content": "result output"},
        ]
        result = svc._messages_to_text(messages)
        assert "result output" not in result

    def test_list_content_blocks_joined(self):
        """Vision messages carry content as a list of typed blocks."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this"},
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ],
            }
        ]
        result = svc._messages_to_text(messages)
        assert "Describe this" in result

    def test_image_blocks_excluded_from_text(self):
        """Non-text blocks must not inject garbage like 'image_url' into the text."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                    {"type": "text", "text": "Caption this"},
                ],
            }
        ]
        result = svc._messages_to_text(messages)
        assert "image_url" not in result
        assert "Caption this" in result

    def test_double_newlines_collapsed(self):
        messages = [{"role": "user", "content": "Line one\n\nLine two"}]
        result = svc._messages_to_text(messages)
        assert "\n\n" not in result

    def test_empty_messages_returns_empty_string(self):
        assert svc._messages_to_text([]) == ""

    def test_multiple_messages_separated_by_newline(self):
        messages = [
            {"role": "user", "content": "Ping"},
            {"role": "assistant", "content": "Pong"},
        ]
        result = svc._messages_to_text(messages)
        lines = result.strip().splitlines()
        assert len(lines) == 2

    def test_whitespace_stripped_from_content(self):
        messages = [{"role": "user", "content": "  trimmed  "}]
        result = svc._messages_to_text(messages)
        assert "  trimmed  " not in result
        assert "trimmed" in result

    def test_empty_content_message_omitted(self):
        """A message with empty content after stripping should not produce a blank line."""
        messages = [
            {"role": "user", "content": "   "},
            {"role": "user", "content": "real"},
        ]
        result = svc._messages_to_text(messages)
        assert result.strip() == "user: real"


# ---------------------------------------------------------------------------
# _extract_title
# ---------------------------------------------------------------------------

class TestExtractTitle:
    """
    Three distinct response shapes are supported because different model
    families return the set_title tool call differently.
    """

    def _tool_call_message(self, title: str) -> MagicMock:
        tc = MagicMock()
        tc.function.arguments = json.dumps({"title": title})
        msg = MagicMock()
        msg.tool_calls = [tc]
        msg.reasoning_content = None
        return msg

    def _reasoning_message(self, reasoning: str) -> MagicMock:
        msg = MagicMock()
        msg.tool_calls = None
        msg.reasoning_content = reasoning
        return msg

    # -- standard tool_calls path -------------------------------------------

    def test_standard_tool_call_path(self):
        msg = self._tool_call_message("Docker Volume Permissions")
        assert svc._extract_title(msg) == "Docker Volume Permissions"

    def test_tool_call_title_with_spaces(self):
        msg = self._tool_call_message("JWT Token Expiry Bug")
        assert svc._extract_title(msg) == "JWT Token Expiry Bug"

    def test_tool_call_title_unicode(self):
        msg = self._tool_call_message("Débogage Python")
        assert svc._extract_title(msg) == "Débogage Python"

    # -- <tool_call> XML in reasoning_content path --------------------------

    def test_reasoning_xml_tool_call_path(self):
        """Some reasoning models embed the call in <tool_call> XML."""
        reasoning = '<tool_call>{"name": "set_title", "arguments": {"title": "JWT Token Bug"}}</tool_call>'
        msg = self._reasoning_message(reasoning)
        assert svc._extract_title(msg) == "JWT Token Bug"

    def test_reasoning_xml_with_whitespace_inside_tags(self):
        reasoning = (
            "<tool_call>\n"
            '  {"name": "set_title", "arguments": {"title": "Async Race Condition"}}\n'
            "</tool_call>"
        )
        msg = self._reasoning_message(reasoning)
        assert svc._extract_title(msg) == "Async Race Condition"

    # -- <parameter=title> XML path -----------------------------------------

    def test_reasoning_parameter_xml_path(self):
        """Alternate XML format used by some models."""
        reasoning = "<parameter=title>Fibonacci in Python</parameter>"
        msg = self._reasoning_message(reasoning)
        assert svc._extract_title(msg) == "Fibonacci in Python"

    def test_parameter_xml_with_leading_trailing_whitespace(self):
        reasoning = "<parameter=title>  Spaced Title  </parameter>"
        msg = self._reasoning_message(reasoning)
        assert svc._extract_title(msg) == "Spaced Title"

    # -- error path ---------------------------------------------------------

    def test_no_tool_call_and_no_xml_raises(self):
        msg = MagicMock()
        msg.tool_calls = None
        msg.reasoning_content = "I was just thinking..."
        with pytest.raises(ValueError, match="tool call"):
            svc._extract_title(msg)

    def test_empty_reasoning_content_raises(self):
        msg = MagicMock()
        msg.tool_calls = None
        msg.reasoning_content = ""
        with pytest.raises(ValueError, match="tool call"):
            svc._extract_title(msg)

    def test_none_reasoning_content_raises(self):
        msg = MagicMock()
        msg.tool_calls = None
        msg.reasoning_content = None
        with pytest.raises(ValueError, match="tool call"):
            svc._extract_title(msg)


# ---------------------------------------------------------------------------
# generate_title
# ---------------------------------------------------------------------------

class TestGenerateTitle:
    messages = [{"role": "user", "content": "How do I fix Docker volume permissions?"}]

    def _make_client(self, content="<title>Docker Volume Permissions</title>", finish_reason="stop"):
        client = MagicMock()
        client.with_options.return_value = client
        choice = client.chat.completions.create.return_value.choices[0]
        choice.message = SimpleNamespace(content=content, tool_calls=None, reasoning_content=None)
        choice.finish_reason = finish_reason
        return client

    def test_returns_generated_text_title_without_tools(self):
        client = self._make_client()
        assert svc.generate_title(client, {"model": "qwen/test"}, self.messages) == "Docker Volume Permissions"
        request = client.chat.completions.create.call_args.kwargs
        assert "tools" not in request and "tool_choice" not in request
        assert request["reasoning_effort"] == "none"
        assert request["model"] == "qwen/test"
        assert request["max_tokens"] == 96
        assert request["messages"][1]["content"].endswith("/no_think")
        client.with_options.assert_called_once_with(timeout=15.0, max_retries=0)

    def test_normal_chat_reasoning_settings_are_not_used(self):
        client = self._make_client()
        svc.generate_title(client, {"reasoning_effort": "high", "max_tokens": 10000}, self.messages)
        assert client.chat.completions.create.call_args.kwargs["reasoning_effort"] == "none"

    def test_first_four_messages_and_context_are_bounded(self):
        client = self._make_client()
        messages = [{"role": "user", "content": f"msg{i}"} for i in range(10)]
        svc.generate_title(client, {}, messages)
        content = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        assert "msg3" in content and "msg4" not in content
        svc.generate_title(client, {}, [{"role": "user", "content": "a" * 10000}])
        assert len(client.chat.completions.create.call_args.kwargs["messages"][1]["content"]) == svc.MAX_CONTEXT_CHARS

    def test_network_error_uses_user_fallback_without_retry(self, caplog):
        client = self._make_client()
        client.chat.completions.create.side_effect = TimeoutError("private message")
        assert svc.generate_title(client, {}, self.messages) == "How do I fix Docker volume permissions?"
        assert client.chat.completions.create.call_count == 1
        assert "TimeoutError" in caplog.text
        assert "private message" not in caplog.text

    @pytest.mark.parametrize("content,reason", [(None, "stop"), ("<think>Still thinking", "length"), ("<title>Partial", "length"), ('{"title": 42}', "stop")])
    def test_missing_invalid_or_truncated_title_uses_fallback(self, content, reason):
        client = self._make_client(content, reason)
        assert svc.generate_title(client, {}, self.messages) == "How do I fix Docker volume permissions?"

    def test_unsupported_reasoning_option_gets_compatibility_attempt(self):
        client = self._make_client()
        good_response = client.chat.completions.create.return_value
        request = httpx.Request("POST", "http://localhost/v1/chat/completions")
        error = BadRequestError("Unsupported parameter: reasoning_effort", response=httpx.Response(400, request=request), body=None)
        client.chat.completions.create.side_effect = [error, good_response]
        assert svc.generate_title(client, {}, self.messages) == "Docker Volume Permissions"
        calls = client.chat.completions.create.call_args_list
        assert "reasoning_effort" in calls[0].kwargs
        assert "reasoning_effort" not in calls[1].kwargs
        assert 0 < client.with_options.call_args.kwargs["timeout"] <= 15

    def test_other_bad_request_is_not_retried(self):
        client = self._make_client()
        request = httpx.Request("POST", "http://localhost/v1/chat/completions")
        client.chat.completions.create.side_effect = BadRequestError("Model not found", response=httpx.Response(400, request=request), body=None)
        assert svc.generate_title(client, {}, self.messages)
        assert client.chat.completions.create.call_count == 1

    def test_empty_input_does_not_call_model(self):
        client = self._make_client()
        assert svc.generate_title(client, {}, []) is None
        client.chat.completions.create.assert_not_called()

    def test_multimodal_fallback_is_bounded(self):
        client = self._make_client(None)
        messages = [{"role": "user", "content": [{"type": "image_url", "image_url": {}}, {"type": "text", "text": "x" * 300}]}]
        assert svc.generate_title(client, {}, messages) == "x" * 80


@pytest.mark.parametrize("content,expected", [
    ("<title>Docker Volume Permissions</title>", "Docker Volume Permissions"),
    ('"Docker Volume Permissions"', "Docker Volume Permissions"),
    ("Title: Docker Volume Permissions", "Docker Volume Permissions"),
    ('{"title": "Docker Volume Permissions"}', "Docker Volume Permissions"),
    ("<think>Ignore this title</think><title>Actual Subject</title>", "Actual Subject"),
    ("<think>Ignore this title</think>Actual Subject", "Actual Subject"),
    ("<title>تصحيح أخطاء بايثون</title>", "تصحيح أخطاء بايثون"),
])
def test_text_response_formats(content, expected):
    assert svc._extract_title(SimpleNamespace(content=content)) == expected


@pytest.mark.parametrize("content", ["<think>Never finished", "<think>Only reasoning</think>", "<title></title>", "a" * 81, "Here's a title:\nUnstructured response"])
def test_rejects_reasoning_or_invalid_text(content):
    with pytest.raises(ValueError):
        svc._extract_title(SimpleNamespace(content=content))


@pytest.mark.parametrize("mode", ["generated", "fallback", "disabled"])
def test_title_is_published_persisted_and_turn_finalized(tmp_lumen, monkeypatch, mode):
    import threading

    import chat_turn_service
    import store

    conversation = store.create()
    client = TestGenerateTitle()._make_client()
    if mode == "fallback":
        client.chat.completions.create.side_effect = TimeoutError()
    monkeypatch.setattr(chat_turn_service, "openai_client", lambda body: client)
    monkeypatch.setattr(chat_turn_service, "_inject_memory", lambda messages: messages)
    monkeypatch.setattr(chat_turn_service.stream_module, "stream_chat_completion", lambda *a, **k: iter([
        {"type": "text", "content": "Check the mounted directory's ownership."},
        {"type": "done"},
    ]))
    messages = [{"role": "user", "content": "How do I fix Docker volume permissions?"}]
    events = []
    chat_turn_service.run_persistent_chat_turn({
        "conv_id": conversation["id"],
        "title": "New Conversation",
        "conversation_messages": messages,
        "messages": messages,
        "auto_generate_titles": mode != "disabled",
    }, threading.Event(), "title-test", events.append)
    saved = store.load(conversation["id"])
    title_events = [event for event in events if event["type"] == "title"]
    if mode == "disabled":
        assert saved["title"] == "New Conversation"
        assert not title_events
        client.chat.completions.create.assert_not_called()
    else:
        expected = "Docker Volume Permissions" if mode == "generated" else messages[0]["content"]
        assert saved["title"] == expected
        assert title_events == [{"type": "title", "title": expected}]
    assert saved["messages"][-1]["role"] == "assistant"
    assert not saved["streaming"]
    assert "active_stream_id" not in saved
    assert not any(event["type"] == "error" for event in events)
    assert events[-1]["type"] == "assistant_done"
    assert events[-1]["messages"] == saved["messages"]
    client.close.assert_called_once()
