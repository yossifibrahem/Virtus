// One bounded, escaped source view for workspace previews and native Read results.
import { escapeHtml, fileExtension } from './format.js';

const LANGUAGES = {
  js: 'javascript', mjs: 'javascript', jsx: 'javascript', ts: 'typescript', tsx: 'typescript',
  py: 'python', rb: 'ruby', rs: 'rust', go: 'go', java: 'java', c: 'c', h: 'c', cpp: 'cpp',
  hpp: 'cpp', cs: 'csharp', php: 'php', sh: 'bash', bash: 'bash', css: 'css', html: 'xml',
  htm: 'xml', json: 'json', yaml: 'yaml', yml: 'yaml', xml: 'xml', sql: 'sql', md: 'markdown', toml: 'toml',
};
const MAX_CHARS = 128 * 1024;
const MAX_LINES = 2000;

export function renderSource(content = '', { numbered = false, filename = '' } = {}) {
  const text = String(content ?? '');
  const bounded = text.slice(0, MAX_CHARS).replace(/\r\n?/g, '\n');
  const lines = (bounded.endsWith('\n') ? bounded.slice(0, -1) : bounded).split('\n', MAX_LINES + 1);
  const shortened = text.length > MAX_CHARS || lines.length > MAX_LINES;
  const language = LANGUAGES[fileExtension(filename)];
  const highlighter = globalThis.hljs;
  // Avoid thousands of syntax-parser calls for large files; plain source stays readable.
  const highlight = language && bounded.length <= 32000 && lines.length <= 400 && highlighter?.getLanguage(language);
  let digits = 2;
  const rows = lines.slice(0, MAX_LINES).map((line, index) => {
    const match = numbered ? line.match(/^(\d+): (.*)$/) : null;
    if (numbered && !match) return `<div class="source-note">${escapeHtml(line)}</div>`;
    const number = match ? match[1] : String(index + 1);
    const code = match ? match[2] : line;
    digits = Math.max(digits, number.length);
    const html = highlight ? highlighter.highlight(code, { language, ignoreIllegals: true }).value : escapeHtml(code);
    return `<div class="source-line"><span class="source-line-number" aria-hidden="true">${number}</span><code>${html || '\u200b'}</code></div>`;
  }).join('');
  return `<div class="source-view" tabindex="0" role="region" aria-label="File contents" style="--line-number-ch:${digits}ch">${rows}${shortened ? '<div class="source-note" role="status">Preview shortened. Open or download the file for the full contents.</div>' : ''}</div>`;
}
