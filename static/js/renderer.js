// DOM rendering — the only module that touches #messages.
// API calls, persistence, and chat orchestration live elsewhere.
//
// Large sub-concerns are split into focused modules:
//   renderer_core.js        – scroll helpers and createMessageRow
//   renderer_groups.js      – block grouping, collapsible logic
//   renderer_thinking.js    – thinking block create/update/finalize
//   renderer_attachments.js – attachment cards and content parts
//   renderer_tools.js       – tool strip states (using/approval/running/result)
//   renderer_actions.js     – copy/edit/regenerate/branch footers

import { applyMarkdown } from './markdown.js';
import { createElement } from './dom.js';
import { ICONS } from './icons.js';
import { escapeHtml } from './format.js';

import { scrollToBottom as _scrollToBottom, messagesEl, createMessageRow, moveComposerToMain } from './renderer_core.js';
import { prepareAssistantRow } from './renderer_groups.js';
import { getRawText, appendContentParts, createAttachmentsGrid } from './renderer_attachments.js';
import { appendThinkingBlock } from './renderer_thinking.js';
import { appendToolResultInline } from './renderer_tools.js';
import { addUserFooter, addAssistantFooter } from './renderer_actions.js';
import { assistantFooterHostIndex } from './chat_log_utils.js';

export { createThinkingBlock, updateThinkingBlock, finalizeThinkingBlock } from './renderer_thinking.js';
export { createToolStrip, toolStripSetApproval, toolStripSetRunning, toolStripFinalize, toolStripSetStopped, cancelAllToolApprovals } from './renderer_tools.js';
export { refreshMessageFooter } from './renderer_actions.js';

// Wrap scrollToBottom with the same signature expected by callers
export function scrollToBottom(force = false) { _scrollToBottom(force); }

const SUGGESTION_CHIPS = [
  { icon: ICONS.chipCode,   label: 'Build',      prompt: 'Help me build a clean solution for' },
  { icon: ICONS.chipPencil, label: 'Draft',      prompt: 'Help me draft this clearly:' },
  { icon: ICONS.chipInfo,   label: 'Teach',      prompt: 'Explain this step by step:' },
  { icon: ICONS.chipBox,    label: 'Inspect',    prompt: 'Analyze this carefully:' },
  { icon: ICONS.chipHelp,   label: 'Ideas',      prompt: 'Brainstorm practical ideas for' },
];

function formatFolderChatDate(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const today = new Date();
  const sameYear = date.getFullYear() === today.getFullYear();
  return new Intl.DateTimeFormat(undefined, sameYear
    ? { month: 'short', day: 'numeric' }
    : { year: 'numeric', month: 'short', day: 'numeric' }
  ).format(date);
}

export function clearMessages({ folder = null, conversations = [] } = {}) {
  const messages = messagesEl();
  const composer = document.getElementById('composer-area');
  const main = document.getElementById('main');

  messages.innerHTML = folder ? `
    <div id="empty-state" class="folder-empty-state">
      <div class="folder-home-title">${ICONS.folder}<span>${escapeHtml(folder.name || 'Untitled Folder')}</span></div>
    </div>` : `
    <div id="empty-state">
      <div class="es-logo"><span>Lu</span><em>men</em></div>
      <div class="es-sub">Ask a question, explore an idea, or get help with a task.</div>
    </div>`;

  if (composer) messages.appendChild(composer);
  main?.classList.add('is-empty');
  main?.classList.toggle('folder-home', Boolean(folder));

  if (folder) {
    const history = document.createElement('section');
    history.id = 'folder-home-chats';
    history.innerHTML = `
      <div class="folder-home-tabs" role="tablist" aria-label="Folder content">
        <button class="folder-home-tab active" type="button" role="tab" aria-selected="true" data-folder-tab="chats">Chats</button>
        <button class="folder-home-tab" type="button" role="tab" aria-selected="false" data-folder-tab="instructions">Instructions</button>
      </div>
      <div class="folder-home-panel" role="tabpanel" data-folder-panel="chats">
        <div class="folder-home-list">
        ${conversations.length ? conversations.map(conv => `
          <div class="folder-home-chat" data-conv-id="${escapeHtml(conv.id)}">
            <button class="folder-home-chat-open" type="button">
              <span class="folder-home-chat-title">${escapeHtml(conv.title || 'Untitled')}</span>
              <time>${escapeHtml(formatFolderChatDate(conv.updated_at))}</time>
            </button>
            <div class="conv-menu-wrap">
              <button class="conv-menu-btn" type="button" title="Conversation options" aria-label="Conversation options" aria-expanded="false">
                ${ICONS.moreVertical}
              </button>
              <div class="conv-menu" role="menu">
                <button class="conv-menu-item" type="button" role="menuitem" data-action="rename">
                  ${ICONS.edit}<span>Rename</span>
                </button>
                <button class="conv-menu-item danger" type="button" role="menuitem" data-action="remove">
                  ${ICONS.trash}<span>Delete</span>
                </button>
              </div>
            </div>
          </div>`).join('') : '<div class="folder-home-no-chats">No chats in this folder yet</div>'}
        </div>
      </div>
      <div class="folder-home-panel folder-instructions-panel" role="tabpanel" data-folder-panel="instructions" hidden>
        <div class="folder-instructions-heading">
          <div class="section-title">Folder instructions</div>
          <p class="tool-card-desc">Guide how the assistant should respond in every chat in this folder.</p>
        </div>
        <textarea id="folder-instructions-input" class="msg-edit-textarea" aria-label="Folder instructions" placeholder="Be concise and cite workspace files.">${escapeHtml(folder.system_prompt || '')}</textarea>
        <div class="folder-instructions-actions">
          <span class="field-note">Overrides the global system prompt when it isn’t empty.</span>
          <span class="folder-instructions-status" role="status"></span>
          <button class="btn-secondary" type="button" data-save-folder-instructions>Save</button>
        </div>
      </div>`;
    const tabs = [...history.querySelectorAll('[data-folder-tab]')];
    const panels = [...history.querySelectorAll('[data-folder-panel]')];
    tabs.forEach(tab => tab.addEventListener('click', () => {
      tabs.forEach(item => {
        const active = item === tab;
        item.classList.toggle('active', active);
        item.setAttribute('aria-selected', String(active));
      });
      panels.forEach(panel => { panel.hidden = panel.dataset.folderPanel !== tab.dataset.folderTab; });
    }));
    history.querySelectorAll('.folder-home-chat').forEach(item => {
      const convId = item.dataset.convId;
      item.querySelector('.folder-home-chat-open').addEventListener('click', () => document.dispatchEvent(new CustomEvent(
        'chat:open-conversation-requested',
        { detail: { convId } },
      )));
      item.querySelector('.conv-menu-btn').addEventListener('click', event => {
        event.stopPropagation();
        document.dispatchEvent(new CustomEvent(
          'chat:toggle-conversation-menu-requested',
          { detail: { item } },
        ));
      });
      item.querySelector('[data-action="rename"]').addEventListener('click', event => {
        event.stopPropagation();
        item.classList.remove('menu-open');
        document.dispatchEvent(new CustomEvent(
          'chat:rename-conversation-requested',
          { detail: { convId } },
        ));
      });
      item.querySelector('[data-action="remove"]').addEventListener('click', event => {
        event.stopPropagation();
        item.classList.remove('menu-open');
        document.dispatchEvent(new CustomEvent(
          'chat:delete-conversation-requested',
          { detail: { convId } },
        ));
      });
    });
    const instructionsInput = history.querySelector('#folder-instructions-input');
    const saveInstructions = history.querySelector('[data-save-folder-instructions]');
    const instructionsStatus = history.querySelector('.folder-instructions-status');
    saveInstructions.addEventListener('click', () => {
      saveInstructions.disabled = true;
      instructionsStatus.textContent = 'Saving…';
      document.dispatchEvent(new CustomEvent('chat:update-folder-instructions-requested', {
        detail: {
          folderId: folder.id,
          systemPrompt: instructionsInput.value.trim(),
          done: error => {
            saveInstructions.disabled = false;
            instructionsStatus.textContent = error ? `Could not save: ${error}` : 'Saved';
            instructionsStatus.classList.toggle('error', Boolean(error));
          },
        },
      }));
    });
    messages.appendChild(history);
  }

  const suggestionsBar = document.getElementById('suggestions-bar');
  if (suggestionsBar) {
    suggestionsBar.innerHTML = SUGGESTION_CHIPS.map(chip => `
      <button class="suggestion-chip" data-prompt="${escapeHtml(chip.prompt)}">
        ${chip.icon}${escapeHtml(chip.label)}
      </button>`).join('');

    suggestionsBar.querySelectorAll('.suggestion-chip').forEach(btn => {
      btn.addEventListener('click', () => {
        const input = document.getElementById('user-input');
        if (input) {
          input.value = btn.dataset.prompt;
          input.focus();
          input.dispatchEvent(new Event('input'));
        }
      });
    });
  }
  const inputEl = document.getElementById('user-input');
  if (inputEl) {
    inputEl.placeholder = folder ? `New chat in ${folder.name}` : 'Ask anything…';
    inputEl.value = inputEl.value || '';
    inputEl.dispatchEvent(new Event('input'));
  }
}

export function appendMessage(role, content, logIndex = -1, entry = null) {
  if (!content) return null;

  const isUser = role === 'user';
  const row = isUser
    ? createMessageRow({ avatarClass: 'user-av', avatarIcon: ICONS.user, roleLabel: 'You', isUser: true })
    : prepareAssistantRow();

  if (logIndex >= 0) row.dataset.logIndex = logIndex;
  row.querySelector('.msg-footer')?.remove();

  const contentEl = createElement('div', { className: 'msg-content' });
  if (logIndex >= 0) contentEl.dataset.logIndex = String(logIndex);

  if (isUser) {
    const attachmentsEl = createAttachmentsGrid(content, { className: 'msg-attachments-grid--outside' });
    if (attachmentsEl) row.appendChild(attachmentsEl);

    appendContentParts(contentEl, content, { includeAttachments: false });
    if (attachmentsEl && !getRawText(content).trim()) contentEl.classList.add('msg-content-empty');
  } else {
    appendContentParts(contentEl, content);
  }

  row.appendChild(contentEl);

  if (isUser) {
    addUserFooter(row, () => getRawText(content), logIndex, () => content, entry?.branch);
  } else {
    addAssistantFooter(row, () => getRawText(content), logIndex, entry?.branch);
  }
  _scrollToBottom(isUser);
  return contentEl;
}

export function createStreamingMessage() {
  const row = prepareAssistantRow();
  const contentEl = createElement('div', { className: 'msg-content', html: '&nbsp;' });
  row.appendChild(contentEl);
  _scrollToBottom();
  return contentEl;
}

export function appendAssistantStatus(content = 'Response stopped.', logIndex = -1, entry = null) {
  const row = prepareAssistantRow();
  row.querySelector('.msg-footer')?.remove();
  if (logIndex >= 0) row.dataset.logIndex = String(logIndex);
  const statusEl = createElement('div', { className: 'msg-content msg-status', text: content });
  if (logIndex >= 0) statusEl.dataset.logIndex = String(logIndex);
  row.appendChild(statusEl);
  addAssistantFooter(row, () => content, logIndex, entry?.branch);
  _scrollToBottom();
  return statusEl;
}

export function finalizeStreamingMessage(contentEl, text, { logIndex = -1, branch = null } = {}) {
  if (!text || !text.trim()) {
    contentEl.remove();
    return;
  }

  contentEl.dataset.rawText = text;
  applyMarkdown(contentEl, text);

  const row = contentEl.parentElement;
  if (logIndex >= 0) row.dataset.logIndex = String(logIndex);
  row.querySelector('.msg-footer')?.remove();
  addAssistantFooter(row, () => text, logIndex, branch);
}

export function setStreamingMessageLogIndex(contentEl, logIndex, branch = null) {
  const row = contentEl?.parentElement;
  if (!row) return;
  row.dataset.logIndex = logIndex;
  const footerEl = row.querySelector('.msg-footer');
  if (!footerEl) return;
  const getText = () => contentEl?.dataset?.rawText || contentEl?.textContent || '';
  footerEl.remove();
  addAssistantFooter(row, getText, logIndex, branch);
}

function ensureTerminalAssistantFooter(displayLog) {
  const rows = [...messagesEl().querySelectorAll('.msg-row')];
  const row = rows.at(-1);
  if (!row || row.classList.contains('user-row') || row.querySelector('.msg-footer')) return;

  const getText = () => row.querySelector('.msg-content')?.dataset.rawText ||
    row.querySelector('.msg-content')?.textContent ||
    row.textContent.trim();
  const logIndex = assistantFooterHostIndex(displayLog);
  if (logIndex < 0) return;
  const branch = displayLog[logIndex]?.branch?.kind === 'assistant' ? displayLog[logIndex].branch : null;
  addAssistantFooter(row, getText, logIndex, branch);
}


export function renderAllMessages(displayLog) {
  moveComposerToMain();
  messagesEl().innerHTML = '';
  if (displayLog.length > 0) {
    document.getElementById('main')?.classList.remove('is-empty', 'folder-home');
    const input = document.getElementById('user-input');
    if (input) input.placeholder = 'Ask anything…';
  } else {
    clearMessages();
    return;
  }
  displayLog.forEach((entry, idx) => {
    if (entry.type === 'message') {
      if (entry.role === 'assistant' && !String(entry.content ?? '').trim()) return;
      appendMessage(entry.role, entry.content, idx, entry);
    }
    if (entry.type === 'tool_result') appendToolResultInline(entry.name, entry.args, entry.media?.length ? { text: entry.result, media: entry.media } : entry.result, entry.displayName, idx);
    if (entry.type === 'thinking') appendThinkingBlock(entry.content, idx);
    if (entry.type === 'status') appendAssistantStatus(entry.content, idx, entry);
  });
  ensureTerminalAssistantFooter(displayLog);
  _scrollToBottom(true);
}
