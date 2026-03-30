/**
 * HOMEPAGE Client — Electron main process.
 *
 * Two windows:
 *   1. Setup window  — config UI (server URL + server ID)
 *   2. App window    — locked BrowserWindow pointed at the server (origin-locked)
 *
 * Simplified flow: no join requests, no password handling, no secure-mode setup.
 * The server handles all authentication via its own login page.
 *
 * Privacy controls are enforced at the Chromium process level (not JS injection).
 */

'use strict';

const { app, BrowserWindow, ipcMain, session, dialog, clipboard } = require('electron');
const path = require('path');
const fs = require('fs');
const https = require('https');
const http = require('http');
const { URL } = require('url');
const { ClientConfig, APP_NAME, dataDir } = require('./config');

/* ── globals ────────────────────────────────────────────── */

let cfg;
let setupWindow = null;
let appWindow = null;

/* ── URL helpers ────────────────────────────────────────── */

function normalizeServerUrl(raw) {
  const value = (raw || '').trim().replace(/\/+$/, '');
  if (!value) throw new Error('Server URL is required');
  let parsed;
  try { parsed = new URL(value); } catch { throw new Error('Invalid URL format'); }
  if (!['http:', 'https:'].includes(parsed.protocol) || !parsed.hostname) {
    throw new Error('Server URL must include scheme and host, e.g. https://example.com');
  }
  let base = `${parsed.protocol}//${parsed.hostname}`;
  if (parsed.port) base += `:${parsed.port}`;
  return base;
}

/* ── HTTP request helper ────────────────────────────────── */

function httpRequest(method, url, { json, params, timeout = 15000 } = {}) {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    if (params) {
      for (const [k, v] of Object.entries(params)) u.searchParams.set(k, v);
    }
    const body = json ? JSON.stringify(json) : null;
    const mod = u.protocol === 'https:' ? https : http;
    const opts = {
      method: method.toUpperCase(),
      hostname: u.hostname,
      port: u.port || (u.protocol === 'https:' ? 443 : 80),
      path: u.pathname + u.search,
      timeout,
      rejectUnauthorized: false,
      headers: {},
    };
    if (body) {
      opts.headers['Content-Type'] = 'application/json';
      opts.headers['Content-Length'] = Buffer.byteLength(body);
    }
    const req = mod.request(opts, (res) => {
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => {
        try {
          resolve({ status: res.statusCode, data: JSON.parse(data) });
        } catch {
          resolve({ status: res.statusCode, data: data });
        }
      });
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('Request timed out')); });
    if (body) req.write(body);
    req.end();
  });
}

/* ── setup window ───────────────────────────────────────── */

function createSetupWindow() {
  if (setupWindow && !setupWindow.isDestroyed()) {
    setupWindow.focus();
    return;
  }
  setupWindow = new BrowserWindow({
    width: 700,
    height: 440,
    resizable: false,
    title: APP_NAME,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });
  setupWindow.setMenuBarVisibility(false);
  setupWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'));
  setupWindow.on('closed', () => { setupWindow = null; });
}

/* ── app window (origin-locked browser) ─────────────────── */

function openAppWindow(serverUrl) {
  const allowedOrigin = normalizeServerUrl(serverUrl);

  if (appWindow && !appWindow.isDestroyed()) {
    appWindow.loadURL(allowedOrigin + '/');
    appWindow.focus();
    return;
  }

  const partitionName = `persist:homepage-${Buffer.from(allowedOrigin).toString('base64url').slice(0, 16)}`;
  const ses = session.fromPartition(partitionName);

  // Block ALL requests to non-server origins
  ses.webRequest.onBeforeRequest((details, callback) => {
    try {
      const reqUrl = new URL(details.url);
      if (['devtools:', 'data:', 'blob:', 'chrome:', 'chrome-extension:'].includes(reqUrl.protocol)) {
        return callback({});
      }
      const allowedUrl = new URL(allowedOrigin);
      if (reqUrl.hostname === allowedUrl.hostname) {
        return callback({});
      }
    } catch { /* block on parse error */ }
    console.log(`[HOMEPAGE] Blocked request to: ${details.url}`);
    callback({ cancel: true });
  });

  // Block permission requests (except media for voice rooms)
  ses.setPermissionRequestHandler((_wc, permission, callback) => {
    if (['media', 'mediaKeySystem'].includes(permission)) {
      callback(true);
    } else {
      callback(false);
    }
  });

  // Trust the configured server's certificate (self-signed support)
  ses.setCertificateVerifyProc((_request, callback) => {
    callback(0);
  });

  appWindow = new BrowserWindow({
    width: 1200,
    height: 820,
    title: APP_NAME,
    webPreferences: {
      session: ses,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webrtcIPHandlingPolicy: 'default_public_interface_only',
    },
  });
  appWindow.setMenuBarVisibility(false);

  // Block navigation to foreign origins
  appWindow.webContents.on('will-navigate', (event, navUrl) => {
    try {
      const u = new URL(navUrl);
      const a = new URL(allowedOrigin);
      if (u.hostname !== a.hostname) {
        event.preventDefault();
        console.log(`[HOMEPAGE] Blocked navigation to: ${navUrl}`);
      }
    } catch {
      event.preventDefault();
    }
  });

  // Block new window creation (popups, target="_blank")
  appWindow.webContents.setWindowOpenHandler(({ url }) => {
    console.log(`[HOMEPAGE] Blocked popup: ${url}`);
    return { action: 'deny' };
  });

  appWindow.loadURL(allowedOrigin + '/');
  appWindow.on('closed', () => { appWindow = null; });
}

/* ── IPC handlers ───────────────────────────────────────── */

function setupIPC() {
  // Get current state
  ipcMain.handle('config:get', () => {
    return {
      clientId: cfg.clientId,
      activeIndex: cfg.activeIndex,
      servers: cfg.servers,
      labels: cfg.serverLabels(),
    };
  });

  // Save active server from UI
  ipcMain.handle('config:save', (_e, serverData) => {
    Object.assign(cfg.activeServer, serverData);
    const sid = serverData.target_server_id || '';
    if (sid) cfg.activeServer.label = sid.slice(0, 16);
    cfg.save();
    return { ok: true, labels: cfg.serverLabels() };
  });

  // Switch active server
  ipcMain.handle('config:switchServer', (_e, index) => {
    cfg.activeIndex = index;
    return {
      server: cfg.activeServer,
      labels: cfg.serverLabels(),
    };
  });

  // Add server
  ipcMain.handle('config:addServer', () => {
    const idx = cfg.addServer('New Server');
    cfg.activeIndex = idx;
    cfg.save();
    return {
      activeIndex: idx,
      servers: cfg.servers,
      labels: cfg.serverLabels(),
    };
  });

  // Rename server
  ipcMain.handle('config:renameServer', (_e, index, name) => {
    if (index >= 0 && index < cfg.servers.length && name) {
      cfg.servers[index].label = name;
      cfg.servers[index].user_renamed = true;
      cfg.save();
    }
    return { ok: true, labels: cfg.serverLabels() };
  });

  // Remove server
  ipcMain.handle('config:removeServer', (_e, index) => {
    if (cfg.servers.length <= 1) return { error: 'Cannot remove the last server entry.' };
    cfg.removeServer(index);
    cfg.save();
    return {
      activeIndex: cfg.activeIndex,
      servers: cfg.servers,
      labels: cfg.serverLabels(),
    };
  });

  // Verify server identity before connecting
  ipcMain.handle('server:verify', async (_e, { serverUrl, targetServerId }) => {
    const url = normalizeServerUrl(serverUrl);
    try {
      const resp = await httpRequest('GET', `${url}/.well-known/server-id`);
      if (resp.status >= 400) {
        // Fallback: try connect-profile
        const resp2 = await httpRequest('GET', `${url}/.well-known/connect-profile`);
        if (resp2.status >= 400) {
          return { ok: false, detail: 'Cannot reach server identity endpoint.' };
        }
        const serverId = (resp2.data?.server_id || '').trim().toLowerCase();
        const target = targetServerId.trim().toLowerCase();
        if (serverId !== target) {
          return { ok: false, detail: `Server ID mismatch. Expected ${target}, got ${serverId || '(none)'}.` };
        }
        return { ok: true, serverId };
      }
      const serverId = (resp.data?.server_id || '').trim().toLowerCase();
      const target = targetServerId.trim().toLowerCase();
      if (serverId !== target) {
        return { ok: false, detail: `Server ID mismatch. Expected ${target}, got ${serverId || '(none)'}.` };
      }
      return { ok: true, serverId };
    } catch (err) {
      return { ok: false, detail: `Cannot reach server: ${err.message}` };
    }
  });

  // Open the app window
  ipcMain.handle('app:open', (_e, { serverUrl }) => {
    openAppWindow(serverUrl);
    return { ok: true };
  });

  // Clipboard
  ipcMain.handle('clipboard:write', (_e, text) => {
    clipboard.writeText(text);
    return { ok: true };
  });
}

/* ── app lifecycle ──────────────────────────────────────── */

// Chromium command-line switches for privacy hardening
app.commandLine.appendSwitch('disable-background-networking');
app.commandLine.appendSwitch('disable-client-side-phishing-detection');
app.commandLine.appendSwitch('disable-default-apps');
app.commandLine.appendSwitch('disable-extensions');
app.commandLine.appendSwitch('disable-component-update');
app.commandLine.appendSwitch('disable-domain-reliability');
app.commandLine.appendSwitch('disable-sync');
app.commandLine.appendSwitch('disable-translate');
app.commandLine.appendSwitch('disable-breakpad');
app.commandLine.appendSwitch('no-pings');
app.commandLine.appendSwitch('disable-features',
  'AutofillServerCommunication,SafeBrowsing,SpareRendererForSitePerProcess,' +
  'OptimizationHints,MediaRouter,DialMediaRouteProvider,Translate,' +
  'NetworkTimeServiceQuerying,CertificateTransparencyComponentUpdater'
);
app.commandLine.appendSwitch('dns-prefetch-disable');
app.commandLine.appendSwitch('disable-preconnect');
app.commandLine.appendSwitch('metrics-recording-only');
app.commandLine.appendSwitch('disable-metrics');
app.commandLine.appendSwitch('disable-metrics-reporting');
app.commandLine.appendSwitch('disable-speech-api');
app.commandLine.appendSwitch('disable-remote-fonts');
app.commandLine.appendSwitch('disable-gpu-shader-disk-cache');
app.commandLine.appendSwitch('autoplay-policy', 'user-gesture-required');

app.whenReady().then(() => {
  cfg = new ClientConfig();
  setupIPC();
  createSetupWindow();
});

app.on('window-all-closed', () => {
  app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createSetupWindow();
});
