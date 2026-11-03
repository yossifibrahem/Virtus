// Block grouping and collapsible helpers — thinking blocks and tool strips
// are collapsed into expandable groups when they appear sequentially.

import { createElement, setVisible } from './dom.js';
import { ICONS } from './icons.js';
import { state } from './state.js';
import { messagesEl, createMessageRow } from './renderer_core.js';

export function attachCollapsible(block, { headerSelector, bodySelector, chevronSelector, markManualToggle = false }) {
  const header = block.querySelector(headerSelector);
  const body = block.querySelector(bodySelector);
  const chevron = block.querySelector(chevronSelector);

  header?.setAttribute('aria-expanded', String(block.classList.contains('open')));
  header?.setAttribute('type', 'button');
  if (chevron) chevron.innerHTML = ICONS.chevronRight;

  header?.addEventListener('click', () => {
    if (markManualToggle) block.dataset.manualToggle = '1';
    const isOpen = block.classList.toggle('open');
    header.setAttribute('aria-expanded', String(isOpen));
    setVisible(body, isOpen);
  });
}

export function getOrCreateAssistantRow() {
  const rows = [...messagesEl().children].filter(child => child.classList.contains('msg-row'));
  const last = rows.at(-1);
  return last && !last.classList.contains('user-row')
    ? last
    : createMessageRow({ avatarClass: 'ai-av', avatarIcon: ICONS.ai, roleLabel: 'Assistant' });
}

export function prepareAssistantRow() {
  const row = getOrCreateAssistantRow();
  row.querySelector('.msg-footer')?.remove();
  return row;
}

export function isGroupableBlock(el) {
  return !!el && (
    el.classList.contains('thinking-block') ||
    el.classList.contains('tool-strip')
  );
}

function getBlockLabel(el) {
  if (!el) return '';
  if (el.classList.contains('thinking-block')) {
    return el.querySelector('.thinking-label')?.textContent?.trim() || 'Thinking';
  }
  if (!el.classList.contains('tool-strip')) return '';
  const name = el.dataset.displayName || el.dataset.toolName || 'tool';
  if (el.classList.contains('tool-strip-using')) return name;
  if (el.classList.contains('tool-strip-running')) return name;
  if (el.classList.contains('tool-strip-approval')) return name;
  return el.querySelector('.tr-tool-name')?.textContent?.trim() || name;
}

function isBlockActive(el) {
  if (el.classList.contains('thinking-block')) return el.classList.contains('thinking-streaming');
  if (el.classList.contains('tool-strip')) {
    return el.classList.contains('tool-strip-using') ||
           el.classList.contains('tool-strip-running') ||
           el.classList.contains('tool-strip-approval');
  }
  return false;
}

let activitySequence = 0;

export function updateGroupLabel(group) {
  if (!group) return;
  const elements = [...group.querySelector('.group-body').children].filter(isGroupableBlock);
  const activeBlock = group.dataset.finished === 'true' ? null : elements.findLast(isBlockActive);
  const processing = group.dataset.processing === 'true';
  const thinking = activeBlock?.classList.contains('thinking-block');
  group.hidden = elements.length === 0 && !processing;
  const busy = !!activeBlock || processing;
  group.classList.toggle('group-active', busy);
  group.querySelector('.group-label').textContent = activeBlock
    ? (thinking ? 'Thinking' : getBlockLabel(activeBlock))
    : (processing ? 'Working' : 'Work log');
  const emblem = group.querySelector('.group-emblem');
  // Keep the same spinner node across activity changes so its motion stays steady.
  const iconState = busy ? 'busy' : 'idle';
  if (emblem && emblem.dataset.state !== iconState) {
    emblem.dataset.state = iconState;
    emblem.innerHTML = busy ? '<span class="group-progress"></span>' : ICONS.layers;
  }

}

// Only the latest log in the visible assistant turn represents ongoing work.
// Replay uses finalized blocks and never sets this transient state.
export function setWorkLogProcessing(processing) {
  const messages = messagesEl();
  if (!messages) return;
  const row = [...messages.children].filter(el => el.classList.contains('msg-row')).at(-1);
  if (!row || row.classList.contains('user-row')) {
    if (processing) {
      const assistant = prepareAssistantRow();
      const group = createGroupBlock();
      assistant.appendChild(group);
      group.dataset.processing = 'true';
      updateGroupLabel(group);
    }
    return;
  }
  let groups = [...row.querySelectorAll('.block-group')];
  if (processing && !groups.length) {
    const group = createGroupBlock();
    row.appendChild(group);
    groups = [group];
  }
  groups.forEach((group, index) => {
    const current = processing && index === groups.length - 1;
    group.dataset.processing = String(current);
    group.dataset.finished = String(!current);
    updateGroupLabel(group);
  });
}

function createGroupBlock() {
  const expanded = !state.collapseSequentialBlocks;
  const group = createElement('div', { className: `block-group${expanded ? ' open' : ''}` });
  group.id = `activity-${++activitySequence}`;
  group.innerHTML = `
    <button class="group-header" type="button" aria-expanded="${expanded}" aria-controls="${group.id}-panel">
      <span class="group-emblem" aria-hidden="true">${ICONS.layers}</span>
      <span class="group-heading"><span class="group-label" aria-live="polite" aria-atomic="true">Work log</span></span>
      <span class="group-chevron" aria-hidden="true">${ICONS.chevronRight}</span>
    </button>
    <div id="${group.id}-panel" class="activity-workspace" ${expanded ? '' : 'hidden'}>
      <div class="group-body"></div>
    </div>`;
  group.querySelector('.group-header').addEventListener('click', () => {
    const open = group.classList.toggle('open');
    group.querySelector('.group-header').setAttribute('aria-expanded', String(open));
    group.querySelector('.activity-workspace').hidden = !open;
  });
  return group;
}

function previousBlockSibling(el) {
  let prev = el.previousElementSibling;
  while (prev?.classList.contains('msg-content') && !prev.textContent.trim()) {
    prev = prev.previousElementSibling;
  }
  return prev;
}

export function tryGroupBlock(el) {
  if (!isGroupableBlock(el)) return;

  const row = el.parentElement;
  if (!row) return;

  if (row.classList.contains('group-body')) {
    updateGroupLabel(row.closest('.block-group'));
    return;
  }

  const prev = previousBlockSibling(el);

  if (prev?.classList.contains('block-group')) {
    prev.querySelector('.group-body')?.appendChild(el);
    updateGroupLabel(prev);
  } else if (prev && isGroupableBlock(prev)) {
    const group = createGroupBlock();
    row.insertBefore(group, prev);
    const body = group.querySelector('.group-body');
    body.appendChild(prev);
    body.appendChild(el);
    updateGroupLabel(group);
  } else {
    const group = createGroupBlock();
    row.insertBefore(group, el);
    group.querySelector('.group-body')?.appendChild(el);
    updateGroupLabel(group);
  }
}