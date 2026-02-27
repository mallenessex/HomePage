/**
 * HOMEPAGE Client — renderer logic.
 *
 * Talks exclusively through window.homepage (exposed by preload.js).
 * No Node.js access, no direct IPC — contextIsolation is ON.
 *
 * Simplified flow: Server URL + Server ID → Connect.
 * The server handles all login and account management.
 */
'use strict';

/* ── DOM references ─────────────────────────────────────── */

const $  = (id) => document.getElementById(id);
const el = {
  clientId:          $('clientId'),
  serverCombo:       $('serverCombo'),
  btnAddServer:      $('btnAddServer'),
  btnRenameServer:   $('btnRenameServer'),
  btnRemoveServer:   $('btnRemoveServer'),
  serverUrl:         $('serverUrl'),
  targetServerId:    $('targetServerId'),
  effectiveUrl:      $('effectiveUrl'),
  // Connect panel
  connectPanel:      $('connectPanel'),
  btnConnect:        $('btnConnect'),
  btnSaveConfig:     $('btnSaveConfig'),
  // Status
  statusBar:         $('statusBar'),
  // Modals
  promptModal:       $('promptModal'),
  promptTitle:       $('promptTitle'),
  promptMsg:         $('promptMsg'),
  promptInput:       $('promptInput'),
  promptOk:          $('promptOk'),
  promptCancel:      $('promptCancel'),
  confirmModal:      $('confirmModal'),
  confirmTitle:      $('confirmTitle'),
  confirmMsg:        $('confirmMsg'),
  confirmYes:        $('confirmYes'),
  confirmNo:         $('confirmNo'),
  alertModal:        $('alertModal'),
  alertTitle:        $('alertTitle'),
  alertMsg:          $('alertMsg'),
  alertOk:           $('alertOk'),
};

/* ── state ──────────────────────────────────────────────── */

let state = {
  clientId: '',
  activeIndex: 0,
  servers: [],
  labels: [],
};

/* ── helpers ────────────────────────────────────────────── */

function status(msg, type) {
  el.statusBar.textContent = msg;
  el.statusBar.className = 'status-bar' + (type ? ` ${type}` : '');
  if (type !== 'error') {
    setTimeout(() => {
      if (el.statusBar.textContent === msg) el.statusBar.textContent = '';
    }, 6000);
  }
}

function showAlert(title, msg) {
  return new Promise((resolve) => {
    el.alertTitle.textContent = title;
    el.alertMsg.textContent = msg;
    el.alertModal.classList.remove('hidden');
    const handler = () => {
      el.alertModal.classList.add('hidden');
      el.alertOk.removeEventListener('click', handler);
      resolve();
    };
    el.alertOk.addEventListener('click', handler);
  });
}

function showConfirm(title, msg) {
  return new Promise((resolve) => {
    el.confirmTitle.textContent = title;
    el.confirmMsg.textContent = msg;
    el.confirmModal.classList.remove('hidden');
    const yes = () => { cleanup(); resolve(true); };
    const no  = () => { cleanup(); resolve(false); };
    function cleanup() {
      el.confirmModal.classList.add('hidden');
      el.confirmYes.removeEventListener('click', yes);
      el.confirmNo.removeEventListener('click', no);
    }
    el.confirmYes.addEventListener('click', yes);
    el.confirmNo.addEventListener('click', no);
  });
}

function showPrompt(title, msg, defaultValue) {
  return new Promise((resolve) => {
    el.promptTitle.textContent = title;
    el.promptMsg.textContent = msg;
    el.promptInput.value = defaultValue || '';
    el.promptModal.classList.remove('hidden');
    el.promptInput.focus();
    el.promptInput.select();
    const ok = () => { cleanup(); resolve(el.promptInput.value.trim()); };
    const cancel = () => { cleanup(); resolve(null); };
    const keydown = (e) => { if (e.key === 'Enter') ok(); else if (e.key === 'Escape') cancel(); };
    function cleanup() {
      el.promptModal.classList.add('hidden');
      el.promptOk.removeEventListener('click', ok);
      el.promptCancel.removeEventListener('click', cancel);
      el.promptInput.removeEventListener('keydown', keydown);
    }
    el.promptOk.addEventListener('click', ok);
    el.promptCancel.addEventListener('click', cancel);
    el.promptInput.addEventListener('keydown', keydown);
  });
}

/* ── normalize URL (client-side mirror for effective URL) ─ */

function normalizeUrl(raw) {
  const v = (raw || '').trim().replace(/\/+$/, '');
  if (!v) return '';
  try {
    const u = new URL(v);
    if (!['http:', 'https:'].includes(u.protocol) || !u.hostname) return v;
    let base = `${u.protocol}//${u.hostname}`;
    if (u.port) base += `:${u.port}`;
    return base;
  } catch { return v; }
}

/* ── update effective URL display ────────────────────── */

function updateEffectiveUrl() {
  const norm = normalizeUrl(el.serverUrl.value);
  el.effectiveUrl.textContent = norm || '(unset)';
}

/* ── populate server combo ───────────────────────────── */

function refreshCombo() {
  el.serverCombo.innerHTML = '';
  state.labels.forEach((label, i) => {
    const opt = document.createElement('option');
    opt.value = i;
    opt.textContent = label;
    el.serverCombo.appendChild(opt);
  });
  el.serverCombo.value = state.activeIndex;
}

/* ── load server data into UI fields ─────────────────── */

function loadServerToUI(srv) {
  el.serverUrl.value      = srv.server_url || '';
  el.targetServerId.value = srv.target_server_id || '';
  updateEffectiveUrl();
}

/* ── gather current UI state for saving ──────────────── */

function gatherServerData() {
  return {
    server_url:       el.serverUrl.value.trim(),
    target_server_id: el.targetServerId.value.trim().toLowerCase(),
  };
}

/* ── actions ─────────────────────────────────────────── */

async function doSaveConfig() {
  try {
    const data = gatherServerData();
    const result = await window.homepage.saveConfig(data);
    if (result.labels) { state.labels = result.labels; refreshCombo(); }
    status('Config saved', 'success');
  } catch (e) {
    status(`Save failed: ${e.message}`, 'error');
  }
}

async function doConnect() {
  try {
    const url = el.serverUrl.value.trim();
    const tid = el.targetServerId.value.trim().toLowerCase();
    if (!url) { await showAlert('Error', 'Server URL is required.'); return; }
    if (!tid) { await showAlert('Error', 'Target Server ID is required.'); return; }

    // Verify the server ID matches before opening
    status('Verifying server identity…');
    const verified = await window.homepage.verifyServer({
      serverUrl: url,
      targetServerId: tid,
    });

    if (!verified.ok) {
      await showAlert('Server Mismatch', verified.detail || 'Server ID does not match.');
      status('Connection refused — server ID mismatch', 'error');
      return;
    }

    // Save and open
    await doSaveConfig();
    status('Opening contained app…');
    await window.homepage.openApp({ serverUrl: url });
    status('App opened', 'success');
  } catch (e) {
    status(`Connect failed: ${e.message}`, 'error');
    await showAlert('Connect Failed', e.message);
  }
}

/* ── event wiring ────────────────────────────────────── */

function wireEvents() {
  // Server selector
  el.serverCombo.addEventListener('change', async () => {
    await doSaveConfig();
    const idx = parseInt(el.serverCombo.value, 10);
    const result = await window.homepage.switchServer(idx);
    state.activeIndex = idx;
    state.labels = result.labels;
    loadServerToUI(result.server);
    status(`Switched to server ${idx + 1}`);
  });

  el.btnAddServer.addEventListener('click', async () => {
    await doSaveConfig();
    const result = await window.homepage.addServer();
    state.activeIndex = result.activeIndex;
    state.servers = result.servers;
    state.labels = result.labels;
    refreshCombo();
    loadServerToUI(result.servers[result.activeIndex]);
    status('New server added');
  });

  el.btnRenameServer.addEventListener('click', async () => {
    const current = state.labels[state.activeIndex] || `Server ${state.activeIndex + 1}`;
    const newName = await showPrompt('Rename Server', 'Enter a new name for this server:', current);
    if (newName === null || newName === '') return;
    const result = await window.homepage.renameServer(state.activeIndex, newName);
    if (result.labels) { state.labels = result.labels; refreshCombo(); }
    status('Server renamed', 'success');
  });

  el.btnRemoveServer.addEventListener('click', async () => {
    if (state.servers.length <= 1) {
      await showAlert('Cannot Remove', 'Cannot remove the last server entry.');
      return;
    }
    const label = state.labels[state.activeIndex] || `Server ${state.activeIndex + 1}`;
    const yes = await showConfirm('Remove Server', `Remove server "${label}"?`);
    if (!yes) return;
    const result = await window.homepage.removeServer(state.activeIndex);
    if (result.error) { await showAlert('Error', result.error); return; }
    state.activeIndex = result.activeIndex;
    state.servers = result.servers;
    state.labels = result.labels;
    refreshCombo();
    loadServerToUI(state.servers[state.activeIndex]);
    status('Server removed');
  });

  // Server URL → effective URL
  el.serverUrl.addEventListener('input', updateEffectiveUrl);

  // Connect panel buttons
  el.btnConnect.addEventListener('click', doConnect);
  el.btnSaveConfig.addEventListener('click', doSaveConfig);

  // Client ID copy on click
  el.clientId.addEventListener('click', () => {
    window.homepage.writeClipboard(state.clientId);
    status('Client ID copied to clipboard', 'success');
  });
}

/* ── init ────────────────────────────────────────────── */

async function init() {
  try {
    const cfg = await window.homepage.getConfig();
    state.clientId    = cfg.clientId;
    state.activeIndex = cfg.activeIndex;
    state.servers     = cfg.servers;
    state.labels      = cfg.labels;

    el.clientId.textContent = `Client ID: ${state.clientId}`;
    refreshCombo();
    loadServerToUI(cfg.servers[cfg.activeIndex] || {});
    wireEvents();
  } catch (e) {
    status(`Failed to load config: ${e.message}`, 'error');
  }
}

document.addEventListener('DOMContentLoaded', init);
