// Chat edit flow — edit/resend and regenerate assistant turns.

import { state } from './state.js';
import { appendMessage, renderAllMessages } from './renderer.js';
import { refreshMessageFooter } from './renderer_actions.js';
import { attachAssistantBranch, attachUserBranch, captureSuffix, logIndexToMessagesIndex, syncVisibleBranches } from './chat_branches.js';
import { assistantFooterHostIndex } from './chat_log_utils.js';

// ── Edit & Resend ─────────────────────────────────────────────────────────────

/**
 * Extract the image ref from a server image URL, e.g. "/api/images/<ref>" → "<ref>".
 * Returns null if the URL doesn't match the expected pattern.
 */
function imageUrlToRef(url) {
  const match = url && url.match(/\/api\/images\/([^/?#]+)/);
  return match ? match[1] : null;
}


function isAssistantTurnEntry(entry) {
  return entry?.role === 'assistant' ||
    entry?.type === 'thinking' ||
    entry?.type === 'tool_result' ||
    entry?.type === 'status';
}

function resolveRegenerateLogIndex(logIndex) {
  const displayLog = state.displayLog || [];
  const requested = Number.isInteger(logIndex) ? logIndex : -1;

  if (isAssistantTurnEntry(displayLog[requested])) return requested;

  for (let i = Math.min(requested, displayLog.length - 1); i >= 0; i--) {
    if (isAssistantTurnEntry(displayLog[i])) return i;
  }

  return assistantFooterHostIndex(displayLog);
}

export async function editAndResend(logIndex, newText, imageUrls = [], files = [], attachments = null, deps) {
  if (state.isStreaming) return;

  const normalizedAttachments = Array.isArray(attachments)
    ? attachments
    : [
        ...(files || []).map(file => ({ kind: 'file', ...file })),
        ...(imageUrls || []).map(url => ({ kind: 'image', url, ref: imageUrlToRef(url) })),
      ];

  if (!newText.trim() && !normalizedAttachments.length) return;
  if (!state.convId) await deps.createNewConversation();

  syncVisibleBranches();
  const existingBranch = state.displayLog[logIndex]?.branch?.kind === 'user' ? state.displayLog[logIndex].branch : null;
  const messagesIndex = logIndexToMessagesIndex(logIndex);
  const oldBranch = captureSuffix({ messages: state.messages, displayLog: state.displayLog }, logIndex, messagesIndex, 'user', logIndex);
  state.displayLog.splice(logIndex);
  state.messages.splice(messagesIndex);
  renderAllMessages(state.displayLog);

  // Rebuild the user turn with the original attachments so they are preserved.
  const textToSend = newText.trim();
  const workspaceAttachments = normalizedAttachments.filter(entry => entry.path);
  const imageAttachments = normalizedAttachments.filter(entry => entry.kind === 'image');
  const refs = imageAttachments.map(entry => entry.ref || imageUrlToRef(entry.url)).filter(Boolean);

  let apiContent;
  if (refs.length) {
    apiContent = [];
    if (textToSend) apiContent.push({ type: 'text', text: textToSend });
    refs.forEach(ref => apiContent.push({ type: 'image_ref', ref }));
  } else {
    apiContent = textToSend;
  }

  const displayContent = normalizedAttachments.length
    ? {
        text: textToSend,
        attachments: normalizedAttachments,
        imageUrls: imageAttachments.map(entry => entry.url).filter(Boolean),
        files: workspaceAttachments,
      }
    : textToSend;

  const turn = deps.createTurnContext(state.convId);
  turn.messages.push({ role: 'user', content: apiContent, attachments: workspaceAttachments });
  turn.displayLog.push({ type: 'message', role: 'user', content: displayContent });
  deps.syncVisibleTurn(turn);
  if (deps.isTurnVisible(turn)) appendMessage('user', displayContent, turn.displayLog.length - 1);
  await deps.persistTurnConversation(turn);

  await deps.runAssistantTurnAndPersist(turn);
  const branchHostIndex = attachUserBranch(turn, logIndex, messagesIndex, oldBranch, existingBranch);
  deps.syncVisibleTurn(turn);
  if (deps.isTurnVisible(turn)) refreshMessageFooter(branchHostIndex);
  await deps.persistTurnConversation(turn);
}

// ── Regenerate ────────────────────────────────────────────────────────────────

export async function regenerateFrom(logIndex, deps) {
  if (state.isStreaming) return;

  syncVisibleBranches();

  logIndex = resolveRegenerateLogIndex(logIndex);
  if (logIndex < 0) return;

  // Walk back to find the index right after the last user message — that's
  // where the whole assistant turn (thinking + tool calls + responses) begins.
  let turnStart = -1;
  for (let i = logIndex - 1; i >= 0; i--) {
    const entry = state.displayLog[i];
    if (!entry) continue;
    if (entry.type === 'message' && entry.role === 'user') {
      turnStart = i + 1;
      break;
    }
  }
  if (turnStart < 0) {
    console.warn('[Virtus] Cannot regenerate: no user message found before assistant turn.', { logIndex, displayLog: state.displayLog });
    return;
  }

  const existingBranch = state.displayLog[logIndex]?.branch?.kind === 'assistant' ? state.displayLog[logIndex].branch : null;
  const messagesIndex = logIndexToMessagesIndex(turnStart);
  const oldBranch = captureSuffix({ messages: state.messages, displayLog: state.displayLog }, turnStart, messagesIndex, 'assistant', logIndex);
  state.displayLog.splice(turnStart);
  state.messages.splice(messagesIndex);
  renderAllMessages(state.displayLog);

  if (!state.convId) return;
  const turn = deps.createTurnContext(state.convId);
  await deps.persistTurnConversation(turn);
  await deps.runAssistantTurnAndPersist(turn);
  const branchHostIndex = attachAssistantBranch(turn, turnStart, messagesIndex, oldBranch, logIndex, existingBranch);
  deps.syncVisibleTurn(turn);
  if (deps.isTurnVisible(turn)) refreshMessageFooter(branchHostIndex);
  await deps.persistTurnConversation(turn);
}
