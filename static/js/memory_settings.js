// Global memory controls in Chat settings.
import { api } from './api.js';
import { showToast } from './ui.js';
import { escapeHtml } from './format.js';
import { ICONS } from './icons.js';

function _el(id) { return document.getElementById(id); }

let revision = '', entries = [], editing = null;
let saving = false, loaded = false, oversized = false, needsReload = false;
let clearTimer, loadVersion = 0;

document.addEventListener('memory:updated', () => {
  if (editing === null && !saving) void loadMemorySettings();
});

function resetClear() {
  clearTimeout(clearTimer);
  const button = _el('memory-clear');
  delete button.dataset.armed;
  button.textContent = 'Clear all';
}

function syncControls() {
  const disabled = !loaded || saving;
  _el('memory-enabled').disabled = disabled;
  _el('memory-block-editor').disabled = disabled || oversized;
  _el('memory-add').disabled = editing !== null;
  _el('memory-clear').disabled = editing !== null || !entries.length;
  for (const button of _el('memory-blocks').querySelectorAll('.memory-icon')) {
    button.disabled = editing !== null;
  }
}

function acceptMemory(data) {
  revision = data.revision;
  entries = data.content.split(/\n[ \t]*\n|\n(?=[ \t]*(?:[-*+]\s|\d+[.)]\s))/u)
    .filter(entry => entry.trim());
  oversized = !!data.oversized;
  needsReload = false;
  editing = null;
  _el('memory-enabled').checked = data.enabled;
  renderMemory();
}

function memoryIcon(action) {
  const label = `${action === 'edit' ? 'Edit' : 'Delete'} memory`;
  return `<button type="button" class="memory-icon ${action === 'delete' ? 'memory-delete' : ''}" data-action="${action}" title="${label}" aria-label="${label}">${ICONS[action === 'edit' ? 'edit' : 'trash']}</button>`;
}

function beginEdit(index) {
  if (saving || editing !== null) return;
  resetClear();
  editing = index;
  renderMemory();
  _el('memory-entry-input').focus();
}

function fitEditor(editor) {
  editor.style.height = 'auto';
  editor.style.height = `${editor.scrollHeight + editor.offsetHeight - editor.clientHeight}px`;
}

async function saveEntries(entries) {
  if (await saveMemory({content: entries.join('\n\n'), revision})) _el('memory-add').focus();
}

function renderMemory() {
  const list = _el('memory-blocks');
  const rows = editing === -1 ? [...entries, ''] : entries;
  list.innerHTML = rows.map((entry, i) => {
    const index = i === entries.length ? -1 : i;
    const body = editing === index ? `
      <form class="memory-entry-editor field">
        <textarea id="memory-entry-input" rows="3" placeholder="What would you like to remember?" aria-label="${index === -1 ? 'New memory' : 'Edit memory'}"></textarea>
        <div class="memory-edit-actions">
          <button type="button" class="btn-secondary" data-action="cancel">Cancel</button>
          <button type="submit" class="btn-primary">${index === -1 ? 'Add' : 'Save'}</button>
        </div>
      </form>` : `
      <p class="memory-text">${escapeHtml(entry.trim().replace(/^(?:[-*+]\s+|\d+[.)]\s+)/u, ''))}</p>
      <div class="memory-row-actions">
        ${memoryIcon('edit')}${memoryIcon('delete')}
      </div>`;
    return `<div class="memory-row" data-index="${index}">${body}</div>`;
  }).join('') || '<p class="memory-empty">No saved memories.</p>';

  const editor = _el('memory-entry-input');
  if (editor) {
    const form = editor.form;
    const submit = form.querySelector('[type="submit"]');
    editor.value = editing === -1 ? '' : entries[editing];
    editor.oninput = () => {
      fitEditor(editor);
      submit.disabled = !editor.value.trim() || needsReload;
    };
    editor.onkeydown = event => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      form.querySelector('[data-action="cancel"]').click();
    };
    form.onsubmit = async event => {
      event.preventDefault();
      if (!editor.value.trim() || needsReload) return;
      const updated = [...entries];
      updated.splice(editing === -1 ? updated.length : editing, editing === -1 ? 0 : 1, editor.value.trim());
      await saveEntries(updated);
      if (needsReload) submit.disabled = true;
    };
    editor.oninput();
  }
  syncControls();
}

async function saveMemory(payload) {
  if (saving || !loaded) return false;
  if ('content' in payload && new TextEncoder().encode(payload.content).length > 32768) {
    showToast('Memory is full. Shorten or delete an entry.');
    return false;
  }
  saving = true;
  syncControls();
  try {
    const data = await api.put('/api/memory', payload);
    if (data.error) {
      if (data.revision) {
        if (editing !== null) {
          needsReload = true;
          throw new Error('Memory changed in another chat. Copy your edit, then cancel to reload.');
        }
        await loadMemorySettings();
      }
      throw new Error(data.error);
    }
    if ('content' in payload) acceptMemory(data);
    showToast('content' in payload ? 'Memory updated' : payload.enabled ? 'Memory enabled' : 'Memory disabled');
    return true;
  } catch (error) {
    showToast(`Could not save memory: ${error.message}`);
    return false;
  } finally {
    saving = false;
    syncControls();
  }
}

export async function loadMemorySettings() {
  const version = ++loadVersion;
  resetClear();
  loaded = false;
  syncControls();
  try {
    const data = await api.get('/api/memory');
    if (version !== loadVersion) return;
    if (data.error) throw new Error(data.error);
    loaded = true;
    acceptMemory(data);
    if (data.oversized) showToast('Memory exceeds 32 KiB. Reduce the file before editing.');
  } catch (error) {
    if (version === loadVersion) showToast(`Could not load memory: ${error.message}`);
  }
}

_el('memory-blocks').onclick = async event => {
  const button = event.target.closest('[data-action]');
  if (!button || button.disabled || saving) return;
  const index = Number(button.closest('[data-index]').dataset.index);
  switch (button.dataset.action) {
    case 'edit': beginEdit(index); break;
    case 'delete':
      resetClear();
      await saveEntries(entries.filter((_, i) => i !== index));
      break;
    case 'cancel':
      editing = null;
      if (needsReload) await loadMemorySettings();
      else renderMemory();
      _el('memory-add').focus();
      break;
  }
};
_el('memory-add').onclick = () => beginEdit(-1);
_el('memory-enabled').onchange = async () => {
  const enabled = _el('memory-enabled').checked;
  if (!await saveMemory({enabled})) _el('memory-enabled').checked = !enabled;
};
_el('memory-clear').onclick = async () => {
  const button = _el('memory-clear');
  if (button.disabled) return;
  if (!button.dataset.armed) {
    button.dataset.armed = '1';
    button.textContent = 'Confirm clear';
    clearTimer = setTimeout(resetClear, 5000);
    return;
  }
  resetClear();
  await saveMemory({content: '', revision});
};
