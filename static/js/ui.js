// Generic UI helpers — no domain knowledge, no API calls.

import { storage }     from './storage.js';
import { STORAGE_KEYS } from './state.js';

// ── Toast & status ────────────────────────────────────────────────────────────

let _toastTimer;

export function showToast(message) {
  const toast = document.getElementById('toast');
  toast.textContent = message;
  toast.classList.add('show');
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => toast.classList.remove('show'), 2500);
}

// ── Sidebar ───────────────────────────────────────────────────────────────────

const sidebarMedia = window.matchMedia('(max-width: 768px)');

function getSidebarElements() {
  return {
    sidebar: document.getElementById('sidebar'),
    toggle: document.getElementById('btn-toggle-sidebar'),
    conversations: document.getElementById('conv-list'),
  };
}

function getSidebarBackdrop() {
  let backdrop = document.getElementById('sidebar-backdrop');
  if (backdrop) return backdrop;

  backdrop = document.createElement('div');
  backdrop.id = 'sidebar-backdrop';
  backdrop.addEventListener('click', () => toggleSidebar(false));
  document.body.appendChild(backdrop);
  return backdrop;
}

function applySidebarState(open, { persist = !sidebarMedia.matches } = {}) {
  const { sidebar, toggle, conversations } = getSidebarElements();
  if (!sidebar || !toggle) return;

  const isMobile = sidebarMedia.matches;
  sidebar.inert = isMobile && !open;
  sidebar.classList.toggle('collapsed', !open);
  sidebar.dataset.state = open ? 'open' : (isMobile ? 'hidden' : 'mini');

  // Keep off-screen/hidden controls out of keyboard navigation. The desktop
  // mini rail itself remains interactive so New chat and Settings still work.
  if (conversations) conversations.inert = !open;

  const action = open ? 'Close sidebar' : 'Open sidebar';
  toggle.setAttribute('aria-expanded', String(open));
  toggle.setAttribute('aria-label', action);
  toggle.title = action;

  const backdrop = isMobile ? getSidebarBackdrop() : document.getElementById('sidebar-backdrop');
  backdrop?.classList.toggle('visible', isMobile && open);

  if (persist) storage.set(STORAGE_KEYS.sidebar, open);
}

export function toggleSidebar(forceOpen) {
  const sidebar  = document.getElementById('sidebar');
  const open     = forceOpen !== undefined ? forceOpen : sidebar.classList.contains('collapsed');

  applySidebarState(open);
}

export function initSidebar(defaultOpen = true) {
  const savedState = storage.get(STORAGE_KEYS.sidebar);
  const desktopOpen = savedState !== null ? savedState : defaultOpen;

  // Mobile is an ephemeral overlay; opening or closing it must not overwrite
  // the user's desktop open/mini preference.
  applySidebarState(sidebarMedia.matches ? false : desktopOpen, { persist: false });
  document.documentElement.classList.remove('sidebar-init-closed');

  sidebarMedia.addEventListener('change', event => {
    const savedDesktopState = storage.get(STORAGE_KEYS.sidebar);
    const preferredDesktopState = savedDesktopState !== null ? savedDesktopState : defaultOpen;
    applySidebarState(event.matches ? false : preferredDesktopState, { persist: false });
  });
}

// ── Modals ────────────────────────────────────────────────────────────────────

const modalOpeners = new WeakMap();
const focusable = 'button, input, textarea, select, a[href], [tabindex="0"]';
const modalControls = modal => [...modal.querySelectorAll(focusable)]
  .filter(el => el.tabIndex >= 0 && !el.disabled && el.getClientRects().length);

export function openModal(id) {
  const modal = document.getElementById(id);
  modalOpeners.set(modal, document.activeElement);
  modal.inert = false;
  modal.classList.add('open');
  modalControls(modal)[0]?.focus();
}

export function closeModal(id) {
  const modal = document.getElementById(id);
  modal.classList.remove('open');
  modal.inert = true;
  modalOpeners.get(modal)?.focus();
  modalOpeners.delete(modal);
}

// Keep keyboard navigation inside the visible dialog.
document.addEventListener('keydown', event => {
  if (event.key !== 'Tab') return;
  const modal = document.querySelector('.modal-overlay.open');
  if (!modal) return;
  const controls = modalControls(modal);
  const first = controls[0], last = controls.at(-1);
  if (!modal.contains(document.activeElement) || (event.shiftKey ? document.activeElement === first : document.activeElement === last)) {
    event.preventDefault();
    (event.shiftKey ? last : first)?.focus();
  }
});

// Settings and folder tabs share selection and keyboard behavior.
export function initTabs(list) {
  if (!list) return;
  const tabs = [...list.querySelectorAll('[role="tab"], [data-tab]')].filter(tab => {
    const id = tab.getAttribute('aria-controls') || tab.dataset.tab;
    if (!id || !document.getElementById(id)) return false;
    tab.setAttribute('role', 'tab');
    tab.setAttribute('aria-controls', id);
    return true;
  });
  if (!tabs.length) return;
  list.setAttribute('role', 'tablist');
  function select(tab) {
    tabs.forEach(item => {
      const selected = item === tab;
      item.classList.toggle('active', selected);
      item.setAttribute('aria-selected', String(selected));
      item.tabIndex = selected ? 0 : -1;
      const panel = document.getElementById(item.getAttribute('aria-controls'));
      if (!panel) return;
      panel.hidden = !selected;
      panel.classList.toggle('active', selected);
    });
  }
  list.addEventListener('click', event => {
    const tab = event.target.closest('[role="tab"]');
    if (tabs.includes(tab)) select(tab);
  });
  list.addEventListener('keydown', event => {
    const index = tabs.indexOf(event.target);
    if (index < 0) return;
    const next = {ArrowRight: (index + 1) % tabs.length, ArrowLeft: (index + tabs.length - 1) % tabs.length,
      Home: 0, End: tabs.length - 1}[event.key];
    if (next === undefined) return;
    event.preventDefault();
    select(tabs[next]);
    tabs[next].focus();
  });
  select(tabs.find(tab => tab.getAttribute('aria-selected') === 'true' || tab.classList.contains('active')) || tabs[0]);
}

// ── Input helpers ─────────────────────────────────────────────────────────────

export function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
}

export function updateCharCount() {
  const input = document.getElementById('user-input');
  const count = document.getElementById('char-count');
  const len   = input.value.length;
  count.textContent = len > 0 ? `${len} chars` : '';
}
