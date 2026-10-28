// Conversation management — talking to /api/conversations and updating the sidebar.

import { api }         from './api.js';
import { state, STORAGE_KEYS } from './state.js';
import { storage } from './storage.js';
import { clearMessages, renderAllMessages } from './renderer.js';
import { toggleSidebar, openModal, closeModal, showToast } from './ui.js';
import { ICONS } from './icons.js';
import { escapeHtml, formatConversationDate } from './format.js';
import { refreshFilePanel, resetFilePanel } from './file_panel.js';
import { activityVersion, workingTitleAttributes, syncConversationActivity } from './conversation_activity.js';

let navigationId = 0;
let listLoadId = 0;
let folders = [];
let conversations = [];
let pendingFolderId = null;
let activeFolderId = null;
let editingFolder = null;
let folderEditorBound = false;
let folderEditorVersion = 0;
let folderEditorBusy = false;
let expandedFolderIds = null;

// ── Sidebar list ──────────────────────────────────────────────────────────────

export async function loadConversationList() {
  const requestId = ++listLoadId;
  const version = activityVersion;
  const [list, folderList] = await Promise.all([
    api.get('/api/conversations'),
    api.get('/api/folders'),
  ]);
  if (requestId !== listLoadId) return;
  if (!Array.isArray(list) || !Array.isArray(folderList)) {
    throw new Error(list?.error || folderList?.error || 'Could not load conversations');
  }
  conversations = list;
  folders = folderList;
  syncConversationActivity(list, version);
  const container = document.getElementById('conv-list');
  container.innerHTML = '';

  if (!list.length && !folders.length) {
    container.innerHTML = '<div class="conv-section-label conv-empty">No conversations yet</div>';
    renderConversationSearchResults();
    return;
  }

  const folderConversations = new Map(folders.map(folder => [folder.id, []]));
  const unfiled = [];
  for (const conv of list) {
    if (folderConversations.has(conv.folder_id)) folderConversations.get(conv.folder_id).push(conv.id);
    else unfiled.push(conv);
  }

  if (folders.length) {
    container.appendChild(Object.assign(document.createElement('div'), {
      className: 'conv-section-label folders-section-label', textContent: 'Folders',
    }));
    folders.forEach(folder => container.appendChild(_buildFolderGroup(folder, folderConversations.get(folder.id))));
  }

  if (unfiled.length) {
    container.appendChild(Object.assign(document.createElement('div'), {
      className: 'conv-section-label recent-section-label', textContent: 'Recent',
    }));
    unfiled.forEach(conv => container.appendChild(_buildConvItem(conv)));
  }

  renderConversationSearchResults();
}

function renderConversationSearchResults() {
  const input = document.getElementById('conv-search');
  const results = document.getElementById('search-results');
  if (!input || !results) return;

  const query = input.value.trim().toLocaleLowerCase();
  const matches = conversations.filter(conv => {
    const folderName = folderById(conv.folder_id)?.name || '';
    return !query
      || (conv.title || 'Untitled').toLocaleLowerCase().includes(query)
      || folderName.toLocaleLowerCase().includes(query);
  });

  if (!matches.length) {
    results.innerHTML = `<div class="folder-home-no-chats">${query
      ? `No conversations found for “${escapeHtml(input.value.trim())}”`
      : 'No conversations yet'}</div>`;
    return;
  }

  results.innerHTML = matches.map(conv => {
    const folderName = folderById(conv.folder_id)?.name || '';
    return `
      <div class="folder-home-chat search-result" data-conv-id="${escapeHtml(conv.id)}">
        <button class="folder-home-chat-open search-result-open" type="button">
          <span class="search-result-copy">
            <span class="folder-home-chat-title" ${workingTitleAttributes(conv.id)}>${escapeHtml(conv.title || 'Untitled')}</span>
            ${folderName ? `<span class="search-result-folder">${escapeHtml(folderName)}</span>` : ''}
          </span>
          <time>${escapeHtml(formatConversationDate(conv.updated_at))}</time>
        </button>
      </div>`;
  }).join('');

  results.querySelectorAll('.search-result-open').forEach(button => {
    button.addEventListener('click', async () => {
      const convId = button.closest('.search-result')?.dataset.convId;
      if (!convId) return;
      closeModal('search-modal');
      await openConversation(convId);
    });
  });
}

export function bindConversationSearch() {
  const button = document.getElementById('btn-open-search');
  const input = document.getElementById('conv-search');
  if (!button || !input || button.dataset.bound) return;
  button.dataset.bound = 'true';

  button.addEventListener('click', () => {
    renderConversationSearchResults();
    openModal('search-modal');
    requestAnimationFrame(() => input.focus());
  });
  input.addEventListener('input', renderConversationSearchResults);
}

function _buildFolderGroup(folder, conversationIds) {
  if (!expandedFolderIds) {
    const saved = storage.get(STORAGE_KEYS.expandedFolders, []);
    expandedFolderIds = new Set(Array.isArray(saved) ? saved.filter(id => typeof id === 'string') : []);
  }
  const group = document.createElement('section');
  group.className = `folder-group${!state.convId && folder.id === state.folderId ? ' active' : ''}`;
  group.dataset.folderId = folder.id;
  group.innerHTML = `
    <div class="folder-header">
      <button class="folder-open" type="button" aria-expanded="false" aria-controls="folder-chats-${escapeHtml(folder.id)}">
        <span class="folder-icon">${ICONS.folder}</span>
        <span class="folder-name" ${workingTitleAttributes(conversationIds)}>${escapeHtml(folder.name || 'Untitled Folder')}</span>
      </button>
      <div class="folder-controls">
        <button class="conv-menu-btn folder-new-chat" type="button" title="New chat in folder" aria-label="New chat in ${escapeHtml(folder.name || 'Untitled Folder')}">${ICONS.plus}</button>
        <div class="folder-menu-wrap">
          <button class="conv-menu-btn folder-menu-btn" type="button" title="Folder options" aria-label="Folder options" aria-expanded="false">${ICONS.moreVertical}</button>
          <div class="conv-menu folder-menu" role="menu">
            <button class="conv-menu-item" type="button" role="menuitem" data-folder-action="open">
              ${ICONS.folderOpen}<span>Open folder</span>
            </button>
            <button class="conv-menu-item" type="button" role="menuitem" data-folder-action="rename">
              ${ICONS.edit}<span>Folder settings</span>
            </button>
            <button class="conv-menu-item danger" type="button" role="menuitem" data-folder-action="remove">
              ${ICONS.trash}<span>Delete folder</span>
            </button>
          </div>
        </div>
      </div>
    </div>
    <div class="folder-chats" id="folder-chats-${escapeHtml(folder.id)}" hidden></div>`;

  const children = group.querySelector('.folder-chats');
  const chatById = new Map(conversations.map(conv => [conv.id, conv]));
  conversationIds.forEach(id => children.appendChild(_buildConvItem(chatById.get(id))));
  if (!conversationIds.length) {
    children.appendChild(Object.assign(document.createElement('div'), {
      className: 'folder-chats-empty', textContent: 'No chats yet',
    }));
  }
  setFolderExpanded(group, expandedFolderIds.has(folder.id));
  const toggle = group.querySelector('.folder-open');
  toggle.addEventListener('click', () => {
    setFolderExpanded(group, children.hidden);
  });
  toggle.addEventListener('keydown', event => {
    if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
      event.preventDefault();
      if (event.key === 'ArrowRight' && !children.hidden) children.querySelector('.conv-open')?.focus();
      else setFolderExpanded(group, event.key === 'ArrowRight');
    }
  });
  group.querySelector('.folder-new-chat').addEventListener('click', () => startNewChat(folder.id));
  group.querySelector('[data-folder-action="open"]').addEventListener('click', () => {
    closeFolderMenus();
    startNewChat(folder.id);
  });
  const menuBtn = group.querySelector('.folder-menu-btn');
  menuBtn.addEventListener('click', event => {
    event.stopPropagation();
    const willOpen = !group.classList.contains('folder-menu-open');
    closeFolderMenus(group);
    group.classList.toggle('folder-menu-open', willOpen);
    menuBtn.setAttribute('aria-expanded', String(willOpen));
  });
  group.querySelector('[data-folder-action="rename"]').addEventListener('click', event => {
    event.stopPropagation();
    closeFolderMenus();
    renameFolder(folder);
  });
  group.querySelector('[data-folder-action="remove"]').addEventListener('click', event => {
    event.stopPropagation();
    closeFolderMenus();
    removeFolder(folder);
  });
  return group;
}

function setFolderExpanded(group, expanded) {
  group.querySelector('.folder-chats').hidden = !expanded;
  group.querySelector('.folder-open').setAttribute('aria-expanded', String(expanded));
  group.querySelector('.folder-icon').innerHTML = expanded ? ICONS.folderOpen : ICONS.folder;
  group.classList.toggle('expanded', expanded);
  if (expanded) expandedFolderIds.add(group.dataset.folderId);
  else expandedFolderIds.delete(group.dataset.folderId);
  storage.set(STORAGE_KEYS.expandedFolders, [...expandedFolderIds]);
}

function closeFolderMenus(exceptGroup = null) {
  document.querySelectorAll('.folder-group.folder-menu-open').forEach(group => {
    if (group === exceptGroup) return;
    group.classList.remove('folder-menu-open');
    group.querySelector('.folder-menu-btn')?.setAttribute('aria-expanded', 'false');
  });
}

function _buildConvItem(conv) {
  const item   = document.createElement('div');
  item.className = `conv-item${conv.id === state.convId ? ' active' : ''}`;
  item.dataset.id = conv.id;

  const title = conv.title || 'Untitled';

  item.innerHTML = `
    <button class="conv-open" type="button" title="${escapeHtml(title)}"${conv.id === state.convId ? ' aria-current="page"' : ''}>
      <span class="conv-title" ${workingTitleAttributes(conv.id)}>${escapeHtml(title)}</span>
    </button>
    <div class="conv-menu-wrap">
      <button class="conv-menu-btn" type="button" title="Conversation options" aria-label="Conversation options" aria-expanded="false">
        ${ICONS.moreVertical}
      </button>
      <div class="conv-menu" role="menu">
        <button class="conv-menu-item" type="button" role="menuitem" data-action="rename">
          ${ICONS.edit}
          <span>Rename</span>
        </button>
        <button class="conv-menu-item" type="button" role="menuitem" data-action="move">
          ${ICONS.folder}
          <span>Move to folder</span>
        </button>
        <button class="conv-menu-item danger" type="button" role="menuitem" data-action="remove">
          ${ICONS.trash}
          <span>Delete</span>
        </button>
      </div>
    </div>`;

  item.querySelector('.conv-open').addEventListener('click', () => openConversation(conv.id));

  const menuBtn = item.querySelector('.conv-menu-btn');
  menuBtn.addEventListener('click', e => {
    e.stopPropagation();
    toggleConversationMenu(item);
  });

  item.querySelector('[data-action="rename"]').addEventListener('click', e => {
    e.stopPropagation();
    closeConversationMenus();
    renameConversation(conv.id);
  });

  item.querySelector('[data-action="remove"]').addEventListener('click', e => {
    e.stopPropagation();
    closeConversationMenus();
    deleteConversation(conv.id);
  });

  item.querySelector('[data-action="move"]').addEventListener('click', e => {
    e.stopPropagation();
    closeConversationMenus();
    moveConversation(conv);
  });

  return item;
}

function closeConversationMenus(exceptItem = null) {
  document.querySelectorAll('.conv-item.menu-open, .folder-home-chat.menu-open').forEach(item => {
    if (item === exceptItem) return;
    item.classList.remove('menu-open');
    item.querySelector('.conv-menu-btn')?.setAttribute('aria-expanded', 'false');
  });
}

function toggleConversationMenu(item) {
  const willOpen = !item.classList.contains('menu-open');
  closeConversationMenus(item);
  item.classList.toggle('menu-open', willOpen);
  item.querySelector('.conv-menu-btn')?.setAttribute('aria-expanded', String(willOpen));
}

document.addEventListener('click', e => {
  if (!e.target.closest('.conv-menu-wrap')) closeConversationMenus();
  if (!e.target.closest('.folder-menu-wrap')) closeFolderMenus();
});

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    closeConversationMenus();
    closeFolderMenus();
  }
});

// ── CRUD ──────────────────────────────────────────────────────────────────────

function updateTitleInput(title) {
  const titleInput = document.getElementById('chat-title-input');
  if (!titleInput || document.activeElement === titleInput) return;
  titleInput.value = title || '';
}

function folderById(folderId) {
  return folders.find(folder => folder.id === folderId) || null;
}

function folderEmptyContext(folderId, excludeConvId = null) {
  const folder = folderById(folderId);
  if (!folder) return {};
  return {
    folder,
    conversations: conversations.filter(conv => conv.folder_id === folderId && conv.id !== excludeConvId),
  };
}

function updateHeaderFolder(folderId, title = '') {
  activeFolderId = folderId || null;
  state.folderId = activeFolderId;
  const folder = folderById(activeFolderId);
  state.folderSystemPrompt = folder?.system_prompt || '';
  const prefix = document.getElementById('chat-folder-prefix');
  const separator = document.getElementById('chat-title-separator');
  const titleInput = document.getElementById('chat-title-input');
  const hasFolder = Boolean(folder);
  prefix.textContent = folder?.name || '';
  prefix.setAttribute('aria-label', folder ? `Open folder ${folder.name}` : 'Open folder');
  prefix.hidden = !hasFolder;
  separator.hidden = !hasFolder;
  if (titleInput && document.activeElement !== titleInput) titleInput.value = title || '';
}

function updateConversationListTitle(convId, title) {
  if (!convId || !title) return;

  const cached = conversations.find(conv => conv.id === convId);
  if (cached) {
    cached.title = title;
    cached.updated_at = new Date().toISOString();
  }

  const item = document.querySelector(`.conv-item[data-id="${CSS.escape(convId)}"]`);
  const titleEl = item?.querySelector('.conv-title');
  const folderHomeTitle = document.querySelector(
    `.folder-home-chat[data-conv-id="${CSS.escape(convId)}"] .folder-home-chat-title`,
  );
  if (folderHomeTitle) folderHomeTitle.textContent = title;
  renderConversationSearchResults();
  if (!titleEl || titleEl.textContent === title) return;

  titleEl.textContent = title;
  item.querySelector('.conv-open').title = title;
}

function setActiveConversationItem(convId) {
  document.querySelectorAll('.folder-group').forEach(group => {
    group.classList.toggle('active', !convId && group.dataset.folderId === state.folderId);
    if (group.dataset.folderId === state.folderId) setFolderExpanded(group, true);
  });
  document.querySelectorAll('.conv-item.active').forEach(item => {
    item.classList.remove('active');
    item.querySelector('.conv-open')?.removeAttribute('aria-current');
  });
  const item = convId ? document.querySelector(`.conv-item[data-id="${CSS.escape(convId)}"]`) : null;
  item?.classList.add('active');
  item?.querySelector('.conv-open')?.setAttribute('aria-current', 'page');
}

function upsertConversationListItem(conv) {
  const container = document.getElementById('conv-list');
  if (!container || !conv?.id) return;

  const cachedIndex = conversations.findIndex(item => item.id === conv.id);
  if (cachedIndex >= 0) conversations[cachedIndex] = { ...conversations[cachedIndex], ...conv };
  else conversations.unshift(conv);

  const existing = container.querySelector(`.conv-item[data-id="${CSS.escape(conv.id)}"]`);
  if (existing) {
    updateConversationListTitle(conv.id, conv.title || 'New Conversation');
    setActiveConversationItem(conv.id);
    return;
  }

  if (conv.folder_id) {
    loadConversationList().then(() => setActiveConversationItem(conv.id));
    return;
  }
  if (container.querySelector('.conv-empty')) container.innerHTML = '';
  let recentLabel = container.querySelector('.recent-section-label');
  if (!recentLabel) {
    recentLabel = Object.assign(document.createElement('div'), {
      className: 'conv-section-label recent-section-label',
      textContent: 'Recent',
    });
    container.appendChild(recentLabel);
  }

  container.insertBefore(_buildConvItem({
    title: 'New Conversation',
    ...conv,
  }), recentLabel.nextSibling);
  setActiveConversationItem(conv.id);
}

document.addEventListener('chat:conversation-title-updated', event => {
  const { convId, title } = event.detail || {};
  updateConversationListTitle(convId, title);
});

function applyConversationData(id, data, { render = true } = {}) {
  if (state.convId !== id) return;

  state.messages   = data.messages || [];
  state.displayLog = data.displayLog || [];

  updateHeaderFolder(data.folder_id, data.title || '');
  updateConversationListTitle(id, data.title || '');
  if (render) {
    if (state.displayLog.length) renderAllMessages(state.displayLog);
    else clearMessages(folderEmptyContext(data.folder_id, id));
  }
}

export async function openConversation(id) {
  const requestId = ++navigationId;
  const data = await api.get(`/api/conversations/${id}`);
  if (requestId !== navigationId) return;
  if (data.error) throw new Error(data.error);
  state.convId = id;
  storage.set(STORAGE_KEYS.lastConv, id);
  const hasActiveStream = Boolean(data.active_stream_id);
  applyConversationData(id, data, { render: !hasActiveStream });
  resetFilePanel();
  refreshFilePanel();

  document.dispatchEvent(new CustomEvent('chat:conversation-opened', { detail: { convId: id, data } }));
  setActiveConversationItem(id);
  if (window.innerWidth <= 768) toggleSidebar(false);
}

// Resets the UI to an empty chat without touching the server.
// The actual conversation record is created lazily on the first send (chat.js).
export function startNewChat(folderId = null) {
  ++navigationId;
  pendingFolderId = folderId;
  state.convId     = null;
  state.messages   = [];
  state.displayLog = [];
  storage.remove(STORAGE_KEYS.lastConv);
  updateHeaderFolder(folderId, '');
  clearMessages(folderEmptyContext(folderId));
  resetFilePanel();
  refreshFilePanel();
  document.dispatchEvent(new CustomEvent('chat:conversation-opened', {
    detail: { convId: null, data: folderId ? { folder_id: folderId } : {} },
  }));
  setActiveConversationItem(null);
  if (window.innerWidth <= 768) toggleSidebar(false);
}

// Creates a conversation record on the server. Called by chat.js before the first send.
export async function createNewConversation() {
  const requestId = ++navigationId;
  const data = await api.post('/api/conversations', {
    title: 'New Conversation',
    ...(pendingFolderId ? { folder_id: pendingFolderId } : {}),
  });
  if (data.error || !data.id) throw new Error(data.error || 'Could not create conversation');
  if (requestId !== navigationId) {
    await loadConversationList();
    return data;
  }
  pendingFolderId = null;
  state.convId     = data.id;
  state.messages   = [];
  state.displayLog = [];
  storage.set(STORAGE_KEYS.lastConv, data.id);
  updateHeaderFolder(data.folder_id, 'New Conversation');
  resetFilePanel();
  refreshFilePanel();
  upsertConversationListItem(data);
  return data;
}

export function renameConversation(convId) {
  if (!convId) return;

  const currentTitle = document.querySelector(
    `.conv-item[data-id="${CSS.escape(convId)}"] .conv-title, `
    + `.folder-home-chat[data-conv-id="${CSS.escape(convId)}"] .folder-home-chat-title`,
  )?.textContent || 'Untitled';
  const input   = document.getElementById('rename-conv-input');
  const confirm = document.getElementById('rename-conv-confirm');
  const overlay = document.getElementById('rename-conv-modal');

  input.value = currentTitle;
  openModal('rename-conv-modal');
  requestAnimationFrame(() => { input.focus(); input.select(); });

  // Clone to drop any previous listeners
  const freshConfirm = confirm.cloneNode(true);
  confirm.replaceWith(freshConfirm);

  const cleanup = () => closeModal('rename-conv-modal');

  const submit = async () => {
    input.removeEventListener('keydown', keyHandler);
    const nextTitle = input.value.trim() || 'Untitled';
    cleanup();
    await persistConversationFor(convId, { title: nextTitle });
    if (state.convId === convId) updateTitleInput(nextTitle);
  };

  function keyHandler(e) {
    if (e.isComposing) return;
    if (e.key === 'Enter')  { submit(); }
    if (e.key === 'Escape') { input.removeEventListener('keydown', keyHandler); cleanup(); }
  }

  freshConfirm.addEventListener('click', submit);
  input.addEventListener('keydown', keyHandler);
}

async function applyMutation(request) {
  try {
    const data = await request;
    if (data.error) throw new Error(data.error);
    return data;
  } catch (error) {
    showToast(error.message || 'Could not save changes');
    return null;
  }
}

export function deleteConversation(convId) {
  const confirmBtn = document.getElementById('delete-conv-confirm');
  openModal('delete-conv-modal');

  // Clone to drop any previous listeners, then re-attach once
  const freshConfirm = confirmBtn.cloneNode(true);
  confirmBtn.replaceWith(freshConfirm);

  freshConfirm.addEventListener('click', async () => {
    closeModal('delete-conv-modal');
    if (!await applyMutation(api.delete(`/api/conversations/${convId}`))) return;
    const deletingActive = state.convId === convId;
    if (deletingActive) startNewChat();
    await loadConversationList();
    if (!deletingActive && !state.convId && activeFolderId) {
      clearMessages(folderEmptyContext(activeFolderId));
    }
  }, { once: true });
}

export async function persistConversationFor(convId, { title, messages, displayLog }) {
  if (!convId) return;
  if ((messages !== undefined || displayLog !== undefined) && state.pendingHistoryReloads?.has(convId)) {
    throw new Error('History recovery is incomplete. Reopen this chat before saving changes.');
  }

  const payload = {};
  if (title !== undefined) payload.title = title || 'Untitled';
  if (messages !== undefined) payload.messages = messages || [];
  if (displayLog !== undefined) payload.displayLog = displayLog || [];

  await api.put(`/api/conversations/${convId}`, payload);

  if (payload.title) updateConversationListTitle(convId, payload.title);
}

export function createFolder() {
  openFolderEditor();
}

function renameFolder(folder) {
  openFolderEditor(folder);
}

function openFolderEditor(folder = null) {
  editingFolder = folder;
  ++folderEditorVersion;
  document.getElementById('folder-editor-error').textContent = '';
  const input = document.getElementById('folder-name-input');
  const title = document.getElementById('folder-modal-title');
  const confirm = document.getElementById('folder-modal-confirm');
  input.value = folder?.name || '';
  document.getElementById('folder-instructions-input').value = folder?.system_prompt || '';
  title.textContent = folder ? 'Folder settings' : 'New folder';
  confirm.textContent = folder ? 'Save' : 'Create';
  updateFolderEditorControls();
  openModal('folder-modal');
  requestAnimationFrame(() => { input.focus(); input.select(); });

  if (!folderEditorBound) {
    confirm.addEventListener('click', submitFolderEditor);
    input.addEventListener('keydown', event => {
      if (event.isComposing) return;
      if (event.key === 'Enter') submitFolderEditor();
      if (event.key === 'Escape') closeModal('folder-modal');
    });
    folderEditorBound = true;
  }
}

function updateFolderEditorControls() {
  document.querySelectorAll('#folder-modal .modal-body input, #folder-modal .modal-body textarea, #folder-modal .modal-body button').forEach(control => {
    control.disabled = folderEditorBusy;
  });
  document.getElementById('folder-modal-confirm').disabled = folderEditorBusy;
}

async function submitFolderEditor() {
  if (folderEditorBusy) return;
  const input = document.getElementById('folder-name-input');
  const name = input.value.trim();
  if (!name) return;
  const system_prompt = document.getElementById('folder-instructions-input').value.trim();
  const folder = editingFolder;
  const version = folderEditorVersion;
  const error = document.getElementById('folder-editor-error');
  folderEditorBusy = true;
  updateFolderEditorControls();
  error.textContent = '';
  try {
    const updated = folder
      ? await api.put(`/api/folders/${folder.id}`, { name, system_prompt })
      : await api.post('/api/folders', { name, system_prompt });
    if (updated.error) { if (version === folderEditorVersion) error.textContent = updated.error; return; }
    if (version === folderEditorVersion) closeModal('folder-modal');
    await loadConversationList();
    if (folder && activeFolderId === updated.id) {
      updateHeaderFolder(activeFolderId, document.getElementById('chat-title-input').value);
      if (!state.displayLog.length) clearMessages(folderEmptyContext(activeFolderId, state.convId));
      resetFilePanel();
      refreshFilePanel();
    }
  } catch (err) {
    if (version === folderEditorVersion) error.textContent = err.message;
  } finally {
    folderEditorBusy = false;
    updateFolderEditorControls();
  }
}

async function removeFolder(folder) {
  const confirm = document.getElementById('delete-folder-confirm');
  openModal('delete-folder-modal');
  const freshConfirm = confirm.cloneNode(true);
  confirm.replaceWith(freshConfirm);
  freshConfirm.addEventListener('click', async () => {
    closeModal('delete-folder-modal');
    const deletingActiveConversation = conversations.some(
      conv => conv.id === state.convId && conv.folder_id === folder.id,
    );
    const deletingActiveFolder = activeFolderId === folder.id;
    if (!await applyMutation(api.delete(`/api/folders/${folder.id}`))) return;
    if (deletingActiveConversation || deletingActiveFolder) startNewChat();
    await loadConversationList();
  }, { once: true });
}

function moveConversation(conv) {
  const select = document.getElementById('move-conv-folder');
  select.innerHTML = [
    '<option value="">Recent (no folder)</option>',
    ...folders.map(folder => `<option value="${folder.id}">${escapeHtml(folder.name)}</option>`),
  ].join('');
  select.value = conv.folder_id || '';
  const updateWarning = () => {
    document.getElementById('move-conv-warning').hidden = !!conv.folder_id || !select.value;
  };
  select.onchange = updateWarning;
  updateWarning();
  openModal('move-conv-modal');

  const confirm = document.getElementById('move-conv-confirm');
  const freshConfirm = confirm.cloneNode(true);
  confirm.replaceWith(freshConfirm);
  freshConfirm.addEventListener('click', async () => {
    closeModal('move-conv-modal');
    const updated = await applyMutation(api.put(`/api/conversations/${conv.id}`, { folder_id: select.value || null }));
    if (!updated) return;
    await loadConversationList();
    if (state.convId === conv.id) {
      updateHeaderFolder(updated.folder_id, updated.title || '');
      if (!state.displayLog.length) clearMessages(folderEmptyContext(updated.folder_id, conv.id));
      resetFilePanel();
      refreshFilePanel();
    }
  }, { once: true });
}

export async function renameConversationTitle() {
  if (!state.convId) return;

  const titleInput = document.getElementById('chat-title-input');
  const title = titleInput.value.trim() || 'New Conversation';
  titleInput.value = title;

  await persistConversationFor(state.convId, { title });
}

document.addEventListener('chat:open-conversation-requested', event => {
  const convId = event.detail?.convId;
  if (convId) openConversation(convId);
});

document.getElementById('chat-folder-prefix')?.addEventListener('click', () => {
  if (activeFolderId) startNewChat(activeFolderId);
});

document.addEventListener('chat:toggle-conversation-menu-requested', event => {
  const item = event.detail?.item;
  if (item) toggleConversationMenu(item);
});

document.addEventListener('chat:rename-conversation-requested', event => {
  const convId = event.detail?.convId;
  if (convId) {
    closeConversationMenus();
    renameConversation(convId);
  }
});

document.addEventListener('chat:delete-conversation-requested', event => {
  const convId = event.detail?.convId;
  if (convId) {
    closeConversationMenus();
    deleteConversation(convId);
  }
});
