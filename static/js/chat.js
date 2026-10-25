// Chat — message sending, SSE stream reading, tool-call orchestration.

import { api }   from './api.js';
import { state } from './state.js';
import {
  createStreamingMessage, appendAssistantStatus,
  cancelAllToolApprovals, finalizeStreamingMessage, setStreamingMessageLogIndex,
  scrollToBottom, renderAllMessages, refreshMessageFooter,
  createThinkingBlock, updateThinkingBlock, finalizeThinkingBlock,
  createToolStrip, toolStripFinalize, toolStripSetApproval, toolStripSetRunning, toolStripSetStopped,
} from './renderer.js';
import { applyMarkdown } from './markdown.js';
import { persistConversationFor, createNewConversation } from './conversations.js';
import { refreshFilePanel } from './file_panel.js';
import { escapeHtml } from './format.js';
import { readResponseError, readSSEStream } from './stream_consumer.js';
import { initImageAttachments, hasPendingAttachments } from './chat_attachments.js';
export { initImageAttachments, hasPendingAttachments } from './chat_attachments.js';
import { buildApiMessages, buildToolsPayload, buildMcpToolMetaPayload } from './chat_payloads.js';
import { createClientId } from './ids.js';
import { sendMessage as sendMessageImpl } from './chat_send.js';
import { editAndResend as editAndResendImpl, regenerateFrom as regenerateFromImpl } from './chat_edit.js';
import { assistantFooterHostIndex, lastAssistantMessageIndex } from './chat_log_utils.js';

const activeTurns = new Map();

// Attachment handling lives in chat_attachments.js.

// Payload construction lives in chat_payloads.js.

// ── Streaming state ───────────────────────────────────────────────────────────

function setStreaming(active) {
  state.isStreaming = active;

  const sendBtn = document.getElementById('send-btn');
  const stopBtn = document.getElementById('stop-btn');
  const input = document.getElementById('user-input');

  if (sendBtn) {
    sendBtn.hidden = active;
    sendBtn.disabled = active || !(input?.value || '').trim();
  }
  if (stopBtn) stopBtn.hidden = !active;
}

function isTurnVisible(turn) {
  return state.convId === turn.convId;
}

function syncVisibleTurn(turn) {
  if (!isTurnVisible(turn)) return;
  state.messages = turn.messages;
  state.displayLog = turn.displayLog;
}

function isCurrentStreamContext(turn, ctx) {
  if (!turn?.convId || !ctx) return true;
  return activeTurns.get(turn.convId)?.ctx === ctx;
}

function createStreamContext(turn, streamId = '') {
  const ctx = {
    accText:            '',
    accReasoning:       '',
    reasoningFinalized: false,
    reasoningBodyEl:    null,
    contentEl:          null,
    turn,
    toolStartNames:     [],
    toolStrips:         [],
    toolApprovalIndex:  0,
    toolRunningIndex:   0,
    toolResultIndex:    0,
    assistantDone:      false,
    streamId,
    abortController:    null,
    cancelled:          false,
    isVisible:          () => isTurnVisible(turn),
    getContentEl:       () => {
      if (!ctx.contentEl && isTurnVisible(turn)) ctx.contentEl = createStreamingMessage();
      return ctx.contentEl;
    },
  };
  return ctx;
}

function activeTurnBaseLog(displayLog = []) {
  const lastUserIndex = [...displayLog]
    .map((entry, index) => ({ entry, index }))
    .reverse()
    .find(item => item.entry?.type === 'message' && item.entry?.role === 'user')?.index;
  return lastUserIndex === undefined ? [] : displayLog.slice(0, lastUserIndex + 1);
}

function reattachRuntime(runtime) {
  if (!runtime || !isTurnVisible(runtime.turn)) return false;

  const { turn, ctx } = runtime;
  syncVisibleTurn(turn);
  renderAllMessages(turn.displayLog);

  ctx.contentEl = null;
  ctx.reasoningBodyEl = null;

  // assistant_done can arrive before the backend finishes title generation and
  // clears the active stream. In that window, turn.displayLog already contains
  // the final assistant answer, while ctx.accText still contains the same text
  // from the live stream. Do not rebuild ctx.accText, or reopening the active
  // conversation will show the last assistant turn twice.
  if (ctx.assistantDone) {
    scrollToBottom(true);
    return true;
  }

  // Rebuild only the live, not-yet-finalized UI. Finalized thinking/message/tool
  // blocks are already in turn.displayLog and were just replayed by renderAllMessages().
  // Recreating every historical tool_start here is what caused old tool stripes to
  // move to the end of the current assistant row when a streaming chat was reopened.
  const finalizedToolCount = ctx.toolResultIndex || 0;
  const pendingToolNames = ctx.toolStartNames.slice(finalizedToolCount);
  ctx.toolStrips = Array(finalizedToolCount).fill(null);

  if (ctx.accReasoning) {
    ctx.reasoningBodyEl = createThinkingBlock();
    updateThinkingBlock(ctx.reasoningBodyEl, ctx.accReasoning);
    if (ctx.reasoningFinalized || ctx.accText || pendingToolNames.length) {
      finalizeThinkingBlock(ctx.reasoningBodyEl, ctx.accReasoning);
      ctx.reasoningBodyEl = null;
    }
  }

  if (ctx.accText) {
    ctx.contentEl = createStreamingMessage();
    applyMarkdown(ctx.contentEl, ctx.accText);
  }

  pendingToolNames.forEach(name => {
    ctx.toolStrips.push(createToolStrip(name));
  });

  scrollToBottom(true);
  return true;
}

export function reattachActiveTurn(convId) {
  const runtime = activeTurns.get(convId);
  return reattachRuntime(runtime);
}

async function attachServerStream(convId, streamId, data = {}) {
  if (!convId || !streamId) return false;
  const existingRuntime = activeTurns.get(convId);
  if (existingRuntime) {
    existingRuntime.streamId ||= streamId;
    existingRuntime.ctx.streamId ||= streamId;
    const attached = reattachActiveTurn(convId);
    if (attached && !existingRuntime.ctx?.assistantDone) {
      state.streamId = existingRuntime.streamId;
      setStreaming(true);
    }
    return attached;
  }

  const turn = {
    convId,
    title:      data.title || currentTitle(),
    messages:   data.messages || [],
    displayLog: activeTurnBaseLog(data.displayLog || []),
  };
  const ctx = createStreamContext(turn, streamId);
  activeTurns.set(convId, { turn, ctx, streamId });

  if (isTurnVisible(turn)) {
    syncVisibleTurn(turn);
    document.getElementById('chat-title-input').value = turn.title || '';
    renderAllMessages(turn.displayLog);
    setStreaming(true);
    state.streamId = streamId;
  }


  try {
    ctx.abortController = new AbortController();
    const resp = await api.stream('/api/chat/stream', {
      stream_id: streamId,
      conv_id: convId,
      attach: true,
    }, { signal: ctx.abortController.signal });

    if (!resp.ok) throw new Error(await readResponseError(resp));

    const success = await readSSEStream(resp, raw => processSSEEvent(raw, ctx));
    if (!success || ctx.cancelled) return false;

    if (!ctx.assistantDone) {
      commitRuntimeAssistantPartial(ctx);
      finalizeAssistantAnswer(ctx);
    }
    if (isCurrentStreamContext(turn, ctx)) syncFinalAssistantFooter(turn, ctx);
    return true;
  } catch (err) {
    return false;
  } finally {
    finishAssistantTurn(turn, ctx);
  }
}

document.addEventListener('chat:conversation-opened', async event => {
  const { convId, data } = event.detail || {};
  if (data?.active_stream_id) {
    const attached = await attachServerStream(convId, data.active_stream_id, data);
    if (!attached && state.convId === convId) {
      state.messages = data.messages || [];
      state.displayLog = data.displayLog || [];
      renderAllMessages(state.displayLog);
      setStreaming(false);
    }
    return;
  }
  if (!reattachActiveTurn(convId)) {
    state.streamId = null;
    setStreaming(false);
  }
});

function currentTitle() {
  return document.getElementById('chat-title-input').value.trim() || 'New Conversation';
}

function applyTurnTitle(turn, title) {
  const nextTitle = (title || '').trim();
  if (!turn || !nextTitle || turn.title === nextTitle) return;

  turn.title = nextTitle;
  if (isTurnVisible(turn)) {
    const titleInput = document.getElementById('chat-title-input');
    if (titleInput && document.activeElement !== titleInput) titleInput.value = nextTitle;
  }
  document.dispatchEvent(new CustomEvent('chat:conversation-title-updated', {
    detail: { convId: turn.convId, title: nextTitle },
  }));
}

function createTurnContext(convId) {
  return {
    convId,
    title: currentTitle(),
    systemPrompt: state.folderSystemPrompt.trim() || state.systemPrompt,
    messages: state.messages.slice(),
    displayLog: state.displayLog.slice(),
  };
}

async function persistTurnConversation(turn) {
  await persistConversationFor(turn.convId, {
    title: turn.title,
    messages: turn.messages,
    displayLog: turn.displayLog,
  });
}

function finishAssistantTurn(turn = null, ctx = null) {
  const isCurrent = !turn?.convId || !ctx || isCurrentStreamContext(turn, ctx);

  if (turn?.convId && isCurrent) activeTurns.delete(turn.convId);

  if ((!turn || isTurnVisible(turn)) && isCurrent) {
    if (ctx) ctx.abortController = null;
    if (!ctx || state.streamId === ctx.streamId) state.streamId = null;
    setStreaming(false);
  }
}

function appendRuntimeEntry(ctx, entry) {
  if (!ctx?.turn || !entry) return;
  ctx.turn.displayLog.push(entry);
  syncVisibleTurn(ctx.turn);
}

function commitRuntimeAssistantPartial(ctx, { includeMessageHistory = false, onEntry = null } = {}) {
  if (!ctx?.turn) return false;

  let changed = false;
  const appendCommittedEntry = entry => {
    appendRuntimeEntry(ctx, entry);
    const logIndex = ctx.turn.displayLog.length - 1;
    onEntry?.(entry, logIndex);
    changed = true;
  };

  if (ctx.accReasoning) {
    appendCommittedEntry({ type: 'thinking', content: ctx.accReasoning });
  }
  if (ctx.accText && ctx.accText.trim()) {
    appendCommittedEntry({ type: 'message', role: 'assistant', content: ctx.accText });
    if (includeMessageHistory) ctx.turn.messages.push({ role: 'assistant', content: ctx.accText });
  }
  if (includeMessageHistory) syncVisibleTurn(ctx.turn);
  return changed;
}

function liveAssistantRowForContext(ctx) {
  const liveNode = ctx?.contentEl ||
    ctx?.reasoningBodyEl ||
    ctx?.toolStrips?.find(strip => strip);
  return liveNode?.closest('.msg-row') || null;
}

function markCommittedEntryInLiveDom(ctx, entry, logIndex) {
  if (!Number.isInteger(logIndex) || logIndex < 0) return;

  if (entry?.type === 'thinking') {
    ctx.reasoningBodyEl?.closest('.thinking-block')?.setAttribute('data-log-index', String(logIndex));
  } else if (entry?.type === 'message' && entry.role === 'assistant') {
    ctx.contentEl?.closest('.msg-row')?.setAttribute('data-log-index', String(logIndex));
  }
}

function branchForFooterHost(turn, logIndex) {
  const branch = turn?.displayLog?.[logIndex]?.branch;
  return branch?.kind === 'assistant' ? branch : null;
}

function stopLiveToolStrips(ctx) {
  ctx.toolStrips.forEach(strip => {
    if (!strip) return;
    const isLive = strip.classList.contains('tool-strip-using') ||
      strip.classList.contains('tool-strip-running') ||
      strip.classList.contains('tool-strip-approval');
    if (isLive) toolStripSetStopped(strip);
  });
}

function appendStoppedStatus(ctx) {
  appendRuntimeEntry(ctx, { type: 'status', content: 'Response stopped.' });
  const logIndex = ctx.turn.displayLog.length - 1;
  if (isTurnVisible(ctx.turn)) appendAssistantStatus('Response stopped.', logIndex, ctx.turn.displayLog[logIndex]);
  return logIndex;
}

function finalizeInterruptedAssistantTurn(ctx) {
  if (!ctx?.turn || ctx.assistantDone) return;

  const liveRow = liveAssistantRowForContext(ctx);

  if (ctx.reasoningBodyEl) {
    finalizeThinkingBlock(ctx.reasoningBodyEl, ctx.accReasoning);
  }

  stopLiveToolStrips(ctx);

  const hadContentEl = Boolean(ctx.contentEl);
  const committed = commitRuntimeAssistantPartial(ctx, {
    includeMessageHistory: true,
    onEntry: (entry, logIndex) => markCommittedEntryInLiveDom(ctx, entry, logIndex),
  });

  let footerHostIndex = assistantFooterHostIndex(ctx.turn.displayLog);

  if (hadContentEl) {
    finalizeStreamingMessage(ctx.contentEl, ctx.accText, {
      logIndex: footerHostIndex,
      branch: branchForFooterHost(ctx.turn, footerHostIndex),
    });
  } else if (committed && footerHostIndex >= 0) {
    refreshMessageFooter(footerHostIndex);
  } else {
    footerHostIndex = appendStoppedStatus(ctx);
  }

  if (!hadContentEl && committed && !liveRow?.querySelector('.msg-footer')) {
    const row = liveAssistantRowForContext(ctx);
    if (row && footerHostIndex >= 0) refreshMessageFooter(footerHostIndex);
  }

  ctx.reasoningBodyEl = null;
  ctx.assistantDone = true;
  syncVisibleTurn(ctx.turn);
  scrollToBottom();
}

function syncFinalAssistantFooter(turn, ctx) {
  if (!ctx?.contentEl || !isTurnVisible(turn)) return;
  const logIndex = lastAssistantMessageIndex(turn.displayLog);
  if (logIndex >= 0) setStreamingMessageLogIndex(ctx.contentEl, logIndex, branchForFooterHost(turn, logIndex));
}

function finalizeAssistantAnswer(ctx, messages = null, displayLog = null) {
  if (!ctx?.turn) return;

  if (Array.isArray(messages)) ctx.turn.messages = messages;
  if (Array.isArray(displayLog)) ctx.turn.displayLog = displayLog;
  syncVisibleTurn(ctx.turn);

  if (ctx.assistantDone) {
    syncFinalAssistantFooter(ctx.turn, ctx);
    return;
  }

  ctx.assistantDone = true;

  if (ctx.reasoningBodyEl) {
    finalizeThinkingBlock(ctx.reasoningBodyEl, ctx.accReasoning);
    ctx.reasoningBodyEl = null;
  }
  if (ctx.contentEl) {
    finalizeStreamingMessage(ctx.contentEl, ctx.accText);
    syncFinalAssistantFooter(ctx.turn, ctx);
  }

  // assistant_done owns only the visible UI transition. Stream cleanup/title work may continue.
  if (isTurnVisible(ctx.turn) && isCurrentStreamContext(ctx.turn, ctx)) {
    state.streamId = null;
    setStreaming(false);
    scrollToBottom();
  }
}

async function runAssistantTurnAndPersist(turn) {
  setStreaming(true);
  let ctx = null;

  try {
    ctx = await runChatLoop(turn);
    if (isCurrentStreamContext(turn, ctx)) syncFinalAssistantFooter(turn, ctx);
  } finally {
    finishAssistantTurn(turn, ctx);
  }
}

export async function stopAssistantTurn() {
  if (!state.isStreaming && !state.streamId) return;

  const runtime = state.convId ? activeTurns.get(state.convId) : null;
  if (runtime?.ctx) finalizeInterruptedAssistantTurn(runtime.ctx);

  if (runtime?.ctx) runtime.ctx.cancelled = true;
  cancelAllToolApprovals();
  runtime?.ctx?.abortController?.abort();

  const streamId = runtime?.streamId || state.streamId;
  state.streamId = null;
  setStreaming(false);

  if (streamId) {
    try { await api.post('/api/chat/cancel', { stream_id: streamId }); } catch {}
  }
}

function chatFlowDeps() {
  return {
    createNewConversation,
    createTurnContext,
    setStreaming,
    syncVisibleTurn,
    isTurnVisible,
    persistTurnConversation,
    runAssistantTurnAndPersist,
    hasPendingAttachments,
  };
}

// ── Main entry point ──────────────────────────────────────────────────────────

export async function sendMessage(userText) {
  return sendMessageImpl(userText, chatFlowDeps());
}

// ── SSE helpers ───────────────────────────────────────────────────────────────

function stripForToolEvent(ctx, evt, cursorKey) {
  const stripIndex = ctx[cursorKey] || 0;
  let strip = ctx.toolStrips[stripIndex];
  if (!strip && ctx.isVisible()) strip = createToolStrip(evt.name);
  if (strip && !ctx.toolStrips[stripIndex]) ctx.toolStrips[stripIndex] = strip;
  ctx[cursorKey] = stripIndex + 1;
  return strip;
}

/** Parses a single SSE event and mutates the streaming context. Returns false on terminal error. */
async function processSSEEvent(raw, ctx) {
  const evt = JSON.parse(raw);

  if (evt.type === 'reasoning') {
    ctx.accReasoning += evt.content;
    if (!ctx.reasoningBodyEl && ctx.isVisible()) ctx.reasoningBodyEl = createThinkingBlock();
    if (ctx.reasoningBodyEl) updateThinkingBlock(ctx.reasoningBodyEl, ctx.accReasoning);

  } else if (evt.type === 'text') {
    if (ctx.reasoningBodyEl) {
      finalizeThinkingBlock(ctx.reasoningBodyEl, ctx.accReasoning);
      ctx.reasoningBodyEl = null;
    }
    ctx.reasoningFinalized = true;
    ctx.accText += evt.content;
    const el = ctx.getContentEl();
    if (el) {
      applyMarkdown(el, ctx.accText);
      scrollToBottom();
    }

  } else if (evt.type === 'tool_start') {
    // Keep indices aligned even when the user is viewing another chat.
    ctx.toolStartNames.push(evt.name);
    ctx.toolStrips.push(ctx.isVisible() ? createToolStrip(evt.name) : null);

  } else if (evt.type === 'tool_calls') {
    // Tool execution is server-owned; frontend only renders starts/results.

  } else if (evt.type === 'tool_approval_required') {
    // Server is paused waiting for the user to approve or deny this tool call.
    // Use a cursor, not indexOf(name), so repeated calls to the same tool update
    // the matching strip instead of always reusing the first one.
    const strip = stripForToolEvent(ctx, evt, 'toolApprovalIndex');

    let approved = false;
    if (strip) {
      const fakeCall = { function: { name: evt.name, arguments: JSON.stringify(evt.args || {}) } };
      approved = await toolStripSetApproval(strip, fakeCall);
      if (approved) toolStripSetRunning(strip, evt.args || {});
    }

    // Send the decision back to the server so it can unblock and proceed.
    api.post('/api/chat/approve', {
      stream_id: ctx.streamId,
      call_id:   evt.call_id,
      approved,
    }).catch(() => {});

  } else if (evt.type === 'tool_running') {
    const strip = stripForToolEvent(ctx, evt, 'toolRunningIndex');
    if (strip) toolStripSetRunning(strip, evt.args || {});

  } else if (evt.type === 'tool_result') {
    if (ctx.reasoningBodyEl) {
      finalizeThinkingBlock(ctx.reasoningBodyEl, ctx.accReasoning);
      ctx.reasoningBodyEl = null;
    }
    const hadContentEl = Boolean(ctx.contentEl);
    if (ctx.contentEl) finalizeStreamingMessage(ctx.contentEl, ctx.accText);

    const strip = stripForToolEvent(ctx, evt, 'toolResultIndex');
    // Pass no displayName — renderer.js derives the label via getToolDisplayLabel()
    // which consults the tool_adapters/ system (each adapter declares its own labelArg).
    if (strip) toolStripFinalize(strip, evt.name, evt.args || {}, evt.media?.length ? { text: evt.result || '', media: evt.media } : evt.result || '');
    // Note: stripForToolEvent already increments ctx.toolResultIndex internally.

    const committedPartial = commitRuntimeAssistantPartial(ctx);
    if (hadContentEl && committedPartial) syncFinalAssistantFooter(ctx.turn, ctx);
    ctx.contentEl = null;

    appendRuntimeEntry(ctx, {
      type: 'tool_result',
      name: evt.name,
      args: evt.args || {},
      result: evt.media?.length ? { text: evt.result || '', media: evt.media } : evt.result || '',
      ...(evt.displayName ? { displayName: evt.displayName } : {}),
    });

    ctx.accText = '';
    ctx.accReasoning = '';
    ctx.reasoningFinalized = false;
    if (ctx.isVisible()) refreshFilePanel({ keepPreview: true }).catch(() => {});

  } else if (evt.type === 'assistant_done') {
    finalizeAssistantAnswer(ctx, evt.messages, evt.displayLog);

  } else if (evt.type === 'title') {
    if (ctx.turn) applyTurnTitle(ctx.turn, evt.title || ctx.turn.title);

  } else if (evt.type === 'error') {
    const el = ctx.getContentEl();
    if (el) el.innerHTML = `<span class="inline-error">Error: ${escapeHtml(evt.message)}</span>`;
    return false; // signal abort
  }

  return true;
}



// ── SSE loop ──────────────────────────────────────────────────────────────────

async function runChatLoop(turn) {
  const streamId = createClientId('stream');
  const ctx = createStreamContext(turn, streamId);

  activeTurns.set(turn.convId, { turn, ctx, streamId });
  state.streamId = streamId;

  // A newly-started turn already has the user's message appended in-place.
  // Reattaching here would fully re-render the chat and cause the first flash
  // right after send. Reattach is only for switching/reloading into a running turn.

  try {
    ctx.abortController = new AbortController();

    const payload = {
      model:     state.model || 'gpt-4o',
      temperature:  state.temperature ?? 0.7,
      messages:              await buildApiMessages(turn.messages, turn.systemPrompt),
      conversation_messages: turn.messages,
      display_log:           turn.displayLog,
      title:                 turn.title,
      tools:                 buildToolsPayload(),
      mcp_tool_meta:         buildMcpToolMetaPayload(),
      stream_id:             streamId,
      conv_id:               turn.convId,
      auto_generate_titles:  state.autoGenerateTitles ?? true,
    };

    const resp = await api.stream('/api/chat/stream', payload, { signal: ctx.abortController.signal });

    if (!resp.ok) throw new Error(await readResponseError(resp));

    const success = await readSSEStream(resp, raw => processSSEEvent(raw, ctx));
    if (!success || ctx.cancelled) return ctx;

    if (!ctx.assistantDone) {
      commitRuntimeAssistantPartial(ctx);
      finalizeAssistantAnswer(ctx);
    }
  } catch (err) {
    if (ctx.cancelled || err.name === 'AbortError') return ctx;

    const el = ctx.getContentEl();
    if (el) el.innerHTML = `<span class="inline-error">Network error: ${escapeHtml(err.message)}</span>`;
  }

  return ctx;
}

// Edit/resend and regenerate flows live in chat_edit.js.

export async function editAndResend(logIndex, newText, imageUrls = [], files = [], attachments = null) {
  return editAndResendImpl(logIndex, newText, imageUrls, files, attachments, chatFlowDeps());
}

export async function regenerateFrom(logIndex) {
  return regenerateFromImpl(logIndex, chatFlowDeps());
}
