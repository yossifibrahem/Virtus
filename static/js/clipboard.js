// Call from a user gesture. LAN HTTP does not expose navigator.clipboard.
export const COPY_FAILURE_MESSAGE = 'Could not copy. Select and copy the text manually, or use HTTPS.';

export async function copyText(text) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      // Permissions or browser policy may reject the modern API too.
    }
  }

  const active = document.activeElement;
  const selection = window.getSelection();
  const ranges = [];
  for (let i = 0; selection && i < selection.rangeCount; i += 1) {
    ranges.push(selection.getRangeAt(i).cloneRange());
  }
  const field = document.createElement('textarea');
  field.value = text;
  field.readOnly = true;
  // Keep it rendered and selectable, without moving the page or opening a keyboard.
  field.style.cssText = 'position:fixed;top:0;left:0;opacity:0;font-size:16px;pointer-events:none';
  document.body.appendChild(field);
  try {
    field.focus({ preventScroll: true });
    field.select();
    field.setSelectionRange(0, field.value.length);
    // Deprecated, but still useful for plain-text copying over LAN HTTP.
    if (!document.execCommand('copy')) throw new Error(COPY_FAILURE_MESSAGE);
  } finally {
    field.remove();
    active?.focus({ preventScroll: true });
    if (selection) {
      selection.removeAllRanges();
      ranges.forEach(range => selection.addRange(range));
    }
  }
}
