// App model prompt policy — Virtus app behavior and workspace semantics.
// Add/remove app-level model guidance here instead of editing chat payload assembly.
// Do NOT add tool-specific instructions here — those belong in tool schemas.
// Do NOT add per-user instructions here — those belong in the user system prompt.
// Attachment paths, memory, and tool results supply their own runtime context.

export function buildAppSystemPrompt(folderId = null) {
  return `\

`.trim();
}
