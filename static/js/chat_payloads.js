// Chat payload builders — transform UI state/history into API-ready messages.

import { state } from './state.js';
import { isToolEnabled, isToolAutoApprove } from './mcp.js';
import { buildAppSystemPrompt } from './app_policy.js';

export function buildToolsPayload() {
  return state.mcpTools
    .filter(tool => isToolEnabled(tool.server, tool.name))
    .map(tool => ({
      type: 'function',
      function: {
        name:        tool.name,
        description: tool.description || tool.name,
        parameters:  tool.inputSchema || { type: 'object', properties: {} },
      },
    }));
}

export function buildMcpToolMetaPayload() {
  return state.mcpTools
    .filter(tool => isToolEnabled(tool.server, tool.name))
    .map(tool => ({
      name: tool.name,
      server: tool.server,
      autoApprove: isToolAutoApprove(tool.server, tool.name),
    }));
}

function formatAttachmentContext(files = []) {
  const validFiles = files.filter(file => file?.path);
  if (!validFiles.length) return '';
  return [
    'Attached file(s) available in the chat workspace:',
    ...validFiles.map(file => `- ${file.name || 'file'}: ${file.path}`),
  ].join('\n');
}

function appendTextToContent(content, extraText) {
  if (!extraText) return content;
  if (typeof content === 'string') return [content, extraText].filter(Boolean).join('\n\n');

  if (Array.isArray(content)) {
    const parts = content.map(part => ({ ...part }));
    const textPart = parts.find(part => part.type === 'text');
    if (textPart) textPart.text = [textPart.text || '', extraText].filter(Boolean).join('\n\n');
    else parts.unshift({ type: 'text', text: extraText });
    return parts;
  }

  return extraText;
}

function prepareMessageForApi(message) {
  const { attachments, ...cleanMessage } = message;
  const fileContext = message.role === 'user' ? formatAttachmentContext(attachments || []) : '';
  return fileContext
    ? { ...cleanMessage, content: appendTextToContent(cleanMessage.content, fileContext) }
    : cleanMessage;
}

export async function buildApiMessages(turnMessages, turnSystemPrompt = null, turnFolderId = state.folderId) {
  const messages = [];
  const configuredPrompt = turnSystemPrompt ?? ((turnFolderId && state.folderSystemPrompt.trim()) || state.systemPrompt);
  const systemParts = [configuredPrompt, buildAppSystemPrompt(turnFolderId)].filter(Boolean);
  if (systemParts.length) messages.push({ role: 'system', content: systemParts.join('\n\n') });

  messages.push(...turnMessages.map(prepareMessageForApi));
  // Keep image references small; Flask expands them at the provider boundary.
  return messages;
}
