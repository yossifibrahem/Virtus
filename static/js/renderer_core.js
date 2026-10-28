// Core scroll and message-row helpers shared across renderer sub-modules.

import { $, createElement, remove } from './dom.js';
import { state } from './state.js';

const BOTTOM_THRESHOLD = 32;
export let stickToBottom = true;
let scrollFrame = null;
let forceScroll = false;

export const messagesEl = () => $('#messages');
const mainEl = () => document.getElementById('main');
const composerEl = () => document.getElementById('composer-area');
const isNearBottom = el => el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_THRESHOLD;

export function moveComposerToMain() {
  const main = mainEl();
  const messages = messagesEl();
  const composer = composerEl();
  if (!main || !messages || !composer || composer.parentElement === main) return;
  main.insertBefore(composer, messages.nextSibling);
}

document.addEventListener('DOMContentLoaded', () => {
  const composer = composerEl();
  if (composer) new ResizeObserver(([entry]) => {
    mainEl()?.style.setProperty('--composer-height', `${Math.ceil(entry.target.getBoundingClientRect().height)}px`);
    scrollToBottom();
  }).observe(composer);
  messagesEl()?.addEventListener('scroll', event => {
    stickToBottom = isNearBottom(event.currentTarget);
  }, { passive: true });
});

export function scrollToBottom(force = false) {
  const el = messagesEl();
  if (!el || (!force && !stickToBottom)) return;
  if (!force && state.isStreaming && !state.autoScrollStreaming) return;

  forceScroll ||= force;
  if (scrollFrame !== null) return;
  scrollFrame = requestAnimationFrame(() => {
    scrollFrame = null;
    if (forceScroll || stickToBottom) {
      el.scrollTop = el.scrollHeight;
      stickToBottom = true;
    }
    forceScroll = false;
  });
}

export function createMessageRow({ avatarClass, avatarIcon, roleLabel, isUser = false }) {
  moveComposerToMain();
  remove('#empty-state');
  remove('#folder-home-chats');
  mainEl()?.classList.remove('is-empty', 'folder-home');
  const input = document.getElementById('user-input');
  if (input) input.placeholder = 'Ask anything…';

  const row = createElement('div', { className: `msg-row${isUser ? ' user-row' : ''}` });
  // Keep speaker identity accessible without repeating decorative avatars.
  row.setAttribute('aria-label', isUser ? 'Your message' : 'Assistant message');
  row.innerHTML = `<div class="msg-meta"><span class="msg-role-label">${roleLabel}</span></div>`;

  messagesEl().appendChild(row);
  return row;
}
