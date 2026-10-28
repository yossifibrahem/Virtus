// Workspace file panel — browse files inside the per-chat /workspace.

import { api } from './api.js';
import { STORAGE_KEYS, state } from './state.js';
import { ICONS } from './icons.js';

import { applyMarkdown } from './markdown.js';
import { renderSource } from './source_view.js';
import { mountPdfPreview } from './pdf_preview.js';
import { showToast } from './ui.js';
import { storage } from './storage.js';
import { escapeHtml, formatBytes, fileExtension as ext } from './format.js';

const PANEL_WIDTH_KEY = 'lumen_file_panel_width';
const MIN_PANEL_WIDTH = 280;
const DEFAULT_PANEL_WIDTH = 380;
const MAX_PANEL_VIEWPORT_RATIO = 0.5;
const WORKSPACE_ROOT = '/workspace';
const TREE_INDENT_PX = 14;

let selectedPath = null;
let isOpen = false;
let renderMode = 'render';   // 'render' | 'code'
let currentFileData = null;  // last loaded file payload, for re-rendering without re-fetch
let panelWidth = DEFAULT_PANEL_WIDTH;
let treeRenderId = 0;
let listRequestId = 0;
let previewRequestId = 0;
let disposePdf = null;
const expandedPaths = new Set([WORKSPACE_ROOT]);

// Resolved once in initFilePanel — these elements are stable for the page lifetime.
let els = {};

function workspaceApiBase() {
  if (state.convId) return `/api/conversations/${encodeURIComponent(state.convId)}`;
  if (state.folderId) return `/api/folders/${encodeURIComponent(state.folderId)}`;
  return null;
}

function maxPanelWidth() {
  // Keep this in sync with the desktop max-width in 12-file-panel.css. The
  // same value drives the panel's negative closing margin, so allowing the
  // custom property to exceed the rendered width can push the header controls
  // past the right edge of the viewport when the panel closes.
  const viewportLimit = Math.floor(window.innerWidth * MAX_PANEL_VIEWPORT_RATIO);
  return Math.max(MIN_PANEL_WIDTH, Math.min(viewportLimit, 920));
}

function clampPanelWidth(width) {
  return Math.min(Math.max(width, MIN_PANEL_WIDTH), maxPanelWidth());
}

function applyPanelWidth(width) {
  if (!els.panel) return;
  panelWidth = clampPanelWidth(Number(width) || DEFAULT_PANEL_WIDTH);
  els.panel.style.setProperty('--file-panel-w', `${panelWidth}px`);
}

function loadSavedPanelWidth() {
  applyPanelWidth(Number(storage.get(PANEL_WIDTH_KEY)) || DEFAULT_PANEL_WIDTH);
}

function savePanelWidth(width) {
  applyPanelWidth(width);
  storage.set(PANEL_WIDTH_KEY, panelWidth);
}

function isMarkdown(name = '') {
  return ['md', 'markdown'].includes(ext(name));
}

function isHtml(name = '') {
  return ['html', 'htm'].includes(ext(name));
}

function isRenderable(name = '') {
  return isMarkdown(name) || isHtml(name);
}

function isMissingWorkspacePath(error = '') {
  return ['Path not found', 'File not found', 'Path is not a file'].includes(error);
}

function setPanelOpen(open, { persist = true } = {}) {
  isOpen = open;
  if (persist) storage.set(STORAGE_KEYS.filePanelOpen, open);
  els.panel?.classList.toggle('open', open);
  els.toggle?.classList.toggle('active', open);
  els.toggle?.setAttribute('aria-expanded', String(open));
  if (!open && els.panel?.contains(document.activeElement)) els.toggle?.focus();
  if (els.panel) els.panel.inert = !open;
  if (!open && currentFileData?.preview_kind === 'pdf') {
    clearPreview();
    currentFileData = null;
  }

  if (open) {
    refreshFilePanel({ keepPreview: Boolean(selectedPath) }).catch(() => {});
  }
}

function setPreviewOpen(open) {
  const changed = open && !els.panel?.classList.contains('preview-open');
  els.panel?.classList.toggle('preview-open', open);
  if (changed && isOpen) els.back?.focus();
}

function updateToggleButton(name, mode, visual = false) {
  if (!els.toggleRender) return;

  if (!isRenderable(name) && !visual) {
    els.toggleRender.hidden = true;
    return;
  }

  els.toggleRender.hidden = false;
  if (mode === 'render') {
    els.toggleRender.innerHTML = ICONS.eyeShow;
    els.toggleRender.title = visual ? 'View text' : 'View source';
    els.toggleRender.setAttribute('aria-label', els.toggleRender.title);
    els.toggleRender.classList.add('active');
  } else {
    els.toggleRender.innerHTML = ICONS.eyeHide;
    els.toggleRender.title = 'Preview';
    els.toggleRender.setAttribute('aria-label', 'Preview');
    els.toggleRender.classList.remove('active');
  }
}

/**
 * Inject `<base target="_blank">` into an HTML string so that any links
 * inside the previewed document open in a new tab rather than navigating
 * the iframe (or, worse, the parent page).  If a <base> tag already exists
 * we leave it untouched so the author's intent is preserved.
 *
 * Also injects a small script that intercepts same-page hash-anchor clicks
 * (e.g. <a href="#section">) and handles them via scrollIntoView instead,
 * so they scroll within the preview rather than opening a new tab (which
 * is what `<base target="_blank">` would otherwise cause).
 *
 * Uses DOMParser so the browser's own HTML parser handles insertion correctly
 * regardless of document structure, casing, or encoding.
 */
function injectBaseTarget(html) {
  const doc = new DOMParser().parseFromString(html, 'text/html');

  // Leave the document untouched if it already controls its own base target.
  if (doc.querySelector('base')) return html;

  const base = doc.createElement('base');
  base.target = '_blank';
  base.rel = 'noopener noreferrer';
  doc.head.prepend(base);

  // Intercept hash-only links so they scroll within the iframe instead of
  // opening a new tab (the default behaviour imposed by <base target="_blank">).
  const script = doc.createElement('script');
  script.textContent = `
document.addEventListener('click', function(e) {
  var a = e.target.closest('a[href]');
  if (!a) return;
  var href = a.getAttribute('href');
  if (!href || href[0] !== '#') return;
  e.preventDefault();
  var id = href.slice(1);
  var target = id ? document.getElementById(id) || document.querySelector('[name="' + id + '"]') : document.documentElement;
  if (!target) return;
  target.scrollIntoView({ behavior: 'smooth' });
}, true);`;
  doc.head.prepend(script);

  return doc.documentElement.outerHTML;
}

function clearPreview() {
  disposePdf?.();
  disposePdf = null;
  els.body.replaceChildren();
}

function renderContent(data, mode) {
  clearPreview();
  const content = data.content || '';
  if (data.preview_kind === 'pdf') {
    disposePdf = mountPdfPreview(els.body,
      `${workspaceApiBase()}/files/pdf?path=${encodeURIComponent(data.path)}`, data.name);
    return;
  }
  if (data.preview_kind && mode === 'render') {
    renderVisualPreview(data);
    return;
  }
  if (isHtml(data.name) && mode === 'render') {
    const iframe = document.createElement('iframe');
    iframe.className = 'file-preview-iframe';
    iframe.title = data.name || 'HTML preview';
    // Preserve an opaque origin even when the preview runs scripts.
    iframe.setAttribute('sandbox', 'allow-scripts allow-popups allow-popups-to-escape-sandbox');
    iframe.srcdoc = injectBaseTarget(content);
    els.body.appendChild(iframe);
    return;
  }
  const preview = document.createElement('div');
  preview.className = 'file-preview-content';
  els.body.appendChild(preview);
  if (isMarkdown(data.name) && mode === 'render') {
    preview.classList.add('msg-content');
    applyMarkdown(preview, content, { copyCodeButtons: false });
  } else {
    preview.classList.add('file-preview-code');
    preview.innerHTML = renderSource(content, { filename: data.name });
  }
}

function renderVisualPreview(data) {
  const preview = document.createElement('div');
  preview.className = 'file-preview-visual';
  els.body.appendChild(preview);
  if (data.preview_kind === 'notebook' && data.total != null) {
    const navigation = document.createElement('div');
    navigation.className = 'file-preview-navigation';
    const step = 20;
    const unit = 'Cells';
    const label = document.createElement('span');
    label.textContent = data.total ? `${unit} ${data.start}–${data.end} of ${data.total}` : 'Empty notebook';
    navigation.appendChild(label);
    for (const [title, start, disabled] of [
      ['Previous', Math.max(1, data.start - step), data.start <= 1],
      ['Next', data.end + 1, data.end >= data.total],
    ]) {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = title;
      button.disabled = disabled;
      button.addEventListener('click', () => {
        loadFilePreview(data.path, `${start}:${start + step - 1}`).catch(() => {});
      });
      navigation.appendChild(button);
    }
    preview.appendChild(navigation);
  }
  // The server rasterizes visuals with the same bounded renderer as View.
  for (const image of data.images || []) {
    if (image.mime !== 'image/jpeg' || !/^[A-Za-z0-9+/]+={0,2}$/.test(image.data)) continue;
    const figure = document.createElement('figure');
    const img = document.createElement('img');
    img.src = `data:image/jpeg;base64,${image.data}`;
    img.alt = data.name;
    img.loading = 'lazy';
    figure.appendChild(img);
    preview.appendChild(figure);
  }
  if (data.preview_kind === 'notebook') {
    const cells = document.createElement('div');
    cells.innerHTML = renderSource(data.content || '', { filename: data.name });
    preview.appendChild(cells);
  }
}


function resetPreview() {
  previewRequestId++;
  selectedPath = null;
  setSelectedTreeRow(null);
  currentFileData = null;
  renderMode = 'render';
  els.title.textContent = 'Preview';
  clearPreview();
  els.copy.disabled = true;
  els.download.disabled = true;
  els.download.removeAttribute('data-path');
  if (els.toggleRender) els.toggleRender.hidden = true;
}

function closePreview() {
  const returnFocus = els.panel?.contains(document.activeElement);
  const row = els.list?.querySelector('.file-row.active');
  resetPreview();
  setPreviewOpen(false);
  if (returnFocus && isOpen) (row || els.refresh)?.focus();
}

function setEmptyPreview(message = 'Select a file to preview it here.') {
  resetPreview();
  els.body.innerHTML = `<div class="file-panel-empty">${escapeHtml(message)}</div>`;
}

function displayNameForPath(path = WORKSPACE_ROOT) {
  const parts = String(path).split('/').filter(Boolean);
  return parts.at(-1) || 'workspace';
}

function setSelectedTreeRow(path) {
  els.list?.querySelectorAll('.file-row.active').forEach(row => row.classList.remove('active'));
  const row = [...(els.list?.querySelectorAll('.file-row.is-file') || [])]
    .find(candidate => candidate.dataset.path === path);
  row?.classList.add('active');
}

function treeStatus(message, className = '') {
  const status = document.createElement('div');
  status.className = `file-tree-status${className ? ` ${className}` : ''}`;
  status.textContent = message;
  return status;
}

async function fetchDirectory(path) {
  const apiBase = workspaceApiBase();
  if (!apiBase) return { error: 'Start or open a chat to browse its workspace.' };
  return api.get(`${apiBase}/files?path=${encodeURIComponent(path)}`)
    .catch(error => ({ error: error.message || 'Could not load files' }));
}

async function populateTreeGroup(group, payload, depth, renderId) {
  if (renderId !== treeRenderId) return;
  group.innerHTML = '';

  if (payload.entries?.length) {
    const items = payload.entries.map(entry => fileTreeItem(entry, depth, renderId));
    items.forEach(item => group.appendChild(item.element));
    await Promise.all(items.map(item => item.restoreExpansion()));
  }

  if (payload.truncated) {
    group.appendChild(treeStatus(
      payload.limit ? `First ${payload.limit} entries shown` : 'Partial list shown',
      'is-note',
    ));
  }
}

async function expandTreeItem(item, entry, depth, renderId) {
  const group = item.querySelector(':scope > .file-tree-children');
  const row = item.querySelector(':scope > .file-row');
  if (!group || !row || renderId !== treeRenderId) return;

  expandedPaths.add(entry.path);
  item.classList.add('expanded', 'loading');
  item.querySelector(':scope > .file-row .file-row-icon').innerHTML = ICONS.folderOpen;
  row.setAttribute('aria-expanded', 'true');
  group.hidden = false;
  group.replaceChildren(treeStatus('Loading…', 'is-loading'));

  const payload = await fetchDirectory(entry.path);
  if (renderId !== treeRenderId || !item.isConnected) return;
  item.classList.remove('loading');
  if (payload.error) {
    group.replaceChildren(treeStatus(payload.error, 'is-error'));
    return;
  }
  await populateTreeGroup(group, payload, depth + 1, renderId);
}

function collapseTreeItem(item, path) {
  const group = item.querySelector(':scope > .file-tree-children');
  const row = item.querySelector(':scope > .file-row');
  expandedPaths.delete(path);
  item.classList.remove('expanded', 'loading');
  item.querySelector(':scope > .file-row .file-row-icon').innerHTML = ICONS.mcpFileSystem;
  row?.setAttribute('aria-expanded', 'false');
  if (group) group.hidden = true;
}

function isManagedPath(path) {
  const relative = String(path || '').slice('/workspace/'.length);
  return String(path || '').startsWith('/workspace/') && relative &&
    !relative.split('/').some(part => !part || part === '.' || part === '..');
}

async function deleteWorkspaceEntry(entry, button) {
  const apiBase = workspaceApiBase();
  if (!apiBase || !isManagedPath(entry.path)) return;
  if (!window.confirm(`Permanently delete ${entry.path}${entry.type === 'directory' ? ' and all its contents' : ''}?`)) return;
  button.disabled = true;
  try {
    const result = await api.delete(`${apiBase}/files/delete?path=${encodeURIComponent(entry.path)}`);
    if (result.error) throw new Error(result.error);
    if (apiBase !== workspaceApiBase()) return;
    if (selectedPath === entry.path || selectedPath?.startsWith(entry.path + '/')) closePreview();
    await refreshFilePanel({ keepPreview: Boolean(selectedPath) });
    showToast('Workspace files deleted');
  } catch (error) {
    showToast(error.message || 'Could not delete workspace files');
  } finally {
    button.disabled = false;
  }
}

function fileTreeItem(entry, depth, renderId, { root = false, payload = null } = {}) {
  const isDirectory = entry.type === 'directory';
  const item = document.createElement('div');
  item.className = `file-tree-item ${isDirectory ? 'is-dir' : 'is-file'}`;
  item.setAttribute('role', 'treeitem');

  const row = document.createElement('button');
  row.type = 'button';
  row.className = `file-row ${isDirectory ? 'is-dir' : 'is-file'}${entry.path === selectedPath ? ' active' : ''}`;
  row.dataset.path = entry.path;
  row.style.setProperty('--tree-offset', `${depth * TREE_INDENT_PX}px`);
  row.title = entry.path;
  if (isDirectory) row.setAttribute('aria-expanded', String(root || expandedPaths.has(entry.path)));
  row.innerHTML = `
    <span class="file-row-icon">${isDirectory ? ICONS.mcpFileSystem : ICONS.file}</span>
    <span class="file-row-name">${escapeHtml(entry.name)}</span>
    ${isDirectory || entry.size == null ? '' : `<span class="file-row-size">${escapeHtml(formatBytes(entry.size))}</span>`}`;
  item.appendChild(row);
  if (!root && isManagedPath(entry.path)) {
    row.classList.add('has-delete');
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'file-tree-delete';
    remove.title = `Delete ${entry.name}`;
    remove.setAttribute('aria-label', `Delete ${entry.name}`);
    remove.innerHTML = ICONS.trash;
    remove.addEventListener('click', () => deleteWorkspaceEntry(entry, remove));
    item.appendChild(remove);
  }

  let group = null;
  if (isDirectory) {
    group = document.createElement('div');
    group.className = 'file-tree-children';
    group.setAttribute('role', 'group');
    group.hidden = !root && !expandedPaths.has(entry.path);
    item.appendChild(group);
  }

  row.addEventListener('click', async () => {
    if (!isDirectory) {
      setSelectedTreeRow(entry.path);
      await loadFilePreview(entry.path);
      return;
    }
    if (item.classList.contains('expanded')) collapseTreeItem(item, entry.path);
    else await expandTreeItem(item, entry, depth, renderId);
  });

  return {
    element: item,
    restoreExpansion: async () => {
      if (!isDirectory || (!root && !expandedPaths.has(entry.path))) return;
      item.classList.add('expanded');
      item.querySelector(':scope > .file-row .file-row-icon').innerHTML = ICONS.folderOpen;
      if (payload) await populateTreeGroup(group, payload, depth + 1, renderId);
      else await expandTreeItem(item, entry, depth, renderId);
    },
  };
}

async function renderList(payload) {
  const renderId = ++treeRenderId;
  els.list.innerHTML = '';
  els.list.setAttribute('role', 'tree');
  els.list.setAttribute('aria-label', 'Workspace files');

  const rootEntry = {
    name: displayNameForPath(payload.path),
    path: payload.path || WORKSPACE_ROOT,
    type: 'directory',
  };
  expandedPaths.add(rootEntry.path);
  const root = fileTreeItem(rootEntry, 0, renderId, { root: true, payload });
  els.list.appendChild(root.element);
  await root.restoreExpansion();
}

async function loadFileList() {
  const requestId = ++listRequestId;
  ++treeRenderId;
  const apiBase = workspaceApiBase();
  if (!apiBase) {
    els.list.innerHTML = '<div class="file-panel-empty">Start or open a chat to browse its workspace.</div>';
    setEmptyPreview('Workspace files will appear after a chat exists.');
    setPreviewOpen(false);
    return;
  }

  if (!els.list.children.length) {
    els.list.innerHTML = '<div class="file-panel-empty">Loading files…</div>';
  }
  const payload = await fetchDirectory(WORKSPACE_ROOT);
  if (requestId !== listRequestId || apiBase !== workspaceApiBase()) return false;
  if (payload.error) {
    els.list.innerHTML = `<div class="file-panel-empty">${escapeHtml(payload.error)}</div>`;
    return false;
  }
  await renderList(payload);
  return true;
}

async function loadFilePreview(path, range = '') {
  const apiBase = workspaceApiBase();
  if (!apiBase || !path) return;
  const requestId = ++previewRequestId;
  const previous = selectedPath === path && currentFileData?.previewRange === range ? currentFileData : null;
  selectedPath = path;
  currentFileData = previous;
  els.toggleRender.hidden = true;
  els.title.textContent = displayNameForPath(path);
  setPreviewOpen(true);

  if (!previous) {
    clearPreview();
    els.body.innerHTML = '<div class="file-panel-empty" role="status">Loading preview…</div>';
  }
  els.copy.disabled = true;
  els.download.disabled = true;
  els.download.removeAttribute('data-path');

  const payload = await api.get(`${apiBase}/files/content?path=${encodeURIComponent(path)}${range ? `&range=${encodeURIComponent(range)}` : ''}${previous?.version ? `&version=${encodeURIComponent(previous.version)}` : ''}`)
    .catch(error => ({ error: error.message || 'Could not load file' }));
  if (requestId !== previewRequestId || apiBase !== workspaceApiBase()) return false;
  const data = payload.unchanged && previous ? previous : payload;
  if (data.error) {
    if (isMissingWorkspacePath(data.error)) {
      closePreview();
      await loadFileList();
      showToast('Previewed file is no longer available');
      return false;
    }
    setEmptyPreview(data.error);
    setPreviewOpen(true);
    return false;
  }

  els.title.textContent = data.name || 'File';
  els.title.title = data.path || path;
  els.download.disabled = false;
  els.download.dataset.path = data.path;

  if (!data.previewable) {
    els.body.innerHTML = `
      <div class="file-panel-empty">
        ${escapeHtml(data.preview_error || 'Preview is unavailable for this file type or size.')}<br />
        <span>Use Download to save it.</span>
      </div>`;
    return true;
  }

  els.copy.disabled = typeof data.content !== 'string';

  currentFileData = data;
  data.previewRange = range;
  // Images and PDFs only have a visual preview.
  if (['image', 'pdf'].includes(data.preview_kind)) renderMode = 'render';
  updateToggleButton(data.name, renderMode, data.preview_kind === 'notebook');
  if (data !== previous) renderContent(data, renderMode);
  return true;
}

function initPanelResize() {
  if (!els.panel || !els.resizer) return;

  loadSavedPanelWidth();
  window.addEventListener('resize', () => applyPanelWidth(panelWidth));

  els.resizer.addEventListener('pointerdown', event => {
    if (window.innerWidth <= 1024) return;
    event.preventDefault();
    els.resizer.setPointerCapture?.(event.pointerId);
    els.panel.classList.add('resizing');
    document.body.classList.add('file-panel-resizing');

    const onMove = moveEvent => {
      applyPanelWidth(window.innerWidth - moveEvent.clientX);
    };

    const onUp = upEvent => {
      if (els.resizer.hasPointerCapture?.(upEvent.pointerId)) {
        els.resizer.releasePointerCapture(upEvent.pointerId);
      }
      els.panel.classList.remove('resizing');
      document.body.classList.remove('file-panel-resizing');
      savePanelWidth(panelWidth);
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onUp);
    };

    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp, { once: true });
    window.addEventListener('pointercancel', onUp, { once: true });
  });
}

export async function refreshFilePanel({ keepPreview = true } = {}) {
  if (!els.panel || !isOpen) return;

  const path = keepPreview ? selectedPath : null;
  const range = currentFileData?.previewRange || '';
  const requestId = previewRequestId;
  try {
    await loadFileList();
    if (path && requestId === previewRequestId && els.panel.classList.contains('preview-open')) {
      await loadFilePreview(path, range);
    }
  } catch (err) {
    console.warn('Could not refresh workspace files', err);
  }
}

export function resetFilePanel() {
  ++listRequestId;
  ++treeRenderId;
  els.list?.replaceChildren();
  expandedPaths.clear();
  expandedPaths.add(WORKSPACE_ROOT);
  closePreview();
  // Preserve the open/closed panel state; reset expansion and preview for the new chat.
}


export function initFilePanel() {
  els = {
    panel:         document.getElementById('file-panel'),
    resizer:       document.getElementById('file-panel-resizer'),
    toggle:        document.getElementById('btn-toggle-files'),
    close:         document.getElementById('btn-close-files'),
    refresh:       document.getElementById('btn-refresh-files'),
    previewRefresh: document.getElementById('btn-refresh-files-preview'),
    previewClose:  document.getElementById('btn-close-files-preview'),
    back:          document.getElementById('btn-back-files'),
    list:          document.getElementById('file-panel-list'),
    title:         document.getElementById('file-preview-title'),
    body:          document.getElementById('file-preview-body'),
    copy:          document.getElementById('btn-copy-file'),
    download:      document.getElementById('btn-download-file'),
    toggleRender:  document.getElementById('btn-toggle-render'),
  };

  initPanelResize();
  setPanelOpen(storage.get(STORAGE_KEYS.filePanelOpen, false), { persist: false });
  setEmptyPreview();
  setPreviewOpen(false);

  els.toggle?.addEventListener('click', () => setPanelOpen(!isOpen));
  const closePanel = () => setPanelOpen(false);
  const refreshPanel = () => refreshFilePanel({ keepPreview: Boolean(selectedPath) })
    .catch(err => showToast(err.message || 'Could not refresh files'));

  els.close?.addEventListener('click', closePanel);
  els.previewClose?.addEventListener('click', closePanel);
  els.back?.addEventListener('click', closePreview);
  els.refresh?.addEventListener('click', refreshPanel);
  els.previewRefresh?.addEventListener('click', refreshPanel);

  els.toggleRender?.addEventListener('click', () => {
    if (!currentFileData) return;
    renderMode = renderMode === 'render' ? 'code' : 'render';
    updateToggleButton(currentFileData.name, renderMode, currentFileData.preview_kind === 'notebook');
    renderContent(currentFileData, renderMode);
  });

  els.copy?.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(currentFileData?.content || '');
      showToast('Copied file content');
    } catch {
      showToast('Copy failed');
    }
  });

  els.download?.addEventListener('click', () => {
    const apiBase = workspaceApiBase();
    if (!apiBase || !els.download.dataset.path) return;
    const link = document.createElement('a');
    link.href = `${apiBase}/files/download?path=${encodeURIComponent(els.download.dataset.path)}`;
    link.download = '';
    link.click();
  });

  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && isOpen && !document.querySelector('.modal-overlay.open, #model-popover.open')) {
      setPanelOpen(false);
    }
  });

  document.addEventListener('lumen:open-workspace-file', async e => {
    const path = e.detail?.path;
    if (!path) return;
    setPanelOpen(true);
    if (e.detail?.reveal) {
      // Native Read can target a directory. Expand its path in the workspace tree.
      const apiBase = workspaceApiBase();
      if (!apiBase) return;
      const payload = await api.get(`${apiBase}/files?path=${encodeURIComponent(path)}`).catch(() => ({ error: "Could not inspect path" }));
      if (apiBase !== workspaceApiBase()) return;
      if (!payload.error) {
        let current = WORKSPACE_ROOT;
        for (const part of path.slice(WORKSPACE_ROOT.length).split('/').filter(Boolean)) {
          current += '/' + part;
          expandedPaths.add(current);
        }
        closePreview();
        await loadFileList();
        return;
      }
    }
    loadFilePreview(path).catch(() => {});
  });
}
