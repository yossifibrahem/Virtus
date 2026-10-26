// Chat attachments — pending uploads, previews, and workspace file upload helpers.

import { api } from './api.js';
import { ICONS } from './icons.js';
import { escapeHtml, formatBytes, fileExtensionLabel } from './format.js';


// Keep one ordered list so mixed uploads render in the exact order the user picked them.
// Image entry: { kind: 'image', file, previewUrl, uploadPromise, name, size }
// File entry:  { kind: 'file', file, name, size }
let pendingAttachments = [];
let pendingAttachmentAdds = Promise.resolve();

function getImagePreviewBar() { return document.getElementById('image-preview-bar'); }

export function hasPendingAttachments() {
  return pendingAttachments.length > 0;
}

function notifyAttachmentsChanged() {
  document.dispatchEvent(new CustomEvent('chat:attachments-changed', {
    detail: { hasAttachments: hasPendingAttachments() },
  }));
}

function refreshImagePreviewBar() {
  const bar = getImagePreviewBar();
  if (!bar) return;
  if (!pendingAttachments.length) {
    bar.hidden = true;
    bar.innerHTML = '';
    notifyAttachmentsChanged();
    return;
  }
  bar.hidden = false;
  bar.innerHTML = '';

  pendingAttachments.forEach((entry, idx) => {
    const wrap = document.createElement('div');
    wrap.className = `attachment-card ${entry.kind === 'image' ? 'attachment-card--image is-uploading' : 'attachment-card--file'}`;

    if (entry.kind === 'image') {
      wrap.innerHTML = `
        <img class="attachment-card-thumb" src="${entry.previewUrl}" alt="" />
        <div class="attachment-card-overlay">
          <div class="attachment-card-name" title="${escapeHtml(entry.name || 'image')}">${escapeHtml(entry.name || 'image')}</div>
        </div>
        <button class="attachment-card-remove" title="Remove attachment" aria-label="Remove attachment">
          ${ICONS.close}
        </button>`;

      wrap.setAttribute('aria-busy', 'true');
      entry.uploadPromise.then(result => {
        wrap.classList.remove('is-uploading');
        wrap.classList.toggle('is-error', !result);
        wrap.setAttribute('aria-busy', 'false');
        if (!result) wrap.title = 'Upload failed. Remove this attachment and try again.';
      });
    } else {
      wrap.innerHTML = `
        <div class="attachment-card-body">
          <div class="attachment-card-name" title="${escapeHtml(entry.name || 'file')}">${escapeHtml(entry.name || 'file')}</div>
          <div class="attachment-card-footer">
            <span class="attachment-card-badge">${escapeHtml(fileExtensionLabel(entry.name || 'file'))}</span>
          </div>
        </div>
        <button class="attachment-card-remove" title="Remove attachment" aria-label="Remove attachment">
          ${ICONS.close}
        </button>`;
    }

    wrap.querySelector('.attachment-card-remove').addEventListener('click', () => {
      pendingAttachments.splice(idx, 1);
      refreshImagePreviewBar();
    });
    bar.appendChild(wrap);
  });
  notifyAttachmentsChanged();
}

/** Read a File into a base64 data-URL, returning { dataUrl, mediaType, name }. */
function readFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload  = () => resolve({ dataUrl: reader.result, mediaType: file.type || 'image/png', name: file.name });
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

/** Upload base64 image to the server; returns { ref, url, mediaType } or null on failure. */
async function uploadImage(dataUrl, mediaType) {
  const base64 = dataUrl.split(',')[1];
  try {
    const result = await api.post('/api/images', { data: base64, media_type: mediaType });
    if (result.error) return null;
    return { ref: result.ref, url: result.url, mediaType };
  } catch {
    return null;
  }
}

async function uploadConversationFiles(convId, entries) {
  if (!entries.length) return [];
  const form = new FormData();
  entries.forEach(entry => form.append('files', entry.file, entry.name));

  const response = await fetch(`/api/conversations/${encodeURIComponent(convId)}/files`, {
    method: 'POST',
    body: form,
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok || result.error) {
    throw new Error(result.error || `Failed to upload file(s) (${response.status})`);
  }
  return Array.isArray(result.files) ? result.files : [];
}

function addRegularFiles(files) {
  for (const file of Array.from(files)) {
    if (!file || file.type.startsWith('image/')) continue;
    pendingAttachments.push({ kind: 'file', file, name: file.name || 'file', size: file.size || 0 });
  }
  refreshImagePreviewBar();
}

async function addImageFiles(files) {
  for (const file of Array.from(files)) {
    if (!file.type.startsWith('image/')) continue;
    try {
      const { dataUrl, mediaType } = await readFile(file);
      const uploadPromise = uploadImage(dataUrl, mediaType);
      // Retain the original File so the send flow can copy the image into the
      // conversation workspace in addition to uploading its model/preview copy.
      pendingAttachments.push({ kind: 'image', file, previewUrl: dataUrl, uploadPromise, name: file.name || 'image', size: file.size || 0 });
      refreshImagePreviewBar(); // show thumb immediately, upload in background
    } catch {}
  }
}

async function addAttachmentFiles(files) {
  // Sequential processing preserves the user's selected order across mixed image/file uploads.
  for (const file of Array.from(files)) {
    if (!file) continue;
    if (file.type.startsWith('image/')) await addImageFiles([file]);
    else addRegularFiles([file]);
  }
}

export function initImageAttachments() {
  const attachBtn       = document.getElementById('attach-btn');
  const attachmentInput = document.getElementById('attachment-input') || document.getElementById('image-input');
  const textarea        = document.getElementById('user-input');

  attachBtn?.addEventListener('click', () => attachmentInput?.click());
  attachmentInput?.addEventListener('change', e => {
    const files = Array.from(e.target.files || []);
    pendingAttachmentAdds = pendingAttachmentAdds.then(() => addAttachmentFiles(files)).catch(() => {});
    attachmentInput.value = '';
  });

  textarea?.addEventListener('paste', e => {
    const items = Array.from(e.clipboardData?.items || []);
    const fileItems = items.filter(i => i.kind === 'file');
    if (!fileItems.length) return;
    const files = fileItems.map(i => i.getAsFile()).filter(Boolean);
    if (!files.length) return;
    e.preventDefault();
    pendingAttachmentAdds = pendingAttachmentAdds.then(() => addAttachmentFiles(files)).catch(() => {});
  });
}


export async function waitForPendingAttachmentAdds() {
  await pendingAttachmentAdds;
}

export function drainPendingAttachments() {
  return pendingAttachments.splice(0);
}

export function restorePendingAttachments(entries = []) {
  pendingAttachments.unshift(...entries);
  refreshImagePreviewBar();
}

export { refreshImagePreviewBar, uploadConversationFiles };
