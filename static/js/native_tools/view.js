import { renderSource } from '../source_view.js';
import { textOf, failed, escapeHtml, block, fileHeader, pathLabel, icon, status } from './shared.js';

const MEDIA_PATH = /^\/api\/conversations\/[a-f0-9-]{36}\/tool-media\/[a-f0-9]{64}\.jpeg$/;
export default {
  name: 'View', usingLabel: 'Viewing file',
  icon: icon('<path d="M14 2H6a2 2 0 0 0-2 2v16h16V8zM14 2v6h6M8 12h8M8 16h6"/>'),
  label: args => pathLabel('View', args),
  status: result => status(result, 'View'),
  renderResult(result, path = '') {
    const text = textOf(result);
    const numbered = !failed(result) && /^\d+: /m.test(text);
    const content = numbered
      ? renderSource(text, { numbered: true, filename: path })
      : block(failed(result) ? 'Could not view' : 'Contents', text || '(Empty)', failed(result) ? 'native-error' : '');
    const media = Array.isArray(result?.media) ? result.media.filter(url => typeof url === 'string' && MEDIA_PATH.test(url)).slice(0, 5) : [];
    return content + `<div class="native-media">${media.map((url, i) => `<figure><a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" aria-label="Open visual ${i + 1}"><img src="${escapeHtml(url)}" alt="View tool visual ${i + 1}" loading="lazy"></a><figcaption>Visual ${i + 1} · click to enlarge</figcaption></figure>`).join('')}</div>`;
  },
  renderBody(args, result) {
    return `${fileHeader(args)}${args.range ? `<div class="native-toolbar">Range ${escapeHtml(String(args.range))}</div>` : ''}${this.renderResult(result, args.path)}`;
  },
};
