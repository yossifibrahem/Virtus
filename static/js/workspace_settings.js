// Workspace data actions and global memory. Runtime configuration uses server defaults.
import { api } from './api.js';
import { showToast } from './ui.js';

function _el(id) { return document.getElementById(id); }

export async function loadMemorySettings() {
  await loadMemory();
}

export async function deleteAllData() {
  try {
    const data = await api.post('/api/danger/delete-all', {});
    if (data?.ok) {
      return { ok: true, deleted: data.deleted ?? 0 };
    }
    return { ok: false, error: data?.error || data?.errors?.join('; ') || 'Unknown error' };
  } catch (err) {
    return { ok: false, error: err.message };
  }
}

let memoryRevision = '';
let memoryBound = false;
let memoryClearTimer;
let memoryLoadVersion = 0;
document.addEventListener('memory:updated', () => { void loadMemory(); });
function resetMemoryClear() {
  clearTimeout(memoryClearTimer);
  const button = _el('memory-clear');
  delete button.dataset.armed;
  button.textContent = 'Clear memory';
}
async function loadMemory() {
  const version = ++memoryLoadVersion;
  resetMemoryClear();
  for (const id of ['memory-content', 'memory-enabled', 'memory-save', 'memory-clear']) _el(id).disabled = true;
  try {
    const data = await api.get('/api/memory');
    if (version !== memoryLoadVersion) return;
    if (data.error) throw new Error(data.error);
    memoryRevision = data.revision;
    _el('memory-enabled').disabled = false;
    for (const id of ['memory-content', 'memory-save', 'memory-clear']) _el(id).disabled = !!data.oversized;
    _el('memory-content').value = data.content;
    _el('memory-enabled').checked = data.enabled;
    if (data.oversized) showToast('Memory exceeds 32 KiB. Reduce the file before editing.');
  } catch (error) {
    if (version === memoryLoadVersion) showToast(`Could not load memory: ${error.message}`);
  }
  if (memoryBound) return;
  memoryBound = true;
  const save = async (payload, reload = false) => {
    try {
      const data = await api.put('/api/memory', payload);
      if (data.error) {
        if (data.revision) await loadMemory();
        throw new Error(data.error);
      }
      if ('content' in payload) memoryRevision = data.revision;
      showToast(reload ? 'Memory cleared' : 'content' in payload ? 'Memory saved' : payload.enabled ? 'Memory enabled' : 'Memory disabled');
      if (reload) await loadMemory();
      return true;
    } catch (error) {
      showToast(`Could not save memory: ${error.message}`);
      return false;
    }
  };
  _el('memory-save').onclick = () => save({content: _el('memory-content').value, revision: memoryRevision});
  _el('memory-enabled').onchange = () => save({enabled: _el('memory-enabled').checked});
  _el('memory-clear').onclick = async () => {
    const button = _el('memory-clear');
    if (button.disabled) return;
    if (!button.dataset.armed) {
      button.dataset.armed = '1';
      button.textContent = 'Click again to confirm';
      showToast('Click again to permanently clear saved memory.');
      memoryClearTimer = setTimeout(resetMemoryClear, 5000);
      return;
    }
    resetMemoryClear();
    button.disabled = true;
    button.textContent = 'Clearing…';
    const cleared = await save({content: '', revision: memoryRevision}, true);
    button.textContent = 'Clear memory';
    if (!cleared) button.disabled = false;
  };
}
