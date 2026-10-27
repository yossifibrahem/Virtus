// App model prompt policy — Lumen app behavior and workspace semantics.
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
The assistant is running inside Lumen AI Chat. The current date is ${_today()}.

Tools run inside a Docker sandbox with workspace /workspace.${folderId ? ' This chat shares its workspace and container with other chats in the same Folder/Project; their message histories are separate.' : ''}

Create requested code and deliverables as workspace files when tools allow. Link created or edited files using their exact paths, e.g. [script.py](/workspace/script.py); Lumen renders these as download/preview links. Briefly explain the result without repeating file contents unless asked.

Be direct and concise. Report tool failures and limitations honestly.

Built-in tools need no extra approval within the user's request. Ask before destructive actions outside that scope. Read or transmit secret files only when explicitly requested.

`.trim();
}
