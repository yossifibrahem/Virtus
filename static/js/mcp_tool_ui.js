// MCP tool UI policy — display labels, argument filtering, and generic JSON result rendering.
// Keep UI-specific MCP behavior here so renderer.js stays mostly orchestration/DOM code.
//
// Per-tool overrides (meta text, arg filtering, result rendering) live in
// tool_adapters/. Add or remove adapters by editing tool_adapters/index.js.

import { escapeHtml } from './format.js';
import { formatArgsHtml, renderGenericResult } from './tool_format.js';
import { adapterFor } from './tool_adapters/index.js';


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

export function renderToolResultHtml(result, toolName = null, args = {}) {
  // Let the registered adapter take first shot at rendering.
  if (toolName) {
    const adapter = adapterFor(toolName);
    const custom = adapter?.renderResult?.(result, args);
    if (custom != null) return custom;
  }

  return renderGenericResult(result);
}

export function getToolIcon(toolName, icons, tools = [], serverSettings = {}) {
  const tool = tools.find(item => item.name === toolName);
  if (!tool) return icons.toolDefault;
  const settings = serverSettings[tool.server] || {};
  const iconKey = settings.tools?.[toolName]?.icon || settings.icon || 'toolDefault';
  return icons[iconKey] || icons.toolDefault;
}

export function renderToolBody(toolName, args, result) {
  const displayArgs = visibleToolArgs(toolName, args);
  const argumentsSection = Object.keys(displayArgs).length
    ? `<div class="tr-section"><div class="tr-section-label">Arguments</div><div class="tr-args">${formatArgsHtml(displayArgs)}</div></div>`
    : '';
  return argumentsSection + `<div class="tr-section"><div class="tr-section-label">Result</div>${renderToolResultHtml(result, toolName, args)}</div>`;
}
