// Chat — message sending, SSE stream reading, tool-call orchestration.

import { api }   from './api.js';
import { state } from './state.js';
import {
  createStreamingMessage, appendAssistantStatus,
  finalizeStreamingMessage, setStreamingMessageLogIndex,
  scrollToBottom, renderAllMessages,
  createThinkingBlock, updateThinkingBlock, finalizeThinkingBlock,
  createToolStrip, toolStripFinalize, toolStripSetApproval, toolStripSetRunning,
} from './renderer.js';
import { applyMarkdown } from './markdown.js';
import { persistConversationFor, createNewConversation } from './conversations.js';
import { refreshFilePanel } from './file_panel.js';
import { escapeHtml } from './format.js';
import { readResponseError, readSSEStream } from './stream_consumer.js';
import { hasPendingAttachments } from './chat_attachments.js';
export { initImageAttachments, hasPendingAttachments } from './chat_attachments.js';
import { buildApiMessages, buildToolsPayload, buildMcpToolMetaPayload } from './chat_payloads.js';
import { createClientId } from './ids.js';
import { sendMessage as sendMessageImpl } from './chat_send.js';
import { editAndResend as editAndResendImpl, regenerateFrom as regenerateFromImpl } from './chat_edit.js';
import { lastAssistantMessageIndex } from './chat_log_utils.js';
import { activeTurns, updateConversationActivity } from './conversation_activity.js';

// Attachment handling lives in chat_attachments.js.

// Payload construction lives in chat_payloads.js.

// ── Streaming state ───────────────────────────────────────────────────────────

// Shared with conversation persistence so branch saves cannot bypass recovery.
const pendingHistoryReloads = state.pendingHistoryReloads ??= new Set();

function hasAuthoritativeHistory(data) {
  return data && !data.error && Array.isArray(data.messages) && Array.isArray(data.displayLog);
}

function requireRecoveredHistory(convId, turn = null) {
  if (pendingHistoryReloads.has(convId) || turn?.historyRecoveryPending) {
    throw new Error('History recovery is incomplete. Reopen this chat before sending or saving changes.');
  }
}

function setStreaming(active) {
  state.isStreaming = active;

  const sendBtn = document.getElementById('send-btn');
  const stopBtn = document.getElementById('stop-btn');
  const input = document.getElementById('user-input');

  if (sendBtn) {
    sendBtn.hidden = active;
    sendBtn.disabled = active || (!(input?.value || '').trim() && !hasPendingAttachments());
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
    renderFrame:        null,
    turn,
    toolStartNames:     [],
    toolStrips:         [],
    toolResultIndex:    0,
    pendingApproval:    null,
    disposeApproval:    null,
    assistantDone:      false,
    streamId,
    abortController:    null,
    cancelled:          false,
    isVisible:          () => isTurnVisible(turn),
    getContentEl:       () => {
      if (!isTurnVisible(turn)) return null;
      if (!ctx.contentEl) ctx.contentEl = createStreamingMessage();
      return ctx.contentEl;
    },
  };
  return ctx;
}

function cancelPendingRender(ctx) {
  if (ctx?.renderFrame == null) return;
  cancelAnimationFrame(ctx.renderFrame);
  ctx.renderFrame = null;
}

function releaseTurnUI(ctx) {
  cancelPendingRender(ctx);
  ctx.disposeApproval?.();
  ctx.contentEl = ctx.reasoningBodyEl = null;
  ctx.toolStrips.fill(null);
}

function scheduleStreamingMarkdown(ctx) {
  if (ctx.renderFrame !== null) return;
  ctx.renderFrame = requestAnimationFrame(() => {
    ctx.renderFrame = null;
    if (ctx.isVisible() && ctx.contentEl) {
      applyMarkdown(ctx.contentEl, ctx.accText);
      scrollToBottom();
    }
  });
}

function activeTurnBaseLog(displayLog = []) {
  for (let i = displayLog.length - 1; i >= 0; i--) {
    if (displayLog[i]?.type === 'message' && displayLog[i]?.role === 'user') {
      return displayLog.slice(0, i + 1);
    }
  }
  return [];
}

function reattachRuntime(runtime) {
  if (!runtime || !isTurnVisible(runtime.turn)) return false;

  const { turn, ctx } = runtime;
  releaseTurnUI(ctx);
  syncVisibleTurn(turn);
  renderAllMessages(turn.displayLog);

  ctx.contentEl = null;
  ctx.reasoningBodyEl = null;

  // The terminal SSE marker may still be in flight. turn.displayLog already contains
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

  showPendingApproval(ctx);
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
    if (attached) {
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
  updateConversationActivity(convId);

  if (isTurnVisible(turn)) {
    syncVisibleTurn(turn);
    document.getElementById('chat-title-input').value = turn.title || '';
    renderAllMessages(turn.displayLog);
    setStreaming(true);
    state.streamId = streamId;
  }


  try {
    ctx.abortController = new AbortController();
    ctx.requestStarted = true;
    const resp = await api.stream('/api/chat/stream', {
      stream_id: streamId,
      conv_id: convId,
      attach: true,
    }, { signal: ctx.abortController.signal });

    if (!resp.ok) throw new Error(await readResponseError(resp));

    const success = await readSSEStream(resp, event => processSSEEvent(event, ctx));
    ctx.streamComplete = success;
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
  if (hasAuthoritativeHistory(data)) pendingHistoryReloads.delete(convId);
  for (const { turn, ctx } of activeTurns.values()) {
    if (!isTurnVisible(turn)) releaseTurnUI(ctx);
  }
  if (data?.active_stream_id) {
    const attached = await attachServerStream(convId, data.active_stream_id, data);
    if (!attached && state.convId === convId) {
      // The worker may finish between loading the chat and attaching its stream.
      const saved = await api.get(`/api/conversations/${convId}`).catch(() => null);
      if (state.convId !== convId) return;
      if (hasAuthoritativeHistory(saved)) pendingHistoryReloads.delete(convId);
      state.messages = saved?.messages || data.messages || [];
      state.displayLog = saved?.displayLog || data.displayLog || [];
      renderAllMessages(state.displayLog);
      state.streamId = null;
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
  requireRecoveredHistory(convId);
  return {
    convId,
    title: currentTitle(),
    systemPrompt: state.folderSystemPrompt.trim() || state.systemPrompt,
    messages: state.messages.slice(),
    displayLog: state.displayLog.slice(),
  };
}

async function persistTurnConversation(turn) {
  requireRecoveredHistory(turn.convId, turn);
  await persistConversationFor(turn.convId, {
    title: turn.title,
    messages: turn.messages,
    displayLog: turn.displayLog,
  });
}

function finishAssistantTurn(turn = null, ctx = null) {
  const isCurrent = !turn?.convId || !ctx || isCurrentStreamContext(turn, ctx);

  cancelPendingRender(ctx);
  ctx?.disposeApproval?.();
  if (turn?.convId && isCurrent) {
    activeTurns.delete(turn.convId);
    updateConversationActivity(turn.convId, Boolean(ctx?.requestStarted && !ctx.streamComplete));
  }

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

function commitRuntimeAssistantPartial(ctx) {
  if (!ctx?.turn) return false;

  let changed = false;
  const appendCommittedEntry = entry => {
    appendRuntimeEntry(ctx, entry);
    changed = true;
  };

  if (ctx.accReasoning) {
    appendCommittedEntry({ type: 'thinking', content: ctx.accReasoning });
  }
  if (ctx.accText && ctx.accText.trim()) {
    appendCommittedEntry({ type: 'message', role: 'assistant', content: ctx.accText });
  }
  return changed;
}

function branchForFooterHost(turn, logIndex) {
  const branch = turn?.displayLog?.[logIndex]?.branch;
  return branch?.kind === 'assistant' ? branch : null;
}

function syncFinalAssistantFooter(turn, ctx) {
  if (!ctx?.contentEl || !isTurnVisible(turn)) return;
  const logIndex = lastAssistantMessageIndex(turn.displayLog);
  if (logIndex >= 0) setStreamingMessageLogIndex(ctx.contentEl, logIndex, branchForFooterHost(turn, logIndex));
}

function finalizeAssistantAnswer(ctx, messages = null, displayLog = null) {
  if (!ctx?.turn) return;
  cancelPendingRender(ctx);

  if (Array.isArray(messages)) ctx.turn.messages = messages;
  if (Array.isArray(displayLog)) ctx.turn.displayLog = displayLog;
  if (Array.isArray(messages) && Array.isArray(displayLog)) {
    ctx.turn.historyRecoveryPending = false;
    pendingHistoryReloads.delete(ctx.turn.convId);
  }
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

  // The composer is released by finishAssistantTurn after the terminal SSE marker.
  if (isTurnVisible(ctx.turn) && isCurrentStreamContext(ctx.turn, ctx)) {
    scrollToBottom();
  }
}

async function runAssistantTurnAndPersist(turn) {
  if (isTurnVisible(turn)) setStreaming(true);
  let ctx = null;

  try {
    ctx = await runChatLoop(turn);
    requireRecoveredHistory(turn.convId, turn);
    if (isCurrentStreamContext(turn, ctx)) syncFinalAssistantFooter(turn, ctx);
  } finally {
    finishAssistantTurn(turn, ctx);
  }
}

export async function stopAssistantTurn() {
  if (!state.isStreaming && !state.streamId) return;

  const runtime = state.convId ? activeTurns.get(state.convId) : null;
  if (runtime?.ctx) runtime.ctx.cancelled = true;
  runtime?.ctx?.disposeApproval?.();
  // Keep consuming until Flask has saved the partial answer and completed every
  // tool response. Releasing the composer early races the next turn's history.
  const streamId = runtime?.streamId || state.streamId;

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
  requireRecoveredHistory(state.convId);
  return sendMessageImpl(userText, chatFlowDeps());
}

// ── SSE helpers ───────────────────────────────────────────────────────────────

function stripForToolEvent(ctx, evt) {
  if (!ctx.isVisible()) return null;
  // Tools execute serially; approvals and running events belong to the next result.
  const stripIndex = ctx.toolResultIndex;
  let strip = ctx.toolStrips[stripIndex];
  if (!strip && ctx.isVisible()) strip = createToolStrip(evt.name);
  if (strip && !ctx.toolStrips[stripIndex]) ctx.toolStrips[stripIndex] = strip;
  return strip;
}

function showPendingApproval(ctx) {
  ctx.disposeApproval?.();
  const evt = ctx.pendingApproval;
  if (!evt || !ctx.isVisible() || ctx.cancelled) return;
  const strip = stripForToolEvent(ctx, evt);
  // Do not block SSE consumption: timeout, cancel, or a decision in another tab
  // must still be able to deliver the tool result and dispose these controls.
  ctx.disposeApproval = toolStripSetApproval(strip, evt.name, evt.args || {}, approved => {
    ctx.pendingApproval = null;
    api.post('/api/chat/approve', {
      stream_id: ctx.streamId, call_id: evt.call_id, approved,
    }).catch(() => {});
  });
}

/** Apply a server event to the current turn. */
async function processSSEEvent(evt, ctx) {
  if (evt.type === 'reasoning') {
    ctx.accReasoning += evt.content;
    if (!ctx.reasoningBodyEl && ctx.isVisible()) ctx.reasoningBodyEl = createThinkingBlock();
    if (ctx.reasoningBodyEl && ctx.isVisible()) updateThinkingBlock(ctx.reasoningBodyEl, ctx.accReasoning);

  } else if (evt.type === 'text') {
    if (ctx.reasoningBodyEl) {
      finalizeThinkingBlock(ctx.reasoningBodyEl, ctx.accReasoning);
      ctx.reasoningBodyEl = null;
    }
    ctx.reasoningFinalized = true;
    ctx.accText += evt.content;
    const el = ctx.getContentEl();
    if (el) scheduleStreamingMarkdown(ctx);

  } else if (evt.type === 'tool_start') {
    // Keep indices aligned even when the user is viewing another chat.
    ctx.toolStartNames.push(evt.name);
    ctx.toolStrips.push(ctx.isVisible() ? createToolStrip(evt.name) : null);

  } else if (evt.type === 'tool_calls') {
    // Tool execution is server-owned; frontend only renders starts/results.

  } else if (evt.type === 'tool_approval_required') {
    ctx.pendingApproval = evt;
    showPendingApproval(ctx);

  } else if (evt.type === 'tool_running') {
    const strip = stripForToolEvent(ctx, evt);
    if (strip) toolStripSetRunning(strip, evt.args || {});

  } else if (evt.type === 'tool_result') {
    if (evt.name === 'update_memory') {
      try {
        if (JSON.parse(evt.result).saved === true) {
          document.dispatchEvent(new CustomEvent('memory:updated'));
        }
      } catch { /* Failed memory calls do not change saved memory. */ }
    }
    cancelPendingRender(ctx);
    ctx.disposeApproval?.();
    ctx.pendingApproval = null;
    if (ctx.reasoningBodyEl) {
      finalizeThinkingBlock(ctx.reasoningBodyEl, ctx.accReasoning);
      ctx.reasoningBodyEl = null;
    }
    const hadContentEl = Boolean(ctx.contentEl);
    if (ctx.contentEl) finalizeStreamingMessage(ctx.contentEl, ctx.accText);

    const strip = stripForToolEvent(ctx, evt);
    // Pass no displayName — renderer.js derives the label via getToolDisplayLabel()
    // which consults the tool_adapters/ system (each adapter declares its own labelArg).
    if (strip) toolStripFinalize(strip, evt.name, evt.args || {}, evt.media?.length ? { text: evt.result || '', media: evt.media } : evt.result || '');
    ctx.toolResultIndex++;

    const committedPartial = commitRuntimeAssistantPartial(ctx);
    if (hadContentEl && committedPartial) syncFinalAssistantFooter(ctx.turn, ctx);
    ctx.contentEl = null;

    appendRuntimeEntry(ctx, evt);

    ctx.accText = '';
    ctx.accReasoning = '';
    ctx.reasoningFinalized = false;
    if (ctx.isVisible()) refreshFilePanel({ keepPreview: true }).catch(() => {});

  } else if (evt.type === 'history_reload') {
    ctx.turn.historyRecoveryPending = true;
    pendingHistoryReloads.add(ctx.turn.convId);
    const saved = await api.get(`/api/conversations/${evt.conv_id}`);
    if (!hasAuthoritativeHistory(saved)) {
      throw new Error(saved?.error || 'Could not recover saved conversation history');
    }
    finalizeAssistantAnswer(ctx, saved.messages, saved.displayLog);
    if (ctx.isVisible()) renderAllMessages(ctx.turn.displayLog);

  } else if (evt.type === 'assistant_done') {
    finalizeAssistantAnswer(ctx, evt.messages, evt.displayLog);
    if ((ctx.cancelled || ctx.failed) && ctx.isVisible()) renderAllMessages(ctx.turn.displayLog);

  } else if (evt.type === 'status') {
    ctx.turn.displayLog.push(evt);
    if (ctx.isVisible()) renderAllMessages(ctx.turn.displayLog);

  } else if (evt.type === 'title') {
    if (ctx.turn) applyTurnTitle(ctx.turn, evt.title || ctx.turn.title);

  } else if (evt.type === 'error') {
    cancelPendingRender(ctx);
    const el = ctx.getContentEl();
    if (el) el.innerHTML = `<span class="inline-error">Error: ${escapeHtml(evt.message)}</span>`;
    ctx.failed = true; // Keep reading the authoritative partial history.
  }

  return true;
}



// ── SSE loop ──────────────────────────────────────────────────────────────────

async function runChatLoop(turn) {
  const streamId = createClientId('stream');
  const ctx = createStreamContext(turn, streamId);

  activeTurns.set(turn.convId, { turn, ctx, streamId });
  updateConversationActivity(turn.convId);
  if (isTurnVisible(turn)) state.streamId = streamId;

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

    ctx.requestStarted = true;
    const resp = await api.stream('/api/chat/stream', payload, { signal: ctx.abortController.signal });

    if (!resp.ok) throw new Error(await readResponseError(resp));

    const success = await readSSEStream(resp, event => processSSEEvent(event, ctx));
    ctx.streamComplete = success;
    if (!success || ctx.cancelled) return ctx;

    if (!ctx.assistantDone) {
      commitRuntimeAssistantPartial(ctx);
      finalizeAssistantAnswer(ctx);
    }
  } catch (err) {
    if (err.name === 'AbortError') return ctx;

    const saved = await api.get(`/api/conversations/${turn.convId}`).catch(() => null);
    if (hasAuthoritativeHistory(saved)) {
      turn.messages = saved.messages;
      turn.displayLog = saved.displayLog;
      turn.historyRecoveryPending = false;
      pendingHistoryReloads.delete(turn.convId);
      syncVisibleTurn(turn);
      if (ctx.isVisible()) renderAllMessages(turn.displayLog);
    }
    if (ctx.isVisible()) appendAssistantStatus(`Network error: ${err.message}`);
  }

  return ctx;
}

// Edit/resend and regenerate flows live in chat_edit.js.

export async function editAndResend(logIndex, newText, imageUrls = [], files = [], attachments = null) {
  requireRecoveredHistory(state.convId);
  return editAndResendImpl(logIndex, newText, imageUrls, files, attachments, chatFlowDeps());
}

export async function regenerateFrom(logIndex) {
  requireRecoveredHistory(state.convId);
  return regenerateFromImpl(logIndex, chatFlowDeps());
}
