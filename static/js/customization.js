// Customization — UI preferences persisted to localStorage.

import { state, CUSTOMIZATION_DEFAULTS, STORAGE_KEYS } from './state.js';
import { storage } from './storage.js';

// ── Apply ─────────────────────────────────────────────────────────────────────
// Reads from `state` and pushes every setting into the live DOM.

let autoThemeListenerAttached = false;
let currentAccentHex = null;

function normalizeFontFamily(family) {
  if (family === 'space') return 'geist';
  return ['geist', 'system'].includes(family) ? family : 'geist';
}

export function applyCustomization() {
  // Theme (light/dark/auto)
  _applyTheme(state.theme || CUSTOMIZATION_DEFAULTS.theme);

  // Font size
  const isMobile = window.innerWidth <= 768;
  const sizes = isMobile
    ? { small: '12px', medium: '14px', large: '16px' }
    : { small: '14px', medium: '16px', large: '18px' };
  document.documentElement.style.setProperty('--font-size-base', sizes[state.fontSize] || sizes.medium);

  // Font family
  _applyFontFamily(state.fontFamily || CUSTOMIZATION_DEFAULTS.fontFamily);

  // Accent color — custom hex overrides swatch if set
  const accent = (state.customAccentColor && /^#[0-9a-f]{6}$/i.test(state.customAccentColor))
    ? state.customAccentColor
    : (state.accentColor || CUSTOMIZATION_DEFAULTS.accentColor);
  _applyAccent(accent);

  // Suggestion chips visibility
  document.documentElement.classList.toggle('hide-suggestion-chips', !state.showSuggestionChips);


}

function _applyTheme(theme) {
  let effective = theme;
  if (theme === 'auto') {
    effective = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  }
  document.documentElement.setAttribute('data-theme', effective);
  if (currentAccentHex) _applyAccent(currentAccentHex);
}

function _applyFontFamily(family) {
  const map = {
    geist:   "'Geist', sans-serif",
    system:  "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
  };
  document.documentElement.style.setProperty('--font-roman', map[normalizeFontFamily(family)]);
}

function _applyAccent(hex) {
  const sourceRgb = hexToRgb(hex);
  if (!sourceRgb) return;

  const root = document.documentElement;
  const rgb = root.getAttribute('data-theme') === 'light'
    ? darkenRgb(sourceRgb, 0.14)
    : sourceRgb;
  const accentText = readableTextForRgb(rgb);
  const accentHex = rgbToHex(rgb);

  currentAccentHex = hex;
  root.style.setProperty('--accent',            accentHex);
  root.style.setProperty('--accent-rgb',        rgb);
  root.style.setProperty('--on-accent',         accentText);
  root.style.setProperty('--accent-dim',        `rgba(${rgb},0.12)`);
  root.style.setProperty('--accent-hover',      `rgba(${rgb},0.20)`);
  root.style.setProperty('--accent-glow',       `rgba(${rgb},0.06)`);
  root.style.setProperty('--accent-border',     `rgba(${rgb},0.4)`);
  root.style.setProperty('--accent-border-dim', `rgba(${rgb},0.25)`);
  root.style.setProperty('--accent-border-mid', `rgba(${rgb},0.50)`);
}

function darkenRgb(rgb, amount) {
  return rgb.split(',').map(channel => Math.round(Number(channel) * (1 - amount))).join(',');
}

function rgbToHex(rgb) {
  return `#${rgb.split(',').map(channel => Number(channel).toString(16).padStart(2, '0')).join('')}`;
}

// ── Load ──────────────────────────────────────────────────────────────────────

export function loadCustomization() {
  const saved = storage.get(STORAGE_KEYS.customization, {});
  // Retain the old toggle value while migrating to the work-log collapse preference.
  const { groupSequentialBlocks: legacyGrouping, ...preferences } = saved;
  Object.assign(state, {
    ...CUSTOMIZATION_DEFAULTS,
    ...preferences,
    collapseSequentialBlocks: preferences.collapseSequentialBlocks ?? legacyGrouping ?? CUSTOMIZATION_DEFAULTS.collapseSequentialBlocks,
  });
  state.fontFamily = normalizeFontFamily(state.fontFamily);
  applyCustomization();
  syncCustomizationUI();

  // If auto theme, re-apply when OS preference changes. Attach once only;
  // loadCustomization can be called again after reset/import-style flows.
  if (!autoThemeListenerAttached) {
    window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', () => {
      if (state.theme === 'auto') _applyTheme('auto');
    });
    autoThemeListenerAttached = true;
  }
}

// ── Save ──────────────────────────────────────────────────────────────────────

export function saveCustomization() {
  const wasCollapsed = state.collapseSequentialBlocks;
  _readControlsIntoState();
  if (wasCollapsed !== state.collapseSequentialBlocks) {
    document.querySelectorAll('.block-group').forEach(group => {
      const expanded = !state.collapseSequentialBlocks;
      group.classList.toggle('open', expanded);
      group.querySelector('.group-header')?.setAttribute('aria-expanded', String(expanded));
      const workspace = group.querySelector('.activity-workspace');
      if (workspace) workspace.hidden = !expanded;
    });
  }
  storage.set(STORAGE_KEYS.customization, {
    sidebarDefaultOpen:    state.sidebarDefaultOpen,
    showSuggestionChips:   state.showSuggestionChips,
    hideToolBlocks:        state.hideToolBlocks,
    collapseSequentialBlocks: state.collapseSequentialBlocks,
    hideThinkingTokens:    state.hideThinkingTokens,
    fontSize:              state.fontSize,
    fontFamily:            state.fontFamily,
    theme:                 state.theme,
    accentColor:           state.accentColor,
    customAccentColor:     state.customAccentColor,
  });
  applyCustomization();
}

// ── Init listeners ────────────────────────────────────────────────────────────

export function initSwatchPicker() {
  // Colour swatches — only update active highlight
  document.querySelectorAll('.cust-swatch').forEach(sw => {
    sw.addEventListener('click', () => {
      document.querySelectorAll('.cust-swatch').forEach(s => s.classList.remove('active'));
      sw.classList.add('active');
      // Clear custom color when a preset swatch is selected
      const hexInput = document.getElementById('cust-accent-hex');
      const picker   = document.getElementById('cust-accent-picker');
      if (hexInput) hexInput.value = '';
      if (picker)   picker.value  = sw.dataset.color;
    });
  });

  // Custom color picker
  const picker   = document.getElementById('cust-accent-picker');
  const hexInput = document.getElementById('cust-accent-hex');

  if (picker && hexInput) {
    picker.addEventListener('input', () => {
      hexInput.value = picker.value;
      _deactivateSwatches();
    });
    hexInput.addEventListener('input', () => {
      const val = hexInput.value.trim();
      if (/^#[0-9a-f]{6}$/i.test(val)) {
        picker.value = val;
        _deactivateSwatches();
      }
    });
  }

  // Theme radio cards
  document.querySelectorAll('input[name="cust-theme"]').forEach(radio => {
    radio.addEventListener('change', () => {
      // Selection is previewed by the card itself; the app theme changes on Apply.
    });
  });
}

function _deactivateSwatches() {
  document.querySelectorAll('.cust-swatch').forEach(s => s.classList.remove('active'));
}

// ── Sync UI controls → current state ─────────────────────────────────────────

export function syncCustomizationUI() {
  _setCheckbox('cust-sidebar-open',    state.sidebarDefaultOpen);
  _setCheckbox('cust-suggestion-chips',state.showSuggestionChips);
  _setCheckbox('cust-hide-tool-blocks',   state.hideToolBlocks);
  _setCheckbox('cust-hide-thinking',     state.hideThinkingTokens);
  _setCheckbox('cust-collapse-seq-blocks',  state.collapseSequentialBlocks);

  const fs = document.getElementById('cust-font-size');
  if (fs) fs.value = state.fontSize;

  const ff = document.getElementById('cust-font-family');
  if (ff) ff.value = normalizeFontFamily(state.fontFamily);

  // Theme radio
  const themeRadio = document.querySelector(`input[name="cust-theme"][value="${state.theme || CUSTOMIZATION_DEFAULTS.theme}"]`);
  if (themeRadio) themeRadio.checked = true;

  // Swatches
  document.querySelectorAll('.cust-swatch').forEach(sw => {
    sw.classList.toggle('active', sw.dataset.color === state.accentColor && !state.customAccentColor);
  });

  // Custom color fields
  const hexInput = document.getElementById('cust-accent-hex');
  const picker   = document.getElementById('cust-accent-picker');
  if (hexInput) hexInput.value = state.customAccentColor || '';
  if (picker) {
    picker.value = state.customAccentColor || state.accentColor || CUSTOMIZATION_DEFAULTS.accentColor;
  }
}

// ── Read DOM controls → state ─────────────────────────────────────────────────

function _readControlsIntoState() {
  state.sidebarDefaultOpen    = document.getElementById('cust-sidebar-open')?.checked     ?? state.sidebarDefaultOpen;
  state.showSuggestionChips   = document.getElementById('cust-suggestion-chips')?.checked  ?? state.showSuggestionChips;
  state.hideToolBlocks        = document.getElementById('cust-hide-tool-blocks')?.checked   ?? state.hideToolBlocks;
  state.hideThinkingTokens    = document.getElementById('cust-hide-thinking')?.checked     ?? state.hideThinkingTokens;
  state.collapseSequentialBlocks = document.getElementById('cust-collapse-seq-blocks')?.checked  ?? state.collapseSequentialBlocks;
  state.fontSize              = document.getElementById('cust-font-size')?.value           ?? state.fontSize;
  state.fontFamily            = normalizeFontFamily(document.getElementById('cust-font-family')?.value ?? state.fontFamily);
  const themeRadio = document.querySelector('input[name="cust-theme"]:checked');
  if (themeRadio) state.theme = themeRadio.value;

  // Accent: custom hex takes priority over swatch
  const hexInput = document.getElementById('cust-accent-hex');
  const customHex = hexInput?.value.trim() || '';
  if (customHex && /^#[0-9a-f]{6}$/i.test(customHex)) {
    state.customAccentColor = customHex;
  } else {
    state.customAccentColor = '';
    const activeSwatch = document.querySelector('.cust-swatch.active');
    if (activeSwatch) state.accentColor = activeSwatch.dataset.color;
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function _setCheckbox(id, value) {
  const el = document.getElementById(id);
  if (el) el.checked = !!value;
}

function hexToRgb(hex) {
  const m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
  return m ? `${parseInt(m[1],16)},${parseInt(m[2],16)},${parseInt(m[3],16)}` : null;
}

function readableTextForRgb(rgbString) {
  const [r, g, b] = rgbString.split(',').map(Number).map(v => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  const luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return luminance > 0.20 ? '#201711' : '#fff7ea';
}
