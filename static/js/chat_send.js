// Chat send flow — compose a user turn, upload attachments, then start assistant streaming.

import { state } from './state.js';
import { appendMessage } from './renderer.js';
import { syncVisibleBranches } from './chat_branches.js';
import { refreshFilePanel } from './file_panel.js';
import {
  drainPendingAttachments,
  restorePendingAttachments,
  refreshImagePreviewBar,
  uploadConversationFiles,
  waitForPendingAttachmentAdds,
} from './chat_attachments.js';

export async function sendMessage(userText, deps) {
  await waitForPendingAttachmentAdds();
  if (!userText.trim() && !deps.hasPendingAttachments()) return;
  if (state.isStreaming) return;
  if (!state.convId) await deps.createNewConversation();

  syncVisibleBranches();
  const turn = deps.createTurnContext(state.convId);
  deps.setStreaming(true);

  const textToSend        = userText.trim();
  const attachmentsToSend = drainPendingAttachments();
  const imagesToSend      = attachmentsToSend.filter(entry => entry.kind === 'image');
  refreshImagePreviewBar();

  // Resolve images first so a failed image does not silently disappear from the sent message.
  const uploadedRefs = await Promise.all(imagesToSend.map(img => img.uploadPromise));
  const failedImageCount = uploadedRefs.filter(result => !result).length;
  if (failedImageCount) {
    restorePendingAttachments(attachmentsToSend);
    deps.setStreaming(false);
    const plural = failedImageCount === 1 ? '' : 's';
    const errorText = `Image upload failed for ${failedImageCount} attachment${plural}. Remove it or try again before sending.`;
    turn.displayLog.push({ type: 'message', role: 'assistant', content: errorText });
    deps.syncVisibleTurn(turn);
    if (deps.isTurnVisible(turn)) appendMessage('assistant', errorText, turn.displayLog.length - 1);
    return;
  }

  // Copy every attachment, including images, into the chat workspace.
  let uploadedFiles = [];
  try {
    uploadedFiles = await uploadConversationFiles(turn.convId, attachmentsToSend);
    if (uploadedFiles.length && deps.isTurnVisible(turn)) refreshFilePanel({ keepPreview: true }).catch(() => {});
  } catch (err) {
    restorePendingAttachments(attachmentsToSend);
    deps.setStreaming(false);
    const errorText = `File upload failed: ${err.message}`;
    turn.displayLog.push({ type: 'message', role: 'assistant', content: errorText });
    deps.syncVisibleTurn(turn);
    if (deps.isTurnVisible(turn)) appendMessage('assistant', errorText, turn.displayLog.length - 1);
    return;
  }

  const displayAttachments = [];
  let imageCursor = 0;
  attachmentsToSend.forEach((entry, attachmentIndex) => {
    const uploadedFile = uploadedFiles[attachmentIndex];
    if (entry.kind === 'image') {
      const uploaded = uploadedRefs[imageCursor++];
      displayAttachments.push({
        ...uploadedFile,
        kind: 'image',
        url: uploaded.url,
        ref: uploaded.ref,
        mediaType: uploaded.mediaType,
        name: uploadedFile?.name || entry.name || 'image',
        size: uploadedFile?.size ?? entry.size ?? 0,
      });
    } else {
      if (uploadedFile) displayAttachments.push({ kind: 'file', ...uploadedFile });
    }
  });

  const imageAttachments = displayAttachments.filter(entry => entry.kind === 'image');

  let apiContent;
  if (imageAttachments.length > 0) {
    // API payload: user-visible text + image_ref blocks. File paths are injected
    // later by buildApiMessages() from message attachment metadata.
    apiContent = [];
    if (textToSend) apiContent.push({ type: 'text', text: textToSend });
    imageAttachments.forEach(entry => apiContent.push({ type: 'image_ref', ref: entry.ref }));
  } else {
    apiContent = textToSend;
  }

  const displayContent = displayAttachments.length
    ? {
        text: textToSend,
        attachments: displayAttachments,
        imageUrls: imageAttachments.map(entry => entry.url),
        files: uploadedFiles,
      }
    : textToSend;

  // The workspace metadata is attached to the same message as the image_ref,
  // so buildApiMessages() supplies both its file path and its visual content.
  turn.messages.push({ role: 'user', content: apiContent, attachments: uploadedFiles });
  turn.displayLog.push({ type: 'message', role: 'user', content: displayContent });
  deps.syncVisibleTurn(turn);
  if (deps.isTurnVisible(turn)) appendMessage('user', displayContent, turn.displayLog.length - 1);
  await deps.persistTurnConversation(turn);

  await deps.runAssistantTurnAndPersist(turn);

  deps.syncVisibleTurn(turn);
  // Navigation can change state while the reply streams. Persist this turn's
  // history, and only synchronize visible branches when its chat is still open.
  if (deps.isTurnVisible(turn)) syncVisibleBranches();
  await deps.persistTurnConversation(turn);
}
