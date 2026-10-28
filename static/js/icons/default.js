// Lumen's icon catalog: soft, rounded, and readable at small sizes.

import { icon, smallIcon } from './svg.js';

export const DEFAULT_ICONS = Object.freeze({
  // ── Avatars ────────────────────────────────────────────────────────────────
  user: icon('<circle cx="12" cy="7.5" r="3.5"/><path d="M5.25 21a6.75 6.75 0 0 1 13.5 0"/><path d="M9.8 7.35h.01M14.2 7.35h.01"/>'),
  ai: icon('<path d="M8 5h8a4 4 0 0 1 4 4v6a4 4 0 0 1-4 4h-1.8L12 21.25 9.8 19H8a4 4 0 0 1-4-4V9a4 4 0 0 1 4-4z"/><path d="M12 5V2.75"/><path d="M9 2.75h6"/><path d="M8.75 11h.01M15.25 11h.01"/><path d="M9.5 14.5h5"/>'),

  // ── Actions ────────────────────────────────────────────────────────────────
  panelLeft: icon('<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M9 4v16"/>'),
  check: icon('<path d="M20 6.5 9.4 17.1 4 11.7"/>', 'stroke-width="2.7"'),
  close: icon('<path d="M6.5 6.5 17.5 17.5M17.5 6.5 6.5 17.5"/>', 'stroke-width="2.5"'),
  moreVertical: icon('<circle cx="12" cy="5" r="1.65" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.65" fill="currentColor" stroke="none"/><circle cx="12" cy="19" r="1.65" fill="currentColor" stroke="none"/>'),
  trash: icon('<path d="M4 6.5h16"/><path d="M9 6.5V4.75A1.75 1.75 0 0 1 10.75 3h2.5A1.75 1.75 0 0 1 15 4.75V6.5"/><path d="M18.25 6.5 17.4 19.2A2 2 0 0 1 15.4 21H8.6a2 2 0 0 1-2-1.8L5.75 6.5"/><path d="M10 11v5.5M14 11v5.5"/>'),
  copy: icon('<rect x="8" y="8" width="12" height="12" rx="3"/><path d="M16 8V6.5A2.5 2.5 0 0 0 13.5 4h-7A2.5 2.5 0 0 0 4 6.5v7A2.5 2.5 0 0 0 6.5 16H8"/>'),
  download: icon('<path d="M5 20.5h14a2 2 0 0 0 2-2V16"/><path d="M3 16v2.5a2 2 0 0 0 2 2"/><path d="M12 3v11"/><path d="m7.5 10.5 4.5 4.5 4.5-4.5"/>'),
  edit: icon('<path d="M4.5 17.25V20h2.75L18.8 8.45a1.95 1.95 0 0 0 0-2.75l-.5-.5a1.95 1.95 0 0 0-2.75 0L4.5 17.25z"/><path d="M14.5 6.25 17.75 9.5"/><path d="M12 20h8"/>'),
  refresh: icon('<path d="M20.5 12a8.5 8.5 0 0 1-14.7 5.85"/><path d="M3.5 12A8.5 8.5 0 0 1 18.2 6.15"/><path d="M18.5 3.5v4h-4"/><path d="M5.5 20.5v-4h4"/>'),

  // ── Navigation / chrome ────────────────────────────────────────────────────
  chevronLeft: icon('<path d="m15 18-6-6 6-6"/>', 'width="10" height="10" stroke-width="2.6"'),
  chevronRight: icon('<path d="m9 6 6 6-6 6"/>', 'width="10" height="10" stroke-width="2.6"'),
  chevronDown: icon('<path d="m6 9 6 6 6-6"/>', 'width="10" height="10" stroke-width="2.6"'),
  menu: icon('<path d="M4.5 7h15M4.5 12h15M4.5 17h15"/>', 'stroke-width="2.6"'),
  settings: icon('<path d="M4 7h9"/><path d="M17 7h3"/><circle cx="15" cy="7" r="2"/><path d="M4 17h3"/><path d="M11 17h9"/><circle cx="9" cy="17" r="2"/>'),
  plus: icon('<path d="M12 5v14M5 12h14"/>', 'stroke-width="2.7"'),
  chatPlus: icon('<path d="M6.5 4h11A3.5 3.5 0 0 1 21 7.5v6A3.5 3.5 0 0 1 17.5 17H12l-5 4v-4h-.5A3.5 3.5 0 0 1 3 13.5v-6A3.5 3.5 0 0 1 6.5 4z"/><path d="M12 7.5v6M9 10.5h6"/>'),
  folder: icon('<path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/>'),
  folderOpen: icon('<path d="M3 17.5V7a2 2 0 0 1 2-2h5l2 2h6.5a2 2 0 0 1 2 2v1"/><path d="M4.5 10h16.25a1.25 1.25 0 0 1 1.2 1.6l-2 6.75A2.25 2.25 0 0 1 17.8 20H5.2a2.25 2.25 0 0 1-2.15-2.9l1.45-5.35A2.5 2.5 0 0 1 6.9 10z"/>'),
  folderPlus: icon('<path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/><path d="M12 11v5M9.5 13.5h5"/>'),

  // ── Brand / logo ───────────────────────────────────────────────────────────
  logo: icon('<path d="M7.5 4h9A3.5 3.5 0 0 1 20 7.5v9a3.5 3.5 0 0 1-3.5 3.5h-9A3.5 3.5 0 0 1 4 16.5v-9A3.5 3.5 0 0 1 7.5 4z"/><path d="M12 4V2.5"/><path d="M9 2.5h6"/><path d="M8.7 10h.01M15.3 10h.01"/><path d="M9 14.75c1.9 1.25 4.1 1.25 6 0"/>'),

  // ── Input toolbar ──────────────────────────────────────────────────────────
  send: icon('<path d="M4 5.25 21 12 4 18.75 7.25 12 4 5.25z"/><path d="M7.25 12H14"/>', 'stroke-width="2.2"'),
  stop: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6.5" y="6.5" width="11" height="11" rx="2.5"/></svg>',
  mic: icon('<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><path d="M12 18v3"/><path d="M8.5 21h7"/>'),
  file: icon('<path d="M7 3h7l5 5v11a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path d="M14 3v5h5"/><path d="M8.5 13h7M8.5 17h5"/>'),

  // ── Conversation list ──────────────────────────────────────────────────────
  chat: icon('<path d="M6.5 4h11A3.5 3.5 0 0 1 21 7.5v6A3.5 3.5 0 0 1 17.5 17H12l-5 4v-4h-.5A3.5 3.5 0 0 1 3 13.5v-6A3.5 3.5 0 0 1 6.5 4z"/><path d="M8 9h8M8 12.5h5"/>'),
  search: icon('<circle cx="10.75" cy="10.75" r="6.25"/><path d="m15.5 15.5 4.75 4.75"/>', 'stroke-width="2.3"'),

  // ── Decorative / small ─────────────────────────────────────────────────────
  bulb: icon('<path d="M9 18h6"/><path d="M10 21h4"/><path d="M12 3a6.5 6.5 0 0 1 4 11.65L15 16H9l-1-1.35A6.5 6.5 0 0 1 12 3z"/><path d="M10.5 9.5 12 11l1.5-1.5"/>', 'width="14" height="14"'),
  layers: icon('<path d="M12 3 21 8l-9 5-9-5 9-5z"/><path d="m3 12 9 5 9-5"/><path d="m3 16 9 5 9-5"/>', 'width="14" height="14"'),

  // ── Suggestion chip icons ──────────────────────────────────────────────────
  chipCode: icon('<path d="m8 7-5 5 5 5"/><path d="m16 7 5 5-5 5"/><path d="m14 4-4 16"/>'),
  chipPencil: icon('<path d="M5 17.5V20h2.5L18.7 8.8a1.8 1.8 0 0 0 0-2.55l-.95-.95a1.8 1.8 0 0 0-2.55 0L5 17.5z"/><path d="M14.5 6 18 9.5"/>'),
  chipInfo: icon('<path d="M7 4h10a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z"/><path d="M12 10.5V16"/><path d="M12 7.5h.01"/>'),
  chipBox: icon('<path d="M5 7.5 12 4l7 3.5v9L12 20l-7-3.5v-9z"/><path d="M5 7.5 12 11l7-3.5"/><path d="M12 11v9"/>'),
  chipHelp: icon('<path d="M12 20a8 8 0 1 0 0-16 8 8 0 0 0 0 16z"/><path d="M9.7 9.3a2.5 2.5 0 0 1 4.85.85c0 1.8-2.2 2.05-2.2 3.55"/><path d="M12.35 16.5h.01"/>'),

  // ── Tab icons (modal navigation) ──────────────────────────────────────────
  tabApi: smallIcon('<rect x="2" y="5" width="12" height="8" rx="2"/><path d="M5.5 5V4a2.5 2.5 0 0 1 5 0v1"/><path d="M6 9h4"/>'),
  tabChat: smallIcon('<path d="M3.5 3h9A1.5 1.5 0 0 1 14 4.5v5A1.5 1.5 0 0 1 12.5 11H8l-3.5 3v-3h-1A1.5 1.5 0 0 1 2 9.5v-5A1.5 1.5 0 0 1 3.5 3z"/>'),
  tabAppearance: smallIcon('<circle cx="8" cy="8" r="2.25"/><path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M3.4 12.6l1.4-1.4M11.2 4.8l1.4-1.4"/>'),
  tabMcp: smallIcon('<rect x="2" y="2" width="4.5" height="4.5" rx="1"/><rect x="9.5" y="2" width="4.5" height="4.5" rx="1"/><rect x="2" y="9.5" width="4.5" height="4.5" rx="1"/><path d="M6.5 4.25h3M4.25 6.5v3M9.5 4.25v7a2.25 2.25 0 0 1-2.25 2.25H6.5"/>'),
  toolDefault: smallIcon('<path d="M13.5 2.5 3 6.5l4.25 2.25L9.5 13 13.5 2.5z"/>', 'width="14" height="14"'),
  tabContainers: smallIcon('<path d="M8 1.5 14 4.75v6.5L8 14.5l-6-3.25v-6.5L8 1.5z"/><path d="M2 4.75 8 8l6-3.25"/><path d="M8 8v6.5"/>'),

  // ── API key visibility toggle ──────────────────────────────────────────────
  eyeShow: smallIcon('<path d="M1.5 8s2.7-4.75 6.5-4.75S14.5 8 14.5 8 11.8 12.75 8 12.75 1.5 8 1.5 8z"/><circle cx="8" cy="8" r="2"/>', 'width="14" height="14"'),
  eyeHide: smallIcon('<path d="M1.5 8s2.7-4.75 6.5-4.75S14.5 8 14.5 8 11.8 12.75 8 12.75 1.5 8 1.5 8z"/><circle cx="8" cy="8" r="2"/><path d="M2.5 2.5 13.5 13.5"/>', 'width="14" height="14"'),

  // ── Sync / fetch ───────────────────────────────────────────────────────────
  syncIcon: smallIcon('<path d="M13.5 8A5.5 5.5 0 0 1 4 11.85"/><path d="M2.5 8A5.5 5.5 0 0 1 12 4.15"/><path d="M12.5 2.5v3h-3"/><path d="M3.5 13.5v-3h3"/>'),

  // ── MCP server icons ───────────────────────────────────────────────────────
  mcpWebSearch: icon('<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3a14 14 0 0 1 3.5 9A14 14 0 0 1 12 21a14 14 0 0 1-3.5-9A14 14 0 0 1 12 3z"/>', 'width="14" height="14"'),
  mcpBash: icon('<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="m7.5 9 3 3-3 3"/><path d="M13 15h4"/>', 'width="14" height="14"'),
  mcpFileSystem: icon('<path d="M3 6.5A2.5 2.5 0 0 1 5.5 4h4l2 2.5h7A2.5 2.5 0 0 1 21 9v8.5A2.5 2.5 0 0 1 18.5 20h-13A2.5 2.5 0 0 1 3 17.5v-11z"/>', 'width="14" height="14"'),
  mcpMemory: icon('<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9.5" y="9.5" width="5" height="5" rx=".75"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>', 'width="14" height="14"'),
  mcpDatabase: icon('<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v12c0 1.65 3.6 3 8 3s8-1.35 8-3V6"/><path d="M20 12c0 1.65-3.6 3-8 3s-8-1.35-8-3"/>', 'width="14" height="14"'),
  mcpGit: icon('<path d="M7 5v8.5a3.5 3.5 0 0 0 3.5 3.5H14"/><circle cx="7" cy="5" r="2.5"/><circle cx="7" cy="18" r="2.5"/><circle cx="17" cy="17" r="2.5"/><path d="M7 13.5V18"/>', 'width="14" height="14"'),
  mcpFile: icon('<path d="M7 3h7l5 5v11a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path d="M14 3v5h5"/>', 'width="14" height="14"'),
  mcpPencil: icon('<path d="M4.5 17.5V20h2.5L18.7 8.3a1.8 1.8 0 0 0 0-2.55l-.45-.45a1.8 1.8 0 0 0-2.55 0L4.5 17.5z"/><path d="M14.75 6.25 17.75 9.25"/>', 'width="14" height="14"'),
  mcpLink: icon('<path d="M10.25 13.75a4 4 0 0 0 5.65 0l2.35-2.35a4 4 0 0 0-5.65-5.65L11.25 7.1"/><path d="M13.75 10.25a4 4 0 0 0-5.65 0L5.75 12.6a4 4 0 0 0 5.65 5.65l1.35-1.35"/>', 'width="14" height="14"'),
  mcpEye: icon('<path d="M2 12s3.8-7 10-7 10 7 10 7-3.8 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>', 'width="14" height="14"'),
});
