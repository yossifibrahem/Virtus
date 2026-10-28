// Markdown + LaTeX rendering.

import { state } from './state.js';
import { ICONS } from './icons.js';
//
// Strategy: before running marked, stash every LaTeX block in a side
// array (replacing it with a sentinel).  After markdown is rendered,
// restore each block by running it through KaTeX.  DOMPurify sanitises
// the final HTML while allowing the extra tags KaTeX and SVG need.

marked.setOptions({ breaks: true, gfm: true });

// Override the link renderer so external links open in a new tab.
// Workspace file links (/workspace/...) are handled separately by
// enhanceWorkspaceFileLinks and get their own click handler instead.
const _renderer = new marked.Renderer();
_renderer.link = function ({ href, title, text }) {
  const isExternal = href && /^https?:\/\//i.test(href);
  const titleAttr = title ? ` title="${title}"` : '';
  const targetAttr = isExternal ? ' target="_blank" rel="noopener noreferrer"' : '';
  return `<a href="${href}"${titleAttr}${targetAttr}>${text}</a>`;
};
marked.use({ renderer: _renderer });

const PURIFY_CONFIG = {
  ADD_TAGS: [
    'math','semantics','mrow','mi','mo','mn','mfrac','msup','msub',
    'mspace','mtext','annotation','svg','path','use','defs','g',
  ],
  ADD_ATTR: [
    'class','style','xmlns','viewBox','d','fill','stroke',
    'href','xlink:href','width','height','aria-hidden',
  ],
  ALLOWED_URI_REGEXP: /^(?:(?:(?:f|ht)tps?|mailto|tel|file):|[^a-z]|[a-z+.-]+(?:[^a-z+.-:]|$))/i,
};


const FILE_LINK_SKIP_SELECTOR = 'button, code, kbd, pre, script, style, textarea';

function fileNameFromPath(path) {
  return path.split('/').filter(Boolean).pop() || 'file';
}

function decodeFileHref(rawHref) {
  const href = String(rawHref || '').trim();

  // Relative /workspace/... path — the standard format.
  if (href.toLowerCase().startsWith('/workspace/')) {
    try { return decodeURIComponent(href).replace(/\\/g, '/'); } catch {}
    return href;
  }

  // file:/workspace/..., file://workspace/..., or file:///workspace/... URI —
  // models emit all three variants. Strip the scheme and any leading slashes
  // beyond the one that belongs to /workspace/ itself, then decode.
  if (/^file:/i.test(href)) {
    // Remove "file:" then strip 0–2 extra leading slashes so we always get /workspace/...
    const withoutScheme = href.slice('file:'.length).replace(/^\/\//, '');
    if (withoutScheme.toLowerCase().startsWith('/workspace/')) {
      try { return decodeURIComponent(withoutScheme).replace(/\\/g, '/'); } catch {}
      return withoutScheme;
    }
  }

  return '';
}

function normalizeDownloadFilePath(path) {
  return String(path || '').trim().replace(/\\/g, '/');
}

function isSafeWorkspaceFilePath(path) {
  const normalized = normalizeDownloadFilePath(path);
  if (!normalized.startsWith('/workspace/')) return false;
  return !normalized.split('/').some(part => part === '..');
}

function buildConversationFileDownloadUrl(path) {
  const safePath = normalizeDownloadFilePath(path);
  if (!state.convId || !isSafeWorkspaceFilePath(safePath)) return '';
  return `/api/conversations/${encodeURIComponent(state.convId)}/files/download?path=${encodeURIComponent(safePath)}`;
}

function enhanceWorkspaceFileLinks(root) {
  if (!root) return;

  root.querySelectorAll('a[href]').forEach(anchor => {
    if (anchor.closest(FILE_LINK_SKIP_SELECTOR)) return;

    const filePath = normalizeDownloadFilePath(decodeFileHref(anchor.getAttribute('href')));
    if (!filePath) return;

    const downloadHref = buildConversationFileDownloadUrl(filePath);
    if (!downloadHref) {
      anchor.replaceWith(document.createTextNode(anchor.textContent || filePath));
      return;
    }

    const name = fileNameFromPath(filePath);
    anchor.classList.add('assistant-file-link');
    anchor.href = downloadHref;
    anchor.title = `Open ${name} in file panel`;
    anchor.dataset.workspacePath = filePath;
    anchor.addEventListener('click', e => {
      e.preventDefault();
      document.dispatchEvent(new CustomEvent('virtus:open-workspace-file', { detail: { path: filePath } }));
    });
  });
}

function renderMarkdown(text) {
  const latexBlocks = [];
  const stash = (block) => {
    latexBlocks.push(block);
    return `\x02LATEX${latexBlocks.length - 1}\x03`;
  };

  // Protect LaTeX from the Markdown parser.
  const safeText = text
    .replace(/\$\$[\s\S]+?\$\$/g, m => stash({ type: 'block',  src: m }))
    .replace(/\$[^\$\n]+?\$/g,    m => stash({ type: 'inline', src: m }));

  let html = marked.parse(safeText);

  // Restore LaTeX blocks as rendered KaTeX.
  latexBlocks.forEach(({ type, src }, i) => {
    const math = src.replace(/^\$+|\$+$/g, '').trim();
    try {
      html = html.replace(
        `\x02LATEX${i}\x03`,
        katex.renderToString(math, { displayMode: type === 'block', throwOnError: false }),
      );
    } catch {
      html = html.replace(`\x02LATEX${i}\x03`, src);
    }
  });

  return DOMPurify.sanitize(html, PURIFY_CONFIG);
}

function addCodeCopyButton(block) {
  const btn = document.createElement('button');
  btn.className = 'code-copy';
  btn.type = 'button';
  btn.innerHTML = ICONS.copy;
  btn.title = 'Copy code';
  btn.setAttribute('aria-label', 'Copy code');
  btn.onclick = () => {
    navigator.clipboard.writeText(block.innerText);
    btn.innerHTML = ICONS.check;
    btn.title = 'Copied';
    btn.setAttribute('aria-label', 'Copied');
    setTimeout(() => {
      btn.innerHTML = ICONS.copy;
      btn.title = 'Copy code';
      btn.setAttribute('aria-label', 'Copy code');
    }, 1500);
  };
  block.parentElement.appendChild(btn);
}

function openExternalLinksInNewTab(root) {
  if (!root) return;
  root.querySelectorAll('a[href]').forEach(anchor => {
    if (anchor.closest(FILE_LINK_SKIP_SELECTOR)) return;
    const href = anchor.getAttribute('href') || '';
    if (/^https?:\/\//i.test(href)) {
      anchor.setAttribute('target', '_blank');
      anchor.setAttribute('rel', 'noopener noreferrer');
    }
  });
}

export function applyMarkdown(el, text, options = {}) {
  const { copyCodeButtons = true } = options;
  el.innerHTML = renderMarkdown(text);
  enhanceWorkspaceFileLinks(el);
  openExternalLinksInNewTab(el);

  el.querySelectorAll('pre code').forEach(block => {
    hljs.highlightElement(block);
    if (copyCodeButtons) addCodeCopyButton(block);
  });
}
