import { createElement } from './dom.js';
import { ICONS } from './icons.js';
import { state } from './state.js';
import { messagesEl } from './renderer_core.js';
import { getRawText, normalizeContentAttachments, createAttachmentsGrid } from './renderer_attachments.js';

function createMessageAction(icon, onClick) {
  const btn = createElement('button', { className: 'msg-action-btn', html: `${icon}` });
  btn.addEventListener('click', onClick);
  return btn;
}

function createCopyAction(getText) {
  const btn = createMessageAction(ICONS.copy, () => {
    navigator.clipboard.writeText(getText());
    btn.innerHTML = ICONS.check;
    setTimeout(() => { btn.innerHTML = ICONS.copy; }, 1500);
  });
  return btn;
}

function createBranchActions(branch, logIndex) {
  if (!branch || !Array.isArray(branch.variants) || branch.variants.length < 2 || logIndex < 0) return [];

  const active = Number.isInteger(branch.active) ? branch.active : 0;
  const count = branch.variants.length;
  const switchTo = index => {
    const nextIndex = (index + count) % count;
    messagesEl().dispatchEvent(new CustomEvent('chat:switch-branch', {
      bubbles: true,
      detail: { logIndex, variantIndex: nextIndex },
    }));
  };

  const label = createElement('span', {
    className: 'msg-branch-label',
    text: `${active + 1}/${count}`,
  });

  return [
    createMessageAction(ICONS.chevronLeft, () => switchTo(active - 1)),
    label,
    createMessageAction(ICONS.chevronRight, () => switchTo(active + 1)),
  ];
}

function addMessageFooter(row, actions = []) {
  row.querySelector('.msg-footer')?.remove();
  const footer = createElement('div', { className: 'msg-footer' });
  actions.forEach(action => footer.appendChild(action));
  row.appendChild(footer);
}

export function addUserFooter(row, getText, logIndex, getContent = () => null, branch = null) {
  addMessageFooter(row, [
    createCopyAction(getText),
    createMessageAction(ICONS.edit, () => {
      if (logIndex < 0) return;
      startInlineEdit(row, logIndex, getText(), getContent(), branch);
    }),
    ...createBranchActions(branch, logIndex),
  ]);
}

export function addAssistantFooter(row, getText, logIndex, branch = null) {
  const actions = [createCopyAction(getText)];

  if (logIndex >= 0) {
    actions.push(createMessageAction(ICONS.refresh, () => {
      const currentLogIndex = Number(row.dataset.logIndex);
      const targetLogIndex = Number.isInteger(currentLogIndex) ? currentLogIndex : logIndex;
      row.dispatchEvent(new CustomEvent('chat:regenerate', { bubbles: true, detail: { logIndex: targetLogIndex } }));
    }));
  }

  actions.push(...createBranchActions(branch, logIndex));
  addMessageFooter(row, actions);
}

function startInlineEdit(row, logIndex, currentText, currentContent = null, branch = null) {
  const contentEl = row.querySelector('.msg-content');
  const footerEl  = row.querySelector('.msg-footer');
  const outsideAttachmentsEl = row.querySelector(':scope > .msg-attachments-grid--outside');
  if (!contentEl) return;

  const preservedContent = currentContent && typeof currentContent === 'object' && !Array.isArray(currentContent)
    ? currentContent
    : {};
  const attachments = normalizeContentAttachments(preservedContent);
  const imageUrls = attachments.filter(entry => entry.kind === 'image').map(entry => entry.url).filter(Boolean);
  const files = attachments.filter(entry => entry.kind === 'file');

  contentEl.style.display = 'none';
  if (outsideAttachmentsEl) outsideAttachmentsEl.style.display = 'none';
  footerEl?.remove();

  const editWrap = createElement('div', { className: 'msg-edit-wrap' });
  const textarea = createElement('textarea', { className: 'msg-edit-textarea' });
  textarea.value = currentText;
  textarea.rows = Math.min(Math.max(currentText.split('\n').length, 2), 10);

  const actions = createElement('div', { className: 'msg-edit-actions' });
  const saveBtn   = createElement('button', { className: 'msg-edit-save',   text: 'Send' });
  const cancelBtn = createElement('button', { className: 'msg-edit-cancel', text: 'Cancel' });

  const cancelEdit = () => {
    editWrap.remove();
    contentEl.style.display = '';
    if (outsideAttachmentsEl) outsideAttachmentsEl.style.display = '';
    addUserFooter(row, () => currentText, logIndex, () => currentContent, branch);
  };

  saveBtn.addEventListener('click', () => {
    const newText = textarea.value.trim();
    if (!newText && !attachments.length) return;
    editWrap.remove();
    contentEl.style.display = '';
    if (outsideAttachmentsEl) outsideAttachmentsEl.style.display = '';
    row.dispatchEvent(new CustomEvent('chat:edit-resend', { bubbles: true, detail: { logIndex, newText, imageUrls, files, attachments } }));
  });

  cancelBtn.addEventListener('click', cancelEdit);

  textarea.addEventListener('keydown', e => {
    if (e.isComposing) return;
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); saveBtn.click(); }
    if (e.key === 'Escape') cancelEdit();
  });

  actions.appendChild(cancelBtn);
  actions.appendChild(saveBtn);

  if (attachments.length) {
    const attachmentStrip = createAttachmentsGrid(preservedContent, { className: 'msg-edit-attachments', edit: true });
    if (attachmentStrip) editWrap.appendChild(attachmentStrip);
  }

  editWrap.appendChild(textarea);
  editWrap.appendChild(actions);
  row.appendChild(editWrap);

  setTimeout(() => {
    textarea.style.height = 'auto';
    textarea.style.height = textarea.scrollHeight + 'px';
    textarea.focus();
    textarea.setSelectionRange(textarea.value.length, textarea.value.length);
  }, 0);
}

function rowForLogIndex(logIndex) {
  const root = messagesEl();
  const rows = Array.from(root.querySelectorAll('.msg-row'));
  return rows.find(row => Number(row.dataset.logIndex) === logIndex) ||
    root.querySelector(`[data-log-index="${logIndex}"]`)?.closest('.msg-row') ||
    null;
}

function assistantRowText(row) {
  return row?.querySelector('.msg-content')?.dataset.rawText ||
    row?.querySelector('.msg-content')?.textContent ||
    row?.textContent?.trim() ||
    '';
}

function isAssistantFooterHost(entry) {
  return entry?.role === 'assistant' ||
    entry?.type === 'thinking' ||
    entry?.type === 'tool_result' ||
    entry?.type === 'status';
}

export function refreshMessageFooter(logIndex) {
  if (!Number.isInteger(logIndex) || logIndex < 0) return;

  const entry = state.displayLog[logIndex];
  const row = entry ? rowForLogIndex(logIndex) : null;
  if (!entry || !row) return;

  if (entry.type === 'message' && entry.role === 'user') {
    addUserFooter(row, () => getRawText(entry.content), logIndex, () => entry.content, entry.branch);
    return;
  }

  if (isAssistantFooterHost(entry)) {
    const branch = entry.branch?.kind === 'assistant' ? entry.branch : null;
    const getText = entry.type === 'message'
      ? () => getRawText(entry.content)
      : () => assistantRowText(row);
    addAssistantFooter(row, getText, logIndex, branch);
  }
}
