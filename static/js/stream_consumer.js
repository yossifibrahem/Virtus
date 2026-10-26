// SSE stream reading helpers for chat turns.

export async function readResponseError(resp) {
  const text = await resp.text().catch(() => '');
  if (!text) return resp.statusText || `HTTP ${resp.status}`;

  try {
    const data = JSON.parse(text);
    return data.error || text;
  } catch {
    return text;
  }
}

/** Reads an SSE response body line-by-line and forwards parsed data payloads. */
export async function readSSEStream(resp, onEvent) {
  if (typeof onEvent !== 'function') {
    throw new TypeError('readSSEStream requires an event handler callback.');
  }
  if (!resp.body) throw new Error('Streaming response has no body.');
  const reader  = resp.body.getReader();
  const decoder = new TextDecoder();
  let   buffer  = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split('\n');
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const raw = line.slice(6).trim();
        if (raw === '[DONE]') return true;

        let event;
        try {
          event = JSON.parse(raw);
        } catch { continue; } // Only malformed JSON is safe to skip.
        if (!await onEvent(event)) return false;
      }
    }
    throw new Error('Response stream ended before completion.');
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
