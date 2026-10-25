/**
 * Tool Adapter Registry
 *
 * Central store for per-tool UI overrides. Each adapter may override any
 * combination of three extension points:
 *
 *   labelArg: 'query'           (string, default: 'description')
 *     Which argument holds the human-readable label shown in the strip header.
 *     That argument is also stripped from the displayed args block.
 *     e.g. most tools use 'description'; Exa uses 'query'.
 *
 *   usingLabel: 'Searching the web'  (string)
 *     Static label shown in the "using" strip state before any arguments are known.
 *     Falls back to the bare tool name when omitted.
 *
 *   filterArgs(args)           → object
 *     Return a subset/transformation of the raw args object for display.
 *     The default strips nothing (all args shown).
 *
 *   renderResult(result, args) → string | null
 *     Return custom HTML for the result section, or null to fall through
 *     to the generic JSON/text renderer in mcp_tool_ui.js.
 *
 * Registration:
 *   import { registerAdapter } from './registry.js';
 *   registerAdapter({ tools: ['my_tool'], usingLabel: 'Using tool' });
 *
 * Lookup (used by mcp_tool_ui.js):
 *   import { adapterFor } from './registry.js';
 *   const adapter = adapterFor('web_search');
 */

/** @type {Map<string, Object>} */
const _registry = new Map();

/**
 * Register a tool adapter.
 *
 * @param {Object}   adapter
 * @param {string[]} adapter.tools          - Tool names this adapter handles.
 * @param {string}   [adapter.usingLabel]   - Static label shown in the "using" (pre-args) strip state.
 *                                            Defaults to the bare tool name when omitted.
 *                                            Example: 'Searching the web', 'Running command'.
 * @param {string}   [adapter.labelArg]     - Arg name used as the strip header label (default: 'description').
 *                                            That arg is also hidden from the expanded args block.
 *                                            Set this when your tool uses a different key, e.g. 'query', 'command', 'url'.
 * @param {Function} [adapter.filterArgs]   - (args) => object
 * @param {Function} [adapter.renderResult] - (result, args) => string | null
 */
export function registerAdapter(adapter) {
  if (!Array.isArray(adapter.tools) || !adapter.tools.length) {
    throw new Error('registerAdapter: adapter.tools must be a non-empty array');
  }
  for (const toolName of adapter.tools) {
    _registry.set(toolName, adapter);
  }
}

/**
 * Look up the adapter for a given tool name.
 * Returns null when no adapter is registered, so callers can fall through
 * to generic behaviour.
 *
 * @param   {string}      toolName
 * @returns {Object|null}
 */
export function adapterFor(toolName) {
  return _registry.get(toolName) ?? null;
}

/**
 * Inject a <style> block into <head> exactly once, identified by `id`.
 * Call this at the top of any adapter that needs its own styles.
 *
 * Usage inside an adapter file:
 *   injectStyles('exa-adapter', `
 *     .exa-card { background: var(--surface2); }
 *   `);
 *
 * @param {string} id  - Unique id for the <style> element (e.g. 'exa-adapter').
 * @param {string} css - CSS text to inject.
 */
export function injectStyles(id, css) {
  const styleId = `tool-adapter-styles-${id}`;
  if (document.getElementById(styleId)) return;
  const el = document.createElement('style');
  el.id = styleId;
  el.textContent = css;
  document.head.appendChild(el);
}
