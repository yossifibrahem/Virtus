export async function copyText(text) {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(text);

  // Compatibility fallback for HTTP on the local network.
  const active = document.activeElement;
  const field = document.createElement('textarea');
  field.value = text;
  field.readOnly = true;
  field.style.cssText = 'position:fixed;top:0;left:0;opacity:0;font-size:16px';
  document.body.appendChild(field);
  try {
    field.select();
    field.setSelectionRange(0, text.length);
    if (!document.execCommand('copy')) throw new Error('Copy failed');
  } finally {
    field.remove();
    active?.focus({ preventScroll: true });
  }
}
