import { ICONS } from './icons.js';
import { api } from './api.js';
import { state } from './state.js';
import { createNewConversation } from './conversations.js';
import { refreshFilePanel } from './file_panel.js';

export function initTerminalPanel() {
  const panel = document.getElementById('terminal-panel');
  const main = document.getElementById('main');
  const toggle = document.getElementById('btn-toggle-terminal');
  const status = document.getElementById('terminal-status');
  const tabBar = document.getElementById('terminal-tabs');
  const addButton = document.getElementById('btn-add-terminal');
  const screen = document.getElementById('terminal-screen');
  const tabs = [];
  let active = null, serial = 0, height = 300, resizeTimer, creatingWorkspace;
  const workspace = () => state.convId ? `/api/conversations/${encodeURIComponent(state.convId)}`
    : state.folderId ? `/api/folders/${encodeURIComponent(state.folderId)}` : null;

  function terminalTheme() {
    const css = window.getComputedStyle?.(document.documentElement);
    const value = (name, fallback) => css?.getPropertyValue(name).trim() || fallback;
    return { background: value('--bg', '#20201e'), foreground: value('--text', '#e4e4e7'),
      cursor: value('--accent', '#DDB37E'), selectionBackground: value('--interaction-bg', '#444444') };
  }
  if (typeof MutationObserver !== 'undefined') {
    new MutationObserver(() => {
      for (const target of tabs) target.term.options.theme = terminalTheme();
    }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'style'] });
  }

  const savedKey = 'virtus_terminal_sessions';
  let restored = false, leaving = false;
  function saveTabs() {
    try {
      sessionStorage.setItem(savedKey, JSON.stringify({ base: workspace(), open: !panel.hidden,
        active: active?.number, tabs: tabs.filter(t => t.url && !t.closed && !t.ended).map(t =>
          ({ number: t.number, url: t.url, output: t.output || '' })) }));
    } catch { /* Storage may be unavailable or full. Shells remain usable. */ }
  }
  function restoreTabs() {
    if (restored) return;
    restored = true;
    try {
      const saved = JSON.parse(sessionStorage.getItem(savedKey) || 'null');
      if (!saved || saved.base !== workspace() || !Array.isArray(saved.tabs)) return;
      for (const entry of saved.tabs.slice(0, 8)) {
        if (Number.isInteger(entry.number) && entry.number > 0 && typeof entry.url === 'string'
            && entry.url.startsWith(`${workspace()}/terminal/`)) start(entry);
      }
      const selected = tabs.find(t => t.number === saved.active);
      if (selected) activate(selected);
      if (saved.open && tabs.length) show(true);
    } catch { /* Ignore expired or invalid browser state. */ }
  }

  function renderTabs() {
    const connected = tabs.filter(target =>
      target.status === 'Connected' && !target.closed && !target.ended).length;
    toggle.classList.toggle('has-connected-terminal', connected > 0);
    const count = document.getElementById('terminal-connected-count');
    count.textContent = String(connected);
    count.hidden = connected === 0;
    const label = `Terminal (${connected} connected ${connected === 1 ? 'shell' : 'shells'})`;
    toggle.setAttribute('aria-label', label);
    toggle.title = label;
    tabBar.replaceChildren();
    for (const target of tabs) {
      const wrapper = document.createElement('div');
      wrapper.className = `file-panel-tab terminal-tab${target === active ? ' active' : ''}`;
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'file-tab-select';
      button.id = `terminal-tab-${target.number}`;
      button.setAttribute('role', 'tab');
      button.setAttribute('aria-selected', String(target === active));
      button.setAttribute('aria-controls', target.mount.id);
      button.tabIndex = target === active ? 0 : -1;
      button.textContent = 'Terminal';
      const icon = document.createElement('span');
      icon.className = 'file-tab-icon';
      icon.innerHTML = ICONS.mcpBash;
      button.prepend(icon);
      button.addEventListener('click', () => activate(target));
      button.addEventListener('keydown', event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const index = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1
          : (tabs.indexOf(target) + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
        activate(tabs[index]);
        document.getElementById(`terminal-tab-${tabs[index].number}`).focus();
      });
      const close = document.createElement('button');
      close.type = 'button';
      close.className = 'file-tab-close';
      close.innerHTML = ICONS.close;
      close.setAttribute('aria-label', `Close Terminal ${target.number}`);
      close.addEventListener('click', () => closeTab(target));
      wrapper.append(button, close);
      tabBar.append(wrapper);
    }
    tabBar.append(addButton);
    status.textContent = active?.status || 'Use + to open a terminal';
    screen.classList.toggle('empty', !tabs.length);
    saveTabs();
  }

  function activate(target) {
    active = target;
    for (const tab of tabs) tab.mount.hidden = tab !== target;
    renderTabs();
    resize();
    target.term.focus();
  }

  function resize() {
    height = Math.max(150, Math.min(height, Math.floor(window.innerHeight * .65)));
    main.style.setProperty('--terminal-height', `${height}px`);
    if (panel.hidden || !active) return;
    const target = active;
    target.fit.fit();
    clearTimeout(resizeTimer);
    if (target.url && !target.ended) resizeTimer = setTimeout(() => {
      if (!target.closed) api.post(target.url, { cols: target.term.cols, rows: target.term.rows })
        .then(result => { if (result.error) fail(target, result.error); })
        .catch(error => fail(target, error.message));
    }, 100);
  }

  function closeTab(target) {
    target.closed = true;
    clearTimeout(target.timer);
    if (target.url) api.delete(target.url).catch(() => {});
    const index = tabs.indexOf(target);
    if (index < 0) return;
    tabs.splice(index, 1);
    target.term.dispose();
    target.mount.remove();
    if (active === target) {
      active = null;
      if (tabs.length) activate(tabs[Math.min(index, tabs.length - 1)]);
      else document.getElementById('btn-add-terminal').focus();
    }
    renderTabs();
    refreshFilePanel().catch(() => {});
  }

  function fail(target, message) {
    if (target.closed || target.ended) return;
    target.ended = true;
    target.status = 'Shell ended';
    clearTimeout(target.timer);
    target.term.writeln(`\r\n[${message}]`);
    if (target.url) api.delete(target.url).catch(() => {});
    renderTabs();
  }

  async function poll(target) {
    if (target.closed || target.ended) return;
    try {
      const result = await api.get(target.url);
      if (target.closed) return;
      if (leaving) return;
      if (result.error) return fail(target, result.error);
      if (result.truncated) target.term.writeln('\r\n[Output truncated while terminal was inactive]');
      if (result.output) {
        target.term.write(result.output);
        target.output = ((target.output || '') + result.output).slice(-65536);
        saveTabs();
      }
      if (result.closed) return fail(target, 'Shell exited');
      if (target.status !== 'Connected') {
        target.status = 'Connected';
        renderTabs();
      }
      target.timer = setTimeout(() => poll(target), document.hidden ? 1500 : result.output ? 80 : 250);
    } catch (error) { if (!leaving) fail(target, error.message); }
  }

  async function start(saved = null) {
    if (!window.Terminal || !window.FitAddon) {
      status.textContent = 'Terminal assets could not load. Reload the application.';
      return;
    }
    const number = saved ? saved.number : serial + 1;
    serial = Math.max(serial, number);
    const mount = document.createElement('div');
    mount.id = `terminal-session-${number}`;
    mount.className = 'terminal-session';
    mount.setAttribute('role', 'tabpanel');
    mount.setAttribute('aria-labelledby', `terminal-tab-${number}`);
    screen.append(mount);
    const term = new window.Terminal({ cursorBlink: true, fontSize: 13, scrollback: 3000,
      fontFamily: '"JetBrains Mono", monospace', theme: terminalTheme() });
    const fit = new window.FitAddon.FitAddon();
    term.loadAddon(fit);
    term.open(mount);
    const target = { number, term, fit, mount, url: saved?.url, output: saved?.output || '', status: 'Connecting…', writes: Promise.resolve() };
    if (target.output) term.write(target.output);
    tabs.push(target);
    activate(target);
    term.onData(data => {
      if (!target.url || target.closed || target.ended) return;
      for (let i = 0; i < data.length; i += 2048) {
        const input = data.slice(i, i + 2048);
        target.writes = target.writes.then(async () => {
          if (target.closed || target.ended) return;
          const result = await api.post(target.url, { input });
          if (result.error) throw new Error(result.error);
        }).catch(error => fail(target, error.message));
      }
    });
    try {
      if (!workspace()) {
        if (!creatingWorkspace) creatingWorkspace = createNewConversation().finally(() => { creatingWorkspace = null; });
        await creatingWorkspace;
      }
      if (target.closed) return;
      const base = workspace();
      if (!target.url) {
        const result = await api.post(`${base}/terminal`, { cols: term.cols, rows: term.rows });
        if (result.error) throw new Error(result.error);
        target.url = `${base}/terminal/${encodeURIComponent(result.id)}`;
      }
      if (target.closed || base !== workspace()) {
        await api.delete(target.url);
        return;
      }
      target.status = saved ? 'Connecting…' : 'Connected';
      renderTabs();
      poll(target);
    } catch (error) { if (!leaving) fail(target, error.message); }
  }

  function show(open) {
    panel.hidden = !open;
    main.classList.toggle('terminal-open', open);
    toggle.setAttribute('aria-expanded', String(open));
    saveTabs();
    if (open) {
      if (!tabs.length) start();
      else { resize(); active?.term.focus(); }
    } else toggle.focus();
  }

  toggle.addEventListener('click', () => show(panel.hidden));
  document.getElementById('btn-close-terminal').addEventListener('click', () => show(false));
  document.getElementById('btn-add-terminal').addEventListener('click', () => start());
  document.addEventListener('app:ready', restoreTabs);
  document.addEventListener('chat:conversation-opened', () => {
    if (!restored) { restoreTabs(); return; }
    for (const target of [...tabs]) closeTab(target);
    // Navigation alone must never allocate a shell or create an empty chat.
    renderTabs();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !panel.hidden && !document.querySelector('.modal-overlay.open')) {
      show(false);
    }
  });
  // The main chat can shrink when either adjacent panel opens or closes.
  new ResizeObserver(resize).observe(document.getElementById('terminal-screen'));
  window.addEventListener('resize', resize);
  window.addEventListener('pagehide', () => {
    leaving = true;
    saveTabs();
    for (const target of tabs) clearTimeout(target.timer);
  });
  window.addEventListener('pageshow', event => {
    if (event.persisted) { leaving = false; for (const target of tabs) poll(target); }
  });
  const resizer = document.getElementById('terminal-resizer');
  resizer.addEventListener('pointerdown', event => {
    const y = event.clientY, initialHeight = height;
    resizer.setPointerCapture(event.pointerId);
    const move = next => { height = initialHeight + y - next.clientY; resize(); };
    const end = () => {
      resizer.removeEventListener('pointermove', move);
      resizer.removeEventListener('pointerup', end);
      resizer.removeEventListener('lostpointercapture', end);
    };
    resizer.addEventListener('pointermove', move);
    resizer.addEventListener('pointerup', end);
    resizer.addEventListener('lostpointercapture', end);
  });
  resizer.addEventListener('keydown', event => {
    if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
    event.preventDefault();
    height += event.key === 'ArrowUp' ? 30 : -30;
    resize();
  });
}
