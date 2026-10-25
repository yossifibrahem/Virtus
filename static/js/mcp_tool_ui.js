// MCP tool UI policy — display labels, argument filtering, and generic JSON result rendering.
// Keep UI-specific MCP behavior here so renderer.js stays mostly orchestration/DOM code.
//
// Per-tool overrides (meta text, arg filtering, result rendering) live in
// tool_adapters/. Add or remove adapters by editing tool_adapters/index.js.

import { escapeHtml } from './format.js';
import { adapterFor } from './tool_adapters/index.js';

export { escapeHtml } from './format.js';

export function getToolDisplayLabel(toolName, args = {}) {
  const adapter = adapterFor(toolName);
  const labelArg = adapter?.labelArg ?? 'description';
  const label = String(args?.[labelArg] || '').trim();
  return label || toolName;
}

export function getToolUsingLabel(toolName) {
  const adapter = adapterFor(toolName);
  return adapter?.usingLabel ?? '';
}

function normalizeBlockText(value) {
  return String(value ?? '')
    .replace(/\r\n?/g, '\n')
    .replace(/^\s*\n+/, '')
    .replace(/\n+\s*$/, '');
}

function formatToolValue(value) {
  if (value == null) return '';
  if (typeof value === 'object') return normalizeBlockText(JSON.stringify(value, null, 2));
  return normalizeBlockText(value);
}

export function visibleToolArgs(toolName, rawArgs = {}) {
  if (!rawArgs || typeof rawArgs !== 'object') return {};

  // Strip whichever arg is used as the UI label (default: 'description').
  const adapter = adapterFor(toolName);
  const labelArg = adapter?.labelArg ?? 'description';
  const withoutDescription = Object.fromEntries(
    Object.entries(rawArgs).filter(([key]) => key !== labelArg)
  );

  // Delegate to the adapter's filterArgs if one is registered.
  if (adapter?.filterArgs) return adapter.filterArgs(withoutDescription);

  return withoutDescription;
}

export function formatArgsHtml(args = {}) {
  return Object.entries(args).map(([key, value]) => `
    <div class="arg-item">
      <span class="arg-name">${escapeHtml(key)}</span>
      <pre class="arg-value">${escapeHtml(formatToolValue(value))}</pre>
    </div>`).join('');
}

function parseToolJson(value) {
  if (value && typeof value === 'object') return value;
  if (typeof value !== 'string') return null;
  const text = value.trim();
  if (!text || !/^[\[{]/.test(text)) return null;
  try { return JSON.parse(text); } catch { return null; }
}

function renderJsonResult(data) {
  if (Array.isArray(data)) {
    return `<div class="tr-command-result"><pre class="tr-result">${escapeHtml(formatToolValue(data))}</pre></div>`;
  }

  const entries = Object.entries(data || {});
  if (!entries.length) {
    return `<pre class="tr-result">${escapeHtml(formatToolValue(data))}</pre>`;
  }

  const blocks = entries.map(([key, value]) => `
    <div>
      <div class="tr-section-label">${escapeHtml(key)}</div>
      <pre class="tr-result">${escapeHtml(formatToolValue(value))}</pre>
    </div>`).join('');

  return `<div class="tr-command-result">${blocks}</div>`;
}

export function renderToolResultHtml(result, toolName = null, args = {}) {
  if (result && typeof result === 'object' && Array.isArray(result.media)) {
    const images = result.media.filter(url => typeof url === 'string' && /^\/api\/conversations\/[a-f0-9-]{36}\/tool-media\/[a-f0-9]{64}\.jpeg$/.test(url));
    return `<pre class="tr-result">${escapeHtml(result.text || '')}</pre>` + images.slice(0, 5).map(url => `<img src="${escapeHtml(url)}" alt="Read tool visual result" loading="lazy" style="max-width:100%;max-height:600px">`).join('');
  }
  // Let the registered adapter take first shot at rendering.
  if (toolName) {
    const adapter = adapterFor(toolName);
    const custom = adapter?.renderResult?.(result, args);
    if (custom != null) return custom;
  }

  const data = parseToolJson(result);
  return data
    ? renderJsonResult(data)
    : `<pre class="tr-result">${escapeHtml(formatToolValue(result))}</pre>`;
}
