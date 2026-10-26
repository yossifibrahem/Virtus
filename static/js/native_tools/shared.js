// Native-tool UI primitives. No MCP registry, settings, or server dependencies.
import { escapeHtml } from '../format.js';

export { escapeHtml };
export const textOf = result => typeof result === 'string' ? result : String(result?.text ?? '');
export const failed = result => /^Error:/i.test(textOf(result));
export function preview(value, limit = 8000) {
  const text = String(value ?? '');
  return escapeHtml(text.slice(0, limit)) + (text.length > limit ? '\n… [preview shortened]' : '');
}
export function block(title, value, kind = '') {
  return `<section class="native-section"><div class="native-section-title">${escapeHtml(title)}</div><pre class="native-code ${kind}">${preview(value)}</pre></section>`;
}
export function pathLabel(name, args) {
  return args.path ? `${name} · ${String(args.path)}` : name;
}
export function workspacePath(path) {
  if (typeof path !== 'string' || !path || /[\x00-\x1f\\]/.test(path)) return null;
  const full = path.startsWith('/') ? path : `/workspace/${path}`;
  if ((full !== '/workspace' && !full.startsWith('/workspace/')) || full.split('/').includes('..')) return null;
  return full;
}
export function fileHeader(args) {
  const path = String(args.path ?? '');
  return `<div class="native-file-header"><code title="${escapeHtml(path)}">${escapeHtml(path)}</code><div class="native-actions">${workspacePath(path) ? '<button type="button" class="native-action" data-native-open>Open in files</button>' : ''}</div></div>`;
}
export function resultBlock(result) {
  return block(failed(result) ? 'Could not complete' : 'Result', textOf(result), failed(result) ? 'native-error' : '');
}
export function icon(paths) {
  return `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
}
export function status(result, success = 'Done') {
  return { label: failed(result) ? 'Failed' : success, tone: failed(result) ? 'error' : 'success' };
}
