/**
 * Adapter: Exa MCP server tools
 *
 * Covers the active tools exposed by @exa-ai/mcp-server:
 *   • web_search_exa          — semantic web search, returns highlights
 *   • web_fetch_exa           — full-page crawl of one or more URLs
 *
 * Styles are injected into <head> once on first use — nothing goes in main.css.
 */

import { registerAdapter, injectStyles } from './registry.js';
import { escapeHtml } from '../format.js';

// ── Self-contained styles ─────────────────────────────────────────────────────

const EXA_STYLES = `
  .exa-results {
    display: grid;
    gap: 6px;
    max-height: 300px;
    overflow-y: auto;
    padding-right: 2px;
    scrollbar-width: thin;
    scrollbar-color: var(--border2) transparent;
  }
  .exa-results::-webkit-scrollbar { width: 4px; }
  .exa-results::-webkit-scrollbar-thumb { background: var(--border2); border-radius: 999px; }

  .exa-card {
    display: block;
    padding: 8px 9px;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    background: var(--code-bg);
    color: inherit;
    text-decoration: none;
    transition: background var(--fast), border-color var(--fast), color var(--fast);
  }
  .exa-card:hover {
    background: var(--surface2);
    border-color: var(--border2);
  }

  .exa-card-meta {
    display: flex;
    align-items: center;
    gap: 5px;
    min-width: 0;
    margin-bottom: 4px;
    font-family: var(--font-mono);
    font-size: .62rem;
    color: var(--text3);
  }

  .exa-favicon {
    width: 12px;
    height: 12px;
    border-radius: 2px;
    flex-shrink: 0;
    object-fit: contain;
    opacity: .7;
  }

  .exa-card-domain {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--text2);
  }

  .exa-card-date {
    flex-shrink: 0;
    color: var(--text3);
  }

  .exa-card-title {
    color: var(--text);
    font-size: .76rem;
    font-weight: 500;
    line-height: 1.35;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .exa-card-snippet {
    margin: 4px 0 0;
    color: var(--text3);
    font-size: .72rem;
    line-height: 1.5;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .exa-card-fetch .exa-card-snippet {
    -webkit-line-clamp: 5;
    white-space: pre-wrap;
    word-break: break-word;
  }

  .exa-no-results {
    padding: 4px 2px;
    color: var(--text3);
    font-family: var(--font-mono);
    font-size: .72rem;
  }
`;

// ── Helpers ───────────────────────────────────────────────────────────────────

function extractDomain(url) {
  try { return new URL(url).hostname.replace(/^www\./, ''); }
  catch { return url; }
}

function faviconUrl(domain) {
  return `https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=32`;
}

function fmtDate(raw) {
  if (!raw || raw === 'N/A') return '';
  try {
    return new Date(raw).toLocaleDateString('en', { month: 'short', year: 'numeric' });
  } catch { return ''; }
}

/**
 * Parse Exa's text result into structured objects.
 * Handles both search format (Title:/Highlights:) and fetch format (# title / body text).
 * Blocks are separated by "\n\n---\n\n".
 */
function parseExaResults(text) {
  if (!text || typeof text !== 'string') return [];

  return text.split(/\n\n---\n\n/).map(block => {
    const lines = block.split('\n');
    const r = { title: '', url: '', date: '', highlights: [] };
    let mode = null;

    for (const line of lines) {
      if      (line.startsWith('Title: '))          { r.title = line.slice(7).trim(); mode = null; }
      else if (line.startsWith('# '))               { r.title = line.slice(2).trim(); mode = null; }
      else if (line.startsWith('URL: '))            { r.url   = line.slice(5).trim(); mode = null; }
      else if (line.startsWith('Published: '))      { r.date  = fmtDate(line.slice(11).trim()); mode = null; }
      else if (line.startsWith('Author: '))         { /* skip */ mode = null; }
      else if (line === 'Highlights:')              { mode = 'snippet'; }
      else if (line.startsWith('Text: '))           { r.highlights.push(line.slice(6).trim()); mode = 'snippet'; }
      else if (mode === 'snippet' && line.trim())   { r.highlights.push(line.trim()); }
    }

    return r;
  }).filter(r => r.url);
}

// ── Card renderer ─────────────────────────────────────────────────────────────

function buildCard(r, fetchVariant = false) {
  const domain  = extractDomain(r.url);
  const snippet = fetchVariant
    ? r.highlights.join('\n').trim()
    : r.highlights.slice(0, 3).join(' … ').trim();

  return `
    <a class="exa-card${fetchVariant ? ' exa-card-fetch' : ''}"
       href="${escapeHtml(r.url)}" target="_blank" rel="noopener noreferrer">
      <div class="exa-card-meta">
        <img class="exa-favicon"
             src="${escapeHtml(faviconUrl(domain))}"
             alt=""
             loading="lazy"
             onerror="this.style.display='none'" />
        <span class="exa-card-domain">${escapeHtml(domain)}</span>
        ${r.date ? `<span class="exa-card-date">· ${escapeHtml(r.date)}</span>` : ''}
      </div>
      <div class="exa-card-title">${escapeHtml(r.title || r.url)}</div>
      ${snippet ? `<p class="exa-card-snippet">${escapeHtml(snippet)}</p>` : ''}
    </a>`;
}

function renderCards(result, fetchVariant = false) {
  injectStyles('exa', EXA_STYLES);
  const results = parseExaResults(result);
  if (!results.length) {
    return `<div class="exa-no-results">${fetchVariant ? 'No content retrieved.' : 'No results found.'}</div>`;
  }
  return `<div class="exa-results">${results.map(r => buildCard(r, fetchVariant)).join('')}</div>`;
}

// ── Adapter registrations ─────────────────────────────────────────────────────

registerAdapter({
  tools: ['web_search_exa'],

  usingLabel: 'Searching the web',
  labelArg: 'query',

  renderResult(result) {
    return renderCards(result, false);
  },
});

registerAdapter({
  tools: ['web_fetch_exa'],

  usingLabel: 'Fetching pages',

  renderResult(result) {
    return renderCards(result, true);
  },
});
