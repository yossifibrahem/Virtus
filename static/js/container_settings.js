// Container settings — environment-variable-backed container and file handling configuration exposed via the UI.
//
// Each setting maps to a backend env var.  When the server reports that a key
// is "env_locked" (i.e. the operator set the env var), the corresponding field
// is disabled and a lock badge is shown so users understand why they can't edit
// it through the UI.

import { api }            from './api.js';
import { refreshFilePanel } from './file_panel.js';

// ── Internal state ────────────────────────────────────────────────────────────

let _envLocked = {};   // key → true when the env var was set at server start

// ── Load ──────────────────────────────────────────────────────────────────────

export async function loadContainerSettings() {
  try {
    const data = await api.get('/api/container-settings');
    if (!data || data.error) return;
    _envLocked = {};
    for (const key of _KEYS) {
      _envLocked[key] = !!data[`${key}_env_locked`];
    }
    _syncUI(data);
  } catch { /* network failure — leave form at defaults */ }
}

// ── Save ──────────────────────────────────────────────────────────────────────

export async function saveContainerSettings() {
  const payload = _readControls();
  const data = await api.post('/api/container-settings', payload);
  if (data?.error) throw new Error(data.error);
  _syncUI(data);
  refreshFilePanel({ keepPreview: true }).catch(() => {});
}

// ── Key list ──────────────────────────────────────────────────────────────────

const _KEYS = [
  'sandbox_image',
  'container_memory',
  'container_cpus',
  'container_network',
  'container_idle_timeout',
  'max_file_preview_bytes',
  'max_file_list_entries',
  'max_upload_bytes',
];

// ── UI sync helpers ───────────────────────────────────────────────────────────

function _el(id) { return document.getElementById(id); }

function _syncUI(data) {
  for (const key of _KEYS) {
    const el = _el(`adv-${key}`);
    if (!el) continue;
    el.value   = data[key] ?? '';
    el.disabled = !!_envLocked[key];
    _updateLockBadge(key);
  }
}

function _updateLockBadge(key) {
  const badge = _el(`adv-lock-${key}`);
  if (!badge) return;
  if (_envLocked[key]) {
    badge.hidden = false;
    badge.title  = `Locked by ${_ENV_NAMES[key]}`;
  } else {
    badge.hidden = true;
  }
}

const _ENV_NAMES = {
  sandbox_image:          'LUMEN_SANDBOX_IMAGE',
  container_memory:       'LUMEN_CONTAINER_MEMORY',
  container_cpus:         'LUMEN_CONTAINER_CPUS',
  container_network:      'LUMEN_CONTAINER_NETWORK',
  container_idle_timeout: 'LUMEN_CONTAINER_IDLE_TIMEOUT',
  max_file_preview_bytes: 'LUMEN_MAX_FILE_PREVIEW_BYTES',
  max_file_list_entries:  'LUMEN_MAX_FILE_LIST_ENTRIES',
  max_upload_bytes:       'LUMEN_MAX_UPLOAD_BYTES',
};

// ── Read controls ─────────────────────────────────────────────────────────────

function _readControls() {
  const payload = {};
  for (const key of _KEYS) {
    if (_envLocked[key]) continue;   // skip — server will ignore anyway
    const el = _el(`adv-${key}`);
    if (!el) continue;
    payload[key] = el.value.trim();
  }
  return payload;
}

// ── Danger zone ───────────────────────────────────────────────────────────────

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