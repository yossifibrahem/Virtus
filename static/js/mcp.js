// MCP server config and tool management.

import { api }     from './api.js';
import { state, STORAGE_KEYS } from './state.js';
import { storage } from './storage.js';
import { showToast } from './ui.js';
import { ICONS, MCP_ICON_OPTIONS } from './icons.js';
import { escapeHtml } from './format.js';

// ── Server settings helpers ───────────────────────────────────────────────────

let _draftMcpServerSettings = null;

function cloneServerSettings(settings) {
  return JSON.parse(JSON.stringify(settings || {}));
}

function loadServerSettings() {
  state.mcpServerSettings = storage.get(STORAGE_KEYS.mcpServerSettings, {});
  _draftMcpServerSettings = cloneServerSettings(state.mcpServerSettings);
}

function saveServerSettings() {
  state.mcpServerSettings = cloneServerSettings(_draftMcpServerSettings);
  storage.set(STORAGE_KEYS.mcpServerSettings, state.mcpServerSettings);
}

function settingsRoot(useDraft = false) {
  if (useDraft) {
    if (!_draftMcpServerSettings) _draftMcpServerSettings = cloneServerSettings(state.mcpServerSettings);
    return _draftMcpServerSettings;
  }
  return state.mcpServerSettings;
}

function getServerSetting(serverName, useDraft = false) {
  const root = settingsRoot(useDraft);
  if (!root[serverName]) {
    root[serverName] = { enabled: true, autoApprove: false, icon: 'toolDefault', tools: {} };
  }
  if (!('icon' in root[serverName])) {
    root[serverName].icon = 'toolDefault';
  }
  if (!root[serverName].tools) {
    root[serverName].tools = {};
  }
  return root[serverName];
}

function getToolSetting(serverName, toolName, useDraft = false) {
  const srv = getServerSetting(serverName, useDraft);
  if (!srv.tools[toolName]) {
    srv.tools[toolName] = { enabled: true, autoApprove: null, icon: null }; // null = inherit from server
  }
  if (!('icon' in srv.tools[toolName])) {
    srv.tools[toolName].icon = null;
  }
  return srv.tools[toolName];
}

// ── Public API used by chat_payloads.js ───────────────────────────────────────

export function isServerEnabled(serverName) {
  return getServerSetting(serverName, false).enabled !== false;
}

export function isServerAutoApprove(serverName) {
  return getServerSetting(serverName, false).autoApprove === true;
}

/** Returns true if this specific tool is enabled (server AND tool both enabled). */
export function isToolEnabled(serverName, toolName) {
  if (!isServerEnabled(serverName)) return false;
  return getToolSetting(serverName, toolName, false).enabled !== false;
}

/**
 * Returns true if this tool should auto-approve.
 * Tool-level null = inherit server setting.
 * Tool-level true/false = explicit override.
 */
export function isToolAutoApprove(serverName, toolName) {
  const toolSetting = getToolSetting(serverName, toolName, false);
  if (toolSetting.autoApprove === null || toolSetting.autoApprove === undefined) {
    return isServerAutoApprove(serverName);
  }
  return toolSetting.autoApprove === true;
}

// ── Config ────────────────────────────────────────────────────────────────────

export async function loadMcpConfig() {
  const cfg = await api.get('/api/mcp/config');
  document.getElementById('mcp-config-editor').value = JSON.stringify(cfg.error ? { mcpServers: {} } : cfg, null, 2);
  if (cfg.error) _setMcpStatus(`Could not load config: ${cfg.error}`, 'err');
}

export async function saveMcpConfig() {
  let cfg;
  try {
    cfg = JSON.parse(document.getElementById('mcp-config-editor').value);
  } catch (err) {
    _setMcpStatus(`Invalid JSON: ${err.message}`, 'err');
    return;
  }

  const result = await api.post('/api/mcp/config', cfg);
  if (result.error) {
    _setMcpStatus(`Could not save config: ${result.error}`, 'err');
    return;
  }
}

export function applyMcpToolSettings() {
  saveServerSettings();
  renderToolList();
}

export function resetMcpDraftSettings() {
  _draftMcpServerSettings = cloneServerSettings(state.mcpServerSettings);
  renderToolList();
}


// ── Tool loading ──────────────────────────────────────────────────────────────

function normalizeToolsResponse(payload) {
  if (Array.isArray(payload)) return { tools: payload, skipped: [] };
  if (payload && Array.isArray(payload.tools)) {
    return { tools: payload.tools, skipped: Array.isArray(payload.skipped) ? payload.skipped : [] };
  }
  const message = payload?.error || 'Unexpected MCP tools response';
  throw new Error(message);
}

function toolsStatusMessage(tools, skipped) {
  if (tools.length) {
    const skippedText = skipped.length ? ` (${skipped.length} server(s) skipped)` : '';
    return `${tools.length} tool(s) loaded ✓${skippedText}`;
  }
  if (skipped.length) {
    return `No additional MCP tools loaded — ${skipped.map(item => `${item.server}: ${item.reason}`).join('; ')}`;
  }
  return 'No additional MCP tools loaded';
}

function toolsEndpoint() {
  const params = new URLSearchParams();
  if (state.convId) params.set('conv_id', state.convId);
  const query = params.toString();
  return query ? `/api/mcp/tools?${query}` : '/api/mcp/tools';
}

export function loadCachedTools() {
  loadServerSettings();
  const cached = storage.get(STORAGE_KEYS.mcpTools);
  if (!cached) return false;

  try {
    state.mcpTools = normalizeToolsResponse(cached).tools;
    if (state.mcpTools.length) renderToolList();
    return state.mcpTools.length > 0;
  } catch {
    state.mcpTools = [];
    storage.remove(STORAGE_KEYS.mcpTools);
    return false;
  }
}

export async function reloadTools() {
  const btn = document.getElementById('btn-reload-tools');
  if (btn) {
    btn.disabled = true;
    btn.classList.add('loading');
    btn.title = 'Loading tools…';
    btn.setAttribute('aria-label', 'Loading tools');
  }
  _setMcpStatus('Loading tools…', 'ok');

  try {
    const payload = await api.get(toolsEndpoint());
    const result = normalizeToolsResponse(payload);
    state.mcpTools = result.tools;
    storage.set(STORAGE_KEYS.mcpTools, state.mcpTools);
    renderToolList();
    _setMcpStatus(toolsStatusMessage(state.mcpTools, result.skipped), result.skipped.length ? 'err' : 'ok');
    showToast(`${state.mcpTools.length} tool(s) loaded`);
  } catch (err) {
    state.mcpTools = [];
    storage.remove(STORAGE_KEYS.mcpTools);
    renderToolList();
    _setMcpStatus(`Error loading tools: ${err.message}`, 'err');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.classList.remove('loading');
      btn.title = 'Reload tools';
      btn.setAttribute('aria-label', 'Reload tools');
    }
  }
}

function _setMcpStatus(message, type) {
  const el = document.getElementById('mcp-status');
  if (!el) return;
  el.textContent = message;
  el.className = `status-msg ${type}`;
  el.style.display = 'block';
}

// ── Render helpers ────────────────────────────────────────────────────────────

function openIconDropdown(btn, container) {
  // Close all open dropdowns first
  container.querySelectorAll('.icon-picker-dropdown').forEach(d => { d.style.display = 'none'; });
  const dropdown = btn.closest('[class*="-icon-wrap"]').querySelector('.icon-picker-dropdown');
  if (!dropdown) return;
  // Position using fixed coords so overflow:hidden on ancestors doesn't clip it
  const rect = btn.getBoundingClientRect();
  dropdown.style.display = 'flex';
  const ddRect = dropdown.getBoundingClientRect();
  const spaceBelow = window.innerHeight - rect.bottom;
  if (spaceBelow < ddRect.height + 8) {
    dropdown.style.top  = `${rect.top - ddRect.height - 5}px`;
  } else {
    dropdown.style.top  = `${rect.bottom + 5}px`;
  }
  dropdown.style.left = `${Math.min(rect.left, window.innerWidth - ddRect.width - 8)}px`;
}


function buildSettingsToggle(dataAttrs, isOn, title) {
  const attrStr = Object.entries(dataAttrs)
    .map(([k, v]) => `data-${k}="${escapeHtml(String(v))}"`)
    .join(' ');
  return `<label class="cust-toggle mcp-settings-toggle" title="${escapeHtml(title)}">
    <input type="checkbox" ${attrStr}${isOn ? ' checked' : ''} aria-label="${escapeHtml(title)}" />
    <span class="cust-toggle-track"><span class="cust-toggle-thumb"></span></span>
  </label>`;
}

function buildServerToggleRow(server, settings) {
  return `
    <div class="server-toggle-group">
      <div class="server-toggle-label">
        ${buildSettingsToggle({ server: server, action: 'enabled', level: 'server' }, settings.enabled, 'Enable server')}
        <span class="toggle-label-text">Enabled</span>
      </div>
      <div class="server-toggle-label">
        ${buildSettingsToggle({ server: server, action: 'autoApprove', level: 'server' }, settings.autoApprove, 'Auto-approve all tools')}
        <span class="toggle-label-text">Auto-approve</span>
      </div>
    </div>`;
}

function buildIconPickerHtml(server, currentIconKey, isDefault) {
  const resetBtn = `
    <button class="icon-option icon-option-reset${isDefault ? ' selected' : ''}"
            data-server="${escapeHtml(server)}" data-icon="__default__"
            title="Use default icon" aria-label="Use default icon">
      ${ICONS.tabMcp}
      <span class="icon-inherit-label">default</span>
    </button>`;
  const optionsHtml = MCP_ICON_OPTIONS.map(opt => `
    <button class="icon-option${!isDefault && currentIconKey === opt.key ? ' selected' : ''}"
            data-server="${escapeHtml(server)}" data-icon="${opt.key}"
            title="${escapeHtml(opt.label)}" aria-label="${escapeHtml(opt.label)}">
      ${ICONS[opt.key]}
    </button>`).join('');
  return `
    <div class="server-icon-wrap" data-server="${escapeHtml(server)}">
      <button class="server-icon-btn" data-server="${escapeHtml(server)}"
              title="${isDefault ? 'Default icon' : 'Custom icon set (click to change)'}"
              aria-label="Change server icon">
        <span class="server-icon-current">${ICONS[currentIconKey] || ICONS.toolDefault}</span>
        ${isDefault ? '' : '<span class="tool-icon-dot"></span>'}
      </button>
      <div class="icon-picker-dropdown" style="display:none">
        ${resetBtn}
        <div class="icon-picker-divider"></div>
        ${optionsHtml}
      </div>
    </div>`;
}

function buildToolIconPickerHtml(server, toolName, currentIconKey, isInheritingIcon) {
  const resetBtn = `
    <button class="icon-option icon-option-reset${isInheritingIcon ? ' selected' : ''}"
            data-server="${escapeHtml(server)}" data-tool="${escapeHtml(toolName)}" data-icon="__inherit__"
            title="Follow server icon" aria-label="Follow server icon">
      ${ICONS.tabMcp}
      <span class="icon-inherit-label">auto</span>
    </button>`;
  const optionsHtml = MCP_ICON_OPTIONS.map(opt => `
    <button class="icon-option${!isInheritingIcon && currentIconKey === opt.key ? ' selected' : ''}"
            data-server="${escapeHtml(server)}" data-tool="${escapeHtml(toolName)}" data-icon="${opt.key}"
            title="${escapeHtml(opt.label)}" aria-label="${escapeHtml(opt.label)}">
      ${ICONS[opt.key]}
    </button>`).join('');
  return `
    <div class="tool-icon-wrap" data-server="${escapeHtml(server)}" data-tool="${escapeHtml(toolName)}">
      <button class="tool-icon-btn" data-server="${escapeHtml(server)}" data-tool="${escapeHtml(toolName)}"
              title="${isInheritingIcon ? 'Icon follows server (click to override)' : 'Custom icon set (click to change)'}"
              aria-label="Change tool icon">
        <span class="tool-icon-current">${ICONS[currentIconKey] || ICONS.toolDefault}</span>
        ${isInheritingIcon ? '' : '<span class="tool-icon-dot"></span>'}
      </button>
      <div class="icon-picker-dropdown" style="display:none">
        ${resetBtn}
        <div class="icon-picker-divider"></div>
        ${optionsHtml}
      </div>
    </div>`;
}

/**
 * Build the per-tool row with icon, enable + auto-approve mini-toggles.
 * autoApprove null = inherited from server (shown with a dot indicator).
 */
function buildToolRowHtml(server, tool, serverSettings) {
  const toolName    = tool.name;
  const toolSetting = getToolSetting(server, toolName, true);
  const isEnabled   = toolSetting.enabled !== false;

  const autoApproveVal = toolSetting.autoApprove;
  const isInherited    = autoApproveVal === null || autoApproveVal === undefined;
  const isAutoApprove  = isInherited ? serverSettings.autoApprove : autoApproveVal;

  const displayName = toolName;

  // Icon: use tool-specific icon if set, otherwise fall back to server icon
  const isInheritingIcon = !toolSetting.icon;
  const toolIconKey = toolSetting.icon || serverSettings.icon || 'toolDefault';

  return `
    <div class="tool-row${!isEnabled ? ' tool-row-disabled' : ''}"
         data-server="${escapeHtml(server)}" data-tool="${escapeHtml(toolName)}">
      <div class="tool-row-info">
        ${buildToolIconPickerHtml(server, toolName, toolIconKey, isInheritingIcon)}
        <div class="tool-row-text">
          <span class="tool-row-name" title="${escapeHtml(toolName)}">${escapeHtml(displayName)}</span>
          <span class="tool-row-desc">${escapeHtml(tool.description || '')}</span>
        </div>
      </div>
      <div class="tool-row-toggles">
        ${buildSettingsToggle(
          { server: server, tool: toolName, action: 'enabled', level: 'tool' },
          isEnabled,
          'Enable this tool'
        )}
        <span class="tool-auto-wrap${isInherited ? ' is-inherited' : ''}">
          ${buildSettingsToggle(
            { server: server, tool: toolName, action: 'autoApprove', level: 'tool' },
            isAutoApprove,
            isInherited
              ? 'Auto-approve inherited from server – click to override'
              : 'Auto-approve override – click to reset to server default'
          )}
          ${isInherited ? '<span class="inherit-badge" title="Inherited from server">↑</span>' : ''}
        </span>
      </div>
    </div>`;
}

function buildServerGroupHtml(server, tools, settings) {
  const disabledCls = settings.enabled ? '' : ' server-disabled';
  const isDefault    = !settings.icon;
  const currentIcon = settings.icon || 'toolDefault';

  const enabledCount = tools.filter(t => getToolSetting(server, t.name, true).enabled !== false).length;
  const totalCount   = tools.length;

  const toolsHtml = tools.map(tool => buildToolRowHtml(server, tool, settings)).join('');

  return `
    <div class="server-group${disabledCls}" data-server="${escapeHtml(server)}">
      <div class="server-group-header">
        <div class="server-header-left">
          ${buildIconPickerHtml(server, currentIcon, isDefault)}
          <div class="server-header-meta">
            <span class="server-group-name">${escapeHtml(server)}</span>
            <span class="server-tool-count">${enabledCount} / ${totalCount} tool${totalCount !== 1 ? 's' : ''} enabled</span>
          </div>
        </div>
        <div class="server-header-right">
          ${buildServerToggleRow(server, settings)}
        </div>
      </div>
      <div class="server-tools">
        <div class="tool-list-header">
          <span class="tool-col-name">Tool</span>
          <div class="tool-col-controls">
            <span class="tool-col-label">On</span>
            <span class="tool-col-label">Auto</span>
          </div>
        </div>
        <div class="tool-rows">${toolsHtml}</div>
      </div>
    </div>`;
}

function groupToolsByServer(tools) {
  return tools.reduce((acc, tool) => {
    if (!acc[tool.server]) acc[tool.server] = [];
    acc[tool.server].push(tool);
    return acc;
  }, {});
}

function renderToolList() {
  const container = document.getElementById('tool-list');

  if (!state.mcpTools.length) {
    container.innerHTML = '<div class="no-tools-label">No additional MCP tools loaded</div>';
    return;
  }

  const byServer = groupToolsByServer(state.mcpTools);
  container.innerHTML = Object.entries(byServer)
    .map(([server, tools]) => buildServerGroupHtml(server, tools, getServerSetting(server, true)))
    .join('');

  // ── Server-level toggles ──────────────────────────────────────────────────
  container.querySelectorAll('.mcp-settings-toggle input[data-level="server"]').forEach(input => {
    input.addEventListener('change', () => {
      const setting = getServerSetting(input.dataset.server, true);
      setting[input.dataset.action] = input.checked;
      renderToolList();
    });
  });

  // ── Tool-level toggles ────────────────────────────────────────────────────
  container.querySelectorAll('.mcp-settings-toggle input[data-level="tool"]').forEach(input => {
    input.addEventListener('change', () => {
      const toolSetting = getToolSetting(input.dataset.server, input.dataset.tool, true);
      const action = input.dataset.action;

      if (action === 'enabled') {
        toolSetting.enabled = input.checked;
      } else if (action === 'autoApprove') {
        // Cycle: null (inherited) → explicit-opposite → null (inherited)
        if (toolSetting.autoApprove === null || toolSetting.autoApprove === undefined) {
          const serverAuto = getServerSetting(input.dataset.server, true).autoApprove;
          toolSetting.autoApprove = !serverAuto;
        } else {
          toolSetting.autoApprove = null; // reset to inherit
        }
      }
      renderToolList();
    });
  });

  // ── Server icon picker: toggle dropdown ─────────────────────────────────
  container.querySelectorAll('.server-icon-btn').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      const wrap     = btn.closest('.server-icon-wrap');
      const dropdown = wrap.querySelector('.icon-picker-dropdown');
      const isOpen   = dropdown.style.display !== 'none';
      if (isOpen) { dropdown.style.display = 'none'; return; }
      openIconDropdown(btn, container);
    });
  });

  // ── Server icon picker: select icon ──────────────────────────────────────
  container.querySelectorAll('.server-icon-wrap .icon-option').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      const setting = getServerSetting(btn.dataset.server, true);
      // __default__ resets to null so the server uses the default icon
      setting.icon = btn.dataset.icon === '__default__' ? null : btn.dataset.icon;
      renderToolList();
    });
  });

  // ── Tool icon picker: toggle dropdown ─────────────────────────────────────
  container.querySelectorAll('.tool-icon-btn').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      const wrap     = btn.closest('.tool-icon-wrap');
      const dropdown = wrap.querySelector('.icon-picker-dropdown');
      const isOpen   = dropdown.style.display !== 'none';
      if (isOpen) { dropdown.style.display = 'none'; return; }
      openIconDropdown(btn, container);
    });
  });

  // ── Tool icon picker: select icon ─────────────────────────────────────────
  container.querySelectorAll('.tool-icon-wrap .icon-option').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      const toolSetting = getToolSetting(btn.dataset.server, btn.dataset.tool, true);
      // __inherit__ resets to null so the tool follows the server icon
      toolSetting.icon = btn.dataset.icon === '__inherit__' ? null : btn.dataset.icon;
      renderToolList();
    });
  });

  // Close dropdowns when clicking outside
  document.addEventListener('click', () => {
    container.querySelectorAll('.icon-picker-dropdown').forEach(d => { d.style.display = 'none'; });
  }, { once: true });
}
