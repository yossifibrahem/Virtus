// Shared presentation router. Native tools and external MCP tools own separate UIs.
import * as mcp from './mcp_tool_ui.js';
import { nativeAdapterFor, bindNativeActions } from './native_tools/index.js';

export { formatArgsHtml } from './tool_format.js';

export const getToolDisplayLabel = (name, args = {}) => nativeAdapterFor(name)?.label(args) ?? mcp.getToolDisplayLabel(name, args);
export const getToolUsingLabel = name => nativeAdapterFor(name)?.usingLabel ?? mcp.getToolUsingLabel(name);
export const visibleToolArgs = (name, args = {}) => nativeAdapterFor(name) ? args : mcp.visibleToolArgs(name, args);
export const renderToolResultHtml = (result, name = null, args = {}) => nativeAdapterFor(name)?.renderResult(result, args) ?? mcp.renderToolResultHtml(result, name, args);

export const getToolKind = name => nativeAdapterFor(name) ? 'native' : 'mcp';
export const getToolOutcome = (name, result) => nativeAdapterFor(name)?.status(result) ?? null;
export const getToolRunningArgs = (name, args) => nativeAdapterFor(name) ? {} : args;
export const getToolIcon = (name, icons, tools, settings) => nativeAdapterFor(name)?.icon ?? mcp.getToolIcon(name, icons, tools, settings);
export function renderToolBody(name, args, result) {
  const native = nativeAdapterFor(name);
  return native ? `<div class="native-tool-body">${native.renderBody(args, result)}</div>` : mcp.renderToolBody(name, args, result);
}
export function bindToolActions(element, name, args) {
  bindNativeActions(element, name, args);
}
