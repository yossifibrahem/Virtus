// App entry point — event binding and boot sequence.
//
// This module's only job is to wire UI events to domain functions.
// No business logic lives here.

import { STORAGE_KEYS, state } from './state.js';
import { storage }  from './storage.js';

import { openModal, closeModal, toggleSidebar, initSidebar, initTabs, autoResize, updateCharCount, initMobileKeyboardHandling, showToast } from './ui.js';
import { loadSettings, saveSettings, saveChatSettings, syncSettingsUI, fetchModels, initKeyToggle, initParameterSliders } from './settings.js';
import { bindConversationSearch, createFolder, loadConversationList, openConversation, renameConversationTitle, startNewChat, persistConversationFor } from './conversations.js';
import { loadMcpConfig, saveMcpConfig, applyMcpToolSettings, resetMcpDraftSettings, reloadTools, loadCachedTools } from './mcp.js';
import { sendMessage, stopAssistantTurn, editAndResend, regenerateFrom } from './chat.js';
import { initImageAttachments, hasPendingAttachments } from './chat_attachments.js';
import { initVoiceInput } from './voice.js';
import { clearMessages } from './renderer.js';
import { initIcons } from './icons.js';
import { loadCustomization, saveCustomization, initSwatchPicker, syncCustomizationUI } from './customization.js';
import { initFilePanel } from './file_panel.js';
import { loadMemorySettings, deleteAllData } from './workspace_settings.js';
import { switchBranch } from './chat_branches.js';

// ── Event binding ─────────────────────────────────────────────────────────────

function bindSidebarEvents() {
  document.getElementById('btn-toggle-sidebar').addEventListener('click', () => toggleSidebar());
  document.getElementById('btn-new-chat').addEventListener('click', () => startNewChat());
  document.getElementById('btn-new-folder').addEventListener('click', createFolder);

  bindConversationSearch();
}

function bindModelPickerEvents() {
  const modelBadge   = document.getElementById('model-badge');
  const modelPopover = document.getElementById('model-popover');
  const viewportMargin = 8;
  const popoverGap = 8;

  function positionModelPopover() {
    if (!modelPopover.classList.contains('open')) return;

    const badgeRect = modelBadge.getBoundingClientRect();
    const headerBottom = document.getElementById('chat-header')?.getBoundingClientRect().bottom || 0;
    const viewportWidth = window.visualViewport?.width || window.innerWidth;
    const viewportHeight = window.visualViewport?.height || window.innerHeight;
    const popoverWidth = Math.min(260, viewportWidth - (viewportMargin * 2));
    const spaceAbove = badgeRect.top - popoverGap - headerBottom - viewportMargin;
    const spaceBelow = viewportHeight - badgeRect.bottom - popoverGap - viewportMargin;
    const openDownward = spaceBelow > spaceAbove;
    const availableHeight = Math.max(0, openDownward ? spaceBelow : spaceAbove);
    const unclampedLeft = badgeRect.left;
    const clampedLeft = Math.min(
      Math.max(viewportMargin, unclampedLeft),
      viewportWidth - popoverWidth - viewportMargin,
    );

    modelPopover.classList.toggle('open-downward', openDownward);
    modelPopover.style.left = `${clampedLeft - badgeRect.left}px`;
    modelPopover.style.setProperty('--model-popover-max-height', `${availableHeight}px`);
  }

  function closeModelPopover() {
    modelPopover.classList.remove('open', 'open-downward');
    modelBadge.classList.remove('open');
    modelBadge.setAttribute('aria-expanded', 'false');
    modelPopover.inert = true;
  }

  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && modelPopover.classList.contains('open')) {
      closeModelPopover();
      modelBadge.focus();
      event.stopImmediatePropagation();
    }
  });
  modelBadge.addEventListener('click', e => {
    e.stopPropagation();
    const isOpen = modelPopover.classList.toggle('open');
    modelBadge.classList.toggle('open', isOpen);
    modelPopover.inert = !isOpen;
    modelBadge.setAttribute('aria-expanded', String(isOpen));
    if (isOpen) positionModelPopover();
    else modelPopover.classList.remove('open-downward');
  });
  modelPopover.addEventListener('click', event => {
    if (event.target.closest('.mp-chip')) {
      closeModelPopover();
      modelBadge.focus();
    }
  });
  document.addEventListener('click', e => {
    if (!document.getElementById('model-picker-wrap').contains(e.target)) {
      closeModelPopover();
    }
  });
  window.addEventListener('resize', positionModelPopover);
  window.visualViewport?.addEventListener('resize', positionModelPopover);
  window.visualViewport?.addEventListener('scroll', positionModelPopover);
}

function dismissModal(id) {
  closeModal(id);
  if (id === 'settings-modal') {
    syncSettingsUI();
    syncCustomizationUI();
    resetMcpDraftSettings();
  }
}

function bindModalEvents() {
  document.getElementById('btn-open-settings').addEventListener('click', () => openModal('settings-modal'));
  document.querySelectorAll('[data-close]').forEach(btn =>
    btn.addEventListener('click', () => dismissModal(btn.dataset.close))
  );
  document.querySelectorAll('.modal-overlay').forEach(overlay =>
    overlay.addEventListener('click', event => {
      if (event.target === overlay) dismissModal(overlay.id);
    })
  );

  initTabs(document.querySelector('#settings-modal .modal-tabs'));
}

async function applyAllSettings() {
  const btn = document.getElementById('btn-apply-settings');
  if (btn) btn.disabled = true;

  try {
    await saveSettings();
    saveChatSettings();
    saveCustomization();
    await saveMcpConfig();
    applyMcpToolSettings();

    showToast('Settings applied');
  } catch (err) {
    console.error('Failed to apply settings:', err);
    showToast('Failed to apply settings');
  } finally {
    if (btn) btn.disabled = false;
  }
}

function bindSettingsEvents() {
  document.getElementById('btn-apply-settings').addEventListener('click', applyAllSettings);
  document.getElementById('btn-fetch-models').addEventListener('click', fetchModels);
  document.getElementById('btn-reload-tools').addEventListener('click', reloadTools);

  document.getElementById('btn-danger-delete-all').addEventListener('click', async () => {
    const btn      = document.getElementById('btn-danger-delete-all');
    const statusEl = document.getElementById('danger-delete-status');

    // Two-click confirmation: first click arms the button, second executes.
    if (!btn.dataset.armed) {
      btn.dataset.armed    = '1';
      btn.textContent      = 'Click again to confirm';
      btn.style.opacity    = '1';
      statusEl.textContent = 'This will permanently delete ALL chats and container workspaces.';
      statusEl.className   = 'danger-status warn';
      // Auto-disarm after 5 s if the user changes their mind.
      setTimeout(() => {
        delete btn.dataset.armed;
        btn.textContent      = 'Delete all chats & containers';
        btn.style.opacity    = '';
        statusEl.textContent = '';
        statusEl.className   = 'danger-status';
      }, 5000);
      return;
    }

    // Confirmed — execute.
    delete btn.dataset.armed;
    btn.disabled    = true;
    btn.textContent = 'Deleting…';
    statusEl.textContent = '';
    statusEl.className   = 'danger-status';

    const result = await deleteAllData();

    btn.disabled    = false;
    btn.textContent = 'Delete all chats & containers';

    if (result.ok) {
      // Reset the in-memory UI to an empty state.
      startNewChat();
      await loadConversationList();
      statusEl.textContent = `Deleted ${result.deleted} conversation${result.deleted === 1 ? '' : 's'}.`;
      statusEl.className   = 'danger-status ok';
    } else {
      statusEl.textContent = `Error: ${result.error}`;
      statusEl.className   = 'danger-status err';
    }
  });
  initSwatchPicker();
  initKeyToggle();
  initParameterSliders();
}

function bindInputEvents() {
  const userInput = document.getElementById('user-input');
  const sendBtn   = document.getElementById('send-btn');

  const updateSendButton = () => {
    if (!sendBtn) return;
    sendBtn.disabled = state.isStreaming || (userInput.value.trim() === '' && !hasPendingAttachments());
  };

  const submitInput = () => {
    if (state.isStreaming) return;
    const text = userInput.value.trim();
    if (!text && !hasPendingAttachments()) return; // don't submit empty messages unless files/images are attached
    userInput.value = '';
    autoResize(userInput);
    updateCharCount();
    sendMessage(text).catch(error => showToast(error.message || 'Could not send message'));
    updateSendButton();
  };

  document.getElementById('send-btn').addEventListener('click', submitInput);
  userInput.addEventListener('input',   () => { autoResize(userInput); updateCharCount(); updateSendButton(); });
  document.addEventListener('chat:attachments-changed', updateSendButton);
  userInput.addEventListener('keydown', e => {
    if (e.key !== 'Enter' || e.isComposing) return;
    const wantsSend = state.enterToSend ? !e.shiftKey : e.shiftKey;
    if (wantsSend) { e.preventDefault(); submitInput(); }
  });

  document.getElementById('stop-btn').addEventListener('click', stopAssistantTurn);

  initImageAttachments();
  initVoiceInput();

  // Chat title persistence
  const titleInput = document.getElementById('chat-title-input');
  titleInput.addEventListener('change', renameConversationTitle);
  titleInput.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); e.target.blur(); } });


  // Edit & Resend — dispatched from renderer when user confirms an edit
  document.getElementById('messages').addEventListener('chat:edit-resend', e => {
    const { logIndex, newText, imageUrls, files, attachments } = e.detail;
    editAndResend(logIndex, newText, imageUrls, files, attachments);
  });

  // Regenerate — dispatched from renderer when user clicks regenerate
  document.getElementById('messages').addEventListener('chat:regenerate', e => {
    regenerateFrom(e.detail.logIndex);
  });

  // Branch switching — dispatched from message footers when alternatives exist.
  document.getElementById('messages').addEventListener('chat:switch-branch', e => {
    switchBranch(e.detail.logIndex, e.detail.variantIndex, persistConversationFor);
  });

  // Initialize send button state
  updateSendButton();
}

function bindKeyboardEvents() {
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
      document.querySelectorAll('.modal-overlay.open').forEach(modal => dismissModal(modal.id));
    }
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
      e.preventDefault();
      startNewChat();
    }
  });
}

function bindEvents() {
  bindSidebarEvents();
  bindModelPickerEvents();
  bindModalEvents();
  bindSettingsEvents();
  bindInputEvents();
  bindKeyboardEvents();
}

// ── Boot sequence ─────────────────────────────────────────────────────────────

(async () => {
  initIcons();
  initFilePanel();
  initMobileKeyboardHandling();   // must run early — sets --vvh before first paint
  bindEvents();
  loadSettings();
  loadCustomization();
  const hasCachedTools = loadCachedTools();
  await loadMemorySettings();
  await loadConversationList();
  await loadMcpConfig();
  // Render a cache immediately, but always refresh it so MCP tool
  // and config changes cannot remain hidden after an app upgrade.
  const toolRefresh = reloadTools();
  if (!hasCachedTools) await toolRefresh;

  const lastConvId = storage.get(STORAGE_KEYS.lastConv);
  if (lastConvId) {
    try {
      await openConversation(lastConvId);
    } catch {
      storage.remove(STORAGE_KEYS.lastConv);
      clearMessages();
    }
  } else {
    clearMessages(); // Show empty state when no conversation exists
  }

  initSidebar(state.sidebarDefaultOpen);
})();
