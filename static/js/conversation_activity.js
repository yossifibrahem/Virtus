// Local streams and server-only activity share one source of truth per turn.
import { api } from './api.js';
import { escapeHtml } from './format.js';

export const activeTurns = new Map();
export let activityVersion = 0;
let serverWorking = new Set();
let pollTimer = null;
const isWorking = id => activeTurns.has(id) || serverWorking.has(id);

export function workingIndicator(ids) {
  const convIds = Array.isArray(ids) ? ids : [ids];
  return `<span class="conv-working" data-working-conversations="${escapeHtml(convIds.join(' '))}" role="img" aria-label="Assistant working" title="Assistant working"${convIds.some(isWorking) ? '' : ' hidden'}></span>`;
}

function refreshIndicators(ids) {
  const selector = [...ids].map(id => `[data-working-conversations~="${CSS.escape(id)}"]`).join(',');
  if (!selector) return;
  document.querySelectorAll(selector).forEach(indicator => {
    indicator.hidden = !indicator.dataset.workingConversations.split(' ').some(isWorking);
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
  serverWorking = new Set(conversations
    .filter(conv => conv.active_stream_id && !activeTurns.has(conv.id))
    .map(conv => conv.id));
  refreshIndicators(new Set([...previous, ...serverWorking]));
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
