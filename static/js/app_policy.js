// App model prompt policy — Virtus app behavior and workspace semantics.
// Add/remove app-level model guidance here instead of editing chat payload assembly.
// Do NOT add tool-specific instructions here — those belong in MCP tool schemas.
// Do NOT add per-user instructions here — those belong in the user system prompt.

// Pure constants — safe at module level.
const _MON = ['January','February','March','April','May','June',
              'July','August','September','October','November','December'];
const _DAY = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];

// Evaluated on every call so the date stays correct across midnight.
function _today() {
  const d = new Date();
  return `${_DAY[d.getDay()]}, ${_MON[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`;
}

export function buildAppSystemPrompt(folderId = null) {
  return `\

`.trim();
}
