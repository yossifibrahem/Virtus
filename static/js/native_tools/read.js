import { textOf, failed, preview, escapeHtml, block, fileHeader, pathLabel, icon, status } from './shared.js';

const MEDIA_PATH = /^\/api\/conversations\/[a-f0-9-]{36}\/tool-media\/[a-f0-9]{64}\.jpeg$/;
export default {
  name: 'Read', usingLabel: 'Reading file',
  icon: icon('<path d="M14 2H6a2 2 0 0 0-2 2v16h16V8zM14 2v6h6M8 12h8M8 16h6"/>'),
  label: args => pathLabel('Read', args),
  status: result => status(result, 'Read'),
  renderResult(result) {
    const text = textOf(result);
    const lines = text.split('\n');
    const numbered = !failed(result) && lines.some(line => /^\d+: /.test(line));
    const content = numbered
      ? `<div class="native-source" tabindex="0" aria-label="File contents">${lines.slice(0, 250).map(line => {
          const match = line.match(/^(\d+): (.*)$/);
          return match ? `<div class="native-source-line"><span class="native-line-number" aria-hidden="true">${match[1]}</span><code>${preview(match[2])}</code></div>` : `<div class="native-read-note">${preview(line)}</div>`;
        }).join('')}</div>`
      : block(failed(result) ? 'Could not read' : 'Contents', text || '(Empty)', failed(result) ? 'native-error' : '');
    const media = Array.isArray(result?.media) ? result.media.filter(url => typeof url === 'string' && MEDIA_PATH.test(url)).slice(0, 5) : [];
    return content + `<div class="native-media">${media.map((url, i) => `<figure><a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" aria-label="Open visual ${i + 1}"><img src="${escapeHtml(url)}" alt="Read tool visual ${i + 1}" loading="lazy"></a><figcaption>Visual ${i + 1} · click to enlarge</figcaption></figure>`).join('')}</div>`;
  },
  renderBody(args, result) {
    return `${fileHeader(args)}<div class="native-toolbar"><span>${args.range ? `Range ${escapeHtml(String(args.range))}` : 'Default range'}</span></div>${this.renderResult(result)}`;
  },
};
