// Pure helpers for mapping conversation display-log entries to assistant turns,
// branch hosts, and message-array indexes. Keep DOM and state out of this file.

const ASSISTANT_ARTIFACT_TYPES = new Set(['thinking', 'tool_result', 'status']);

export function isTransientStatus(entry) {
  // Older saved fallback notices predate the presentation field.
  return entry?.type === 'status' && (entry.presentation === 'toast' ||
    entry.content === 'This model does not support image inputs. Continuing without images.');
}

export function isUserMessage(entry) {
  return entry?.type === 'message' && entry.role === 'user';
}

export function isAssistantMessage(entry) {
  return entry?.type === 'message' && entry.role === 'assistant';
}

export function hasAssistantMessageContent(entry) {
  return isAssistantMessage(entry) && String(entry.content ?? '').trim().length > 0;
}

export function isAssistantArtifact(entry) {
  return Boolean(entry && ASSISTANT_ARTIFACT_TYPES.has(entry.type) && !isTransientStatus(entry));
}

export function canHostAssistantBranch(entry) {
  return hasAssistantMessageContent(entry) || isAssistantArtifact(entry);
}

export function logIndexToMessagesIndex(logIndex, displayLog = []) {
  let count = 0;
  for (let i = 0; i < logIndex; i++) {
    const entry = displayLog[i];
    if (entry && (entry.type === 'message' || entry.type === 'tool_result')) count++;
  }
  return count;
}

export function assistantTurnStartIndex(logIndex, displayLog = []) {
  const cursor = Math.min(Math.max(logIndex - 1, 0), displayLog.length - 1);
  for (let i = cursor; i >= 0; i--) {
    if (isUserMessage(displayLog[i])) return i + 1;
  }
  return 0;
}

export function terminalAssistantTurnStartIndex(displayLog = []) {
  for (let i = displayLog.length - 1; i >= 0; i--) {
    if (isUserMessage(displayLog[i])) return i + 1;
  }
  return 0;
}

export function lastAssistantMessageIndex(displayLog = []) {
  for (let i = displayLog.length - 1; i >= 0; i--) {
    if (hasAssistantMessageContent(displayLog[i])) return i;
  }
  return -1;
}

export function assistantFooterHostIndex(displayLog = [], turnStart = terminalAssistantTurnStartIndex(displayLog)) {
  for (let i = displayLog.length - 1; i >= turnStart; i--) {
    if (hasAssistantMessageContent(displayLog[i])) return i;
  }

  for (let i = turnStart; i < displayLog.length; i++) {
    const entry = displayLog[i];
    if (canHostAssistantBranch(entry) && entry.branch?.kind === 'assistant') return i;
  }

  for (let i = displayLog.length - 1; i >= turnStart; i--) {
    if (canHostAssistantBranch(displayLog[i])) return i;
  }

  return -1;
}

export function branchHostIndex(segment = [], kind) {
  if (kind === 'user') return segment.findIndex(isUserMessage);

  // Assistant branch controls live in the assistant footer. A single assistant
  // turn can contain several display entries when tools are used, and history
  // rendering reuses one assistant row for that whole turn. Hosting the branch
  // on the first assistant message lets later tool/final-answer entries replace
  // the footer, so the branch arrows disappear after regenerate. Anchor the
  // branch to the same entry that owns the footer instead.
  return assistantFooterHostIndex(segment, 0);
}
