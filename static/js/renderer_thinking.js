// Thinking block creation, streaming updates, and finalization.

import { createElement, setVisible } from './dom.js';
import { ICONS } from './icons.js';
import { state } from './state.js';
import { escapeHtml } from './format.js';
import { scrollToBottom } from './renderer_core.js';
import { prepareAssistantRow, attachCollapsible, tryGroupBlock, updateGroupLabel, setWorkLogProcessing } from './renderer_groups.js';

function createThinkingMarkup({ label, chevron, body = '', streaming = false, display = 'none' }) {
  return `
    <button class="thinking-header">
      <span class="thinking-chevron">${chevron}</span>
      <span class="thinking-icon">${ICONS.bulb}</span>
      <span class="thinking-label">${label}</span>
      <span class="activity-state">${streaming ? 'Reasoning' : 'Notes'}</span>
    </button>
    <pre class="thinking-body" style="display:${display}">${body}</pre>`;
}

export function createThinkingBlock() {
  const row = prepareAssistantRow();
  const startOpen = !state.hideThinkingTokens;
  const block = createElement('div', {
    className: `thinking-block thinking-streaming${startOpen ? ' open' : ''}`,
    html: createThinkingMarkup({
      label:     'Thinking…',
      chevron:   startOpen ? ICONS.chevronDown : ICONS.chevronRight,
      streaming: true,
      display:   startOpen ? 'block' : 'none',
    }),
  });

  attachCollapsible(block, {
    headerSelector:  '.thinking-header',
    bodySelector:    '.thinking-body',
    chevronSelector: '.thinking-chevron',
    markManualToggle: true,
  });

  row.appendChild(block);
  tryGroupBlock(block);
  setWorkLogProcessing(true);
  scrollToBottom();
  return block.querySelector('.thinking-body');
}

export function updateThinkingBlock(bodyEl, text) {
  bodyEl.textContent = text;
  scrollToBottom();
}

export function finalizeThinkingBlock(bodyEl, fullText) {
  const block = bodyEl.closest('.thinking-block');
  if (!block) return;

  block.classList.remove('thinking-streaming');
  block.querySelector('.thinking-label').textContent = 'Thought process';
  block.querySelector('.activity-state').textContent = 'Notes';
  bodyEl.textContent = fullText;

  // Collapse when done unless the user manually toggled it open mid-stream.
  if (!block.dataset.manualToggle) {
    block.classList.remove('open');
    block.querySelector('.thinking-header').setAttribute('aria-expanded', 'false');
    setVisible(bodyEl, false);
  }

  updateGroupLabel(block.closest('.block-group'));
}

export function appendThinkingBlock(reasoningText, logIndex = -1) {
  if (!reasoningText) return;

  const row = prepareAssistantRow();
  const block = createElement('div', {
    className: 'thinking-block',
    html: createThinkingMarkup({
      label:   'Thought process',
      chevron: ICONS.chevronRight,
      body:    escapeHtml(reasoningText),
      display: 'none',
    }),
  });

  if (logIndex >= 0) block.dataset.logIndex = String(logIndex);

  attachCollapsible(block, {
    headerSelector:  '.thinking-header',
    bodySelector:    '.thinking-body',
    chevronSelector: '.thinking-chevron',
  });

  row.appendChild(block);
  tryGroupBlock(block);
  scrollToBottom();
}
