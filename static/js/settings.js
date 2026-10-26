// Settings — API + Chat behaviour settings persisted to localStorage.

import { state, SETTINGS_DEFAULTS, STORAGE_KEYS } from './state.js';
import { storage } from './storage.js';
import { api }     from './api.js';

// ── Read / write ──────────────────────────────────────────────────────────────

const SETTINGS_KEYS = Object.keys(SETTINGS_DEFAULTS);
let _draftModel = SETTINGS_DEFAULTS.model;

export function loadSettings() {
  Object.assign(state, SETTINGS_DEFAULTS, _savedSettings());

  const cachedModels = storage.get(STORAGE_KEYS.models);
  renderModelList(cachedModels || []);

  _draftModel = state.model;
  _syncAPIUI();
  _loadServerApiSettings();
  _syncChatUI();
  updateModelBadge();
  updateInputHint();
}

export async function saveSettings() {
  _readAPIControls();
  state.model = _draftModel;
  _persistSettings();
  const saved = await _saveServerApiSettings();
  updateModelBadge();
  if (saved?.error) {
    throw new Error(`API config failed: ${saved.error}`);
  }
}

export function saveChatSettings() {
  _readChatControls();
  _persistSettings();
  updateInputHint();
}

export function syncSettingsUI() {
  _draftModel = state.model;
  _syncAPIUI();
  _syncChatUI();
  renderModelList(storage.get(STORAGE_KEYS.models) || []);
}

export function updateInputHint() {
  const hint = document.querySelector('.input-hint');
  if (!hint) return;
  hint.textContent = state.enterToSend ? '⏎ send · ⇧⏎ newline' : '⏎ newline · ⇧⏎ send';
}

function _savedSettings() {
  const saved = storage.get(STORAGE_KEYS.settings, {});
  return Object.fromEntries(SETTINGS_KEYS.map(key => [key, saved[key]]).filter(([, v]) => v !== undefined));
}

function _persistSettings() {
  storage.set(STORAGE_KEYS.settings, Object.fromEntries(SETTINGS_KEYS.map(key => [key, state[key]])));
}

async function _loadServerApiSettings() {
  try {
    const data = await api.get('/api/settings');
    if (data?.api_base) state.apiBase = data.api_base;
    state.serverHasApiKey = !!data?.has_api_key;
    _syncAPIUI();
  } catch {}
}

async function _saveServerApiSettings() {
  const keyInput = document.getElementById('api-key');
  const data = await api.post('/api/settings', {
    api_base: state.apiBase,
    api_key: keyInput?.value.trim() || '',
  });
  if (!data.error) {
    if (data.api_base) state.apiBase = data.api_base;
    state.serverHasApiKey = !!data.has_api_key;
    if (keyInput) {
      keyInput.value = '';
      keyInput.placeholder = state.serverHasApiKey ? 'Saved on server — leave blank to keep' : 'sk-…';
    }
    _persistSettings();
  }
  return data;
}

// ── Model list ────────────────────────────────────────────────────────────────

let _modelFetchId = 0;

export async function fetchModels() {
  const fetchId = ++_modelFetchId;
  const btn = document.getElementById('btn-fetch-models');
  if (btn) {
    btn.disabled = true;
    btn.classList.add('loading');
    btn.title = 'Loading models…';
    btn.setAttribute('aria-label', 'Loading models');
  }

  storage.set(STORAGE_KEYS.models, []);
  renderModelList([]);
  _setModelStatus('Loading models…', 'Checking the API connection.', 'ok');

  try {
    const data = await api.post('/api/models', {});
    if (fetchId !== _modelFetchId) return;

    if (data.error) {
      _setModelStatus('Could not load models', data.error, 'err');
      return;
    }

    const models = data.models || [];
    storage.set(STORAGE_KEYS.models, models);
    renderModelList(models);
    _setModelStatus(models.length ? 'Models loaded' : 'No models found', '', models.length ? 'ok' : 'err');
  } catch (err) {
    if (fetchId === _modelFetchId) _setModelStatus('Could not load models', err.message, 'err');
  } finally {
    if (fetchId === _modelFetchId && btn) {
      btn.disabled = false;
      btn.classList.remove('loading');
      btn.title = 'Reload models';
      btn.setAttribute('aria-label', 'Reload models');
    }
  }
}

export function renderModelList(models = []) {
  _renderModelRows(document.getElementById('model-list'), models);
  _renderChips(document.getElementById('mp-model-list'), 'mp-chip', models);
}

function _renderModelRows(container, models) {
  if (!container) return;
  if (!models.length) {
    container.innerHTML = '<div class="model-empty">No models found</div>';
    return;
  }

  container.innerHTML = models.map(m =>
    `<button class="model-row${m === _draftModel ? ' selected' : ''}" data-model="${_escape(m)}" title="${_escape(m)}"><span>${_escape(m)}</span></button>`
  ).join('');

  container.querySelectorAll('.model-row').forEach(row => {
    row.addEventListener('click', () => _chooseModel(row.dataset.model, models));
  });
}

function _renderChips(container, chipClass, models) {
  if (!container) return;
  if (!models.length) {
    container.innerHTML = '<span class="mp-empty">No models — fetch them in API settings</span>';
    return;
  }
  container.innerHTML = models.map(m =>
    `<button type="button" class="${chipClass}${m === state.model ? ' selected' : ''}" aria-pressed="${m === state.model}" data-model="${_escape(m)}" title="${_escape(m)}">${_escape(m)}</button>`
  ).join('');

  container.querySelectorAll(`.${chipClass}`).forEach(chip => {
    chip.addEventListener('click', () => _chooseModel(chip.dataset.model, models, true));
  });
}

function _chooseModel(model, models, commit = false) {
  _draftModel = model;
  if (commit) {
    state.model = model;
    _persistSettings();
    updateModelBadge();
  }
  renderModelList(models);
}

function _setModelStatus(title, detail, type) {
  const el = document.getElementById('settings-status');
  if (!el) return;
  el.className = `status-msg ${type}`;
  el.innerHTML = `<strong>${_escape(title)}</strong>${detail ? `<span>${_escape(detail)}</span>` : ''}`;
  el.style.display = 'block';
}

function _escape(value) {
  return String(value).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function updateModelBadge() {
  document.getElementById('model-badge-label').textContent = state.model || 'No model';
}

// ── Show/hide API key ─────────────────────────────────────────────────────────

export function initKeyToggle() {
  const btn   = document.getElementById('btn-toggle-key');
  const input = document.getElementById('api-key');
  const show  = document.getElementById('eye-icon-show');
  const hide  = document.getElementById('eye-icon-hide');
  if (!btn || !input) return;

  btn.addEventListener('click', () => {
    const isHidden = input.type === 'password';
    input.type  = isHidden ? 'text' : 'password';
    // Spans use data-icon and were hydrated by initIcons(); just toggle visibility
    if (show) show.style.display = isHidden ? 'none' : '';
    if (hide) hide.style.display = isHidden ? '' : 'none';
  });
}

// ── Temperature slider live badge ─────────────────────────────────────────────

export function initParameterSliders() {
  const tempSlider  = document.getElementById('setting-temperature');
  const tempBadge   = document.getElementById('temp-badge');
  if (tempSlider && tempBadge) {
    tempSlider.addEventListener('input', () => {
      tempBadge.textContent = parseFloat(tempSlider.value).toFixed(2);
    });
  }
}

// ── Private helpers ───────────────────────────────────────────────────────────

function _syncAPIUI() {
  const el = id => document.getElementById(id);
  el('api-base').value = state.apiBase;
  const keyInput = el('api-key');
  if (keyInput) {
    keyInput.value = '';
    keyInput.placeholder = state.serverHasApiKey ? 'Saved on server — leave blank to keep' : 'sk-…';
  }

  const temp = el('setting-temperature');
  if (temp) { temp.value = state.temperature; }
  const tempBadge = el('temp-badge');
  if (tempBadge) tempBadge.textContent = parseFloat(state.temperature).toFixed(2);
}

function _syncChatUI() {
  const el = id => document.getElementById(id);
  el('system-prompt').value = state.systemPrompt;

  _setCheckbox('setting-auto-titles',     state.autoGenerateTitles);
  _setCheckbox('setting-enter-send',      state.enterToSend);
  _setCheckbox('setting-auto-scroll',     state.autoScrollStreaming);
}

function _readAPIControls() {
  state.apiBase = document.getElementById('api-base').value.trim();

  const temp = document.getElementById('setting-temperature');
  if (temp) state.temperature = parseFloat(temp.value);
}

function _readChatControls() {
  state.systemPrompt        = document.getElementById('system-prompt')?.value.trim()          ?? state.systemPrompt;
  state.autoGenerateTitles  = document.getElementById('setting-auto-titles')?.checked          ?? state.autoGenerateTitles;
  state.enterToSend         = document.getElementById('setting-enter-send')?.checked           ?? state.enterToSend;
  state.autoScrollStreaming = document.getElementById('setting-auto-scroll')?.checked          ?? state.autoScrollStreaming;
}

function _setCheckbox(id, value) {
  const el = document.getElementById(id);
  if (el) el.checked = !!value;
}
