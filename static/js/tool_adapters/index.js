/**
 * Tool Adapter Manifest
 *
 * This is the only file you need to edit when adding or removing a tool adapter.
 *
 * To add a new adapter:
 *   1. Create `tool_adapters/my_tool.js` that calls registerAdapter({...}).
 *   2. Add one import line below.
 *
 * To remove an adapter:
 *   1. Delete (or keep) its file.
 *   2. Remove its import line below.
 *
 * The registry API is re-exported here so the rest of the codebase only needs
 * to import from this single entry-point:
 *
 *   import { adapterFor } from './tool_adapters/index.js';
 */

// ── Adapter registrations (order does not matter) ─────────────────────────────
import './core_tools.js';
import './exa.js';

// ── Re-export the registry API ────────────────────────────────────────────────
export { adapterFor } from './registry.js';
