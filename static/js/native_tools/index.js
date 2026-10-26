// Fixed, server-reserved native tools. External MCP adapters cannot override these.
import bash from './bash.js';
import read from './read.js';
import write from './write.js';
import edit from './edit.js';
import { workspacePath } from './shared.js';

const adapters = new Map(Object.entries({ bash, read, write, edit }));
export const nativeAdapterFor = name => adapters.get(name) ?? null;

export function bindNativeActions(element, name, args) {
  const adapter = nativeAdapterFor(name);
  if (!adapter) return;
  const path = workspacePath(args.path);
  if (path) element.querySelector('[data-native-open]')?.addEventListener('click', () => {
    document.dispatchEvent(new CustomEvent('lumen:open-workspace-file', { detail: { path, reveal: true } }));
  });
}
