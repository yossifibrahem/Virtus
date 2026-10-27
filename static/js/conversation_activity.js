// Local streams and server-only activity share one source of truth per turn.
import { api } from './api.js';
import { escapeHtml } from './format.js';

export const activeTurns = new Map();
export let activityVersion = 0;
let serverWorking = new Set();
let pollTimer = null;
const isWorking = id => activeTurns.has(id) || serverWorking.has(id);

export function workingTitleAttributes(ids) {
  const convIds = Array.isArray(ids) ? ids : [ids];
  const working = convIds.some(isWorking);
  return `data-working-conversations="${escapeHtml(convIds.join(' '))}" aria-busy="${working}"`;
}

function refreshIndicators(ids) {
  const selector = [...ids].map(id => `[data-working-conversations~="${CSS.escape(id)}"]`).join(',');
  if (!selector) return;
  document.querySelectorAll(selector).forEach(indicator => {
    const working = String(indicator.dataset.workingConversations.split(' ').some(isWorking));
    if (indicator.getAttribute('aria-busy') !== working) indicator.setAttribute('aria-busy', working);
  });
}

// Called after registering/removing a local consumer. A disconnected consumer
// hands its turn to status polling until the server confirms completion.
export function updateConversationActivity(convId, disconnected = false) {
  ++activityVersion;
  if (disconnected) serverWorking.add(convId);
  else serverWorking.delete(convId);
  refreshIndicators([convId]);
  scheduleActivityRefresh();
}

export function syncConversationActivity(conversations, version = activityVersion) {
  if (!Array.isArray(conversations) || version !== activityVersion) return;
  ++activityVersion;
  const previous = serverWorking;
  serverWorking = new Set();
  for (const conv of conversations) {
    if (conv.active_stream_id && !activeTurns.has(conv.id)) {
      serverWorking.add(conv.id);
      previous.add(conv.id);
    }
  }
  refreshIndicators(previous);
  scheduleActivityRefresh();
}

function scheduleActivityRefresh() {
  if (!serverWorking.size || pollTimer) return;
  pollTimer = setTimeout(async () => {
    try {
      if (serverWorking.size) {
        const version = activityVersion;
        syncConversationActivity(await api.get('/api/conversations/activity'), version);
      }
    } catch {
      // Keep unknown turns visible and retry transient failures.
    } finally {
      pollTimer = null;
      scheduleActivityRefresh();
    }
  }, 2500);
}
