/**
 * ClientConfig — multi-server config manager.
 *
 * Simplified: stores only server_url, target_server_id, and label per server.
 * No join request fields, no secure profile, no password handling.
 *
 *   - Persistent JSON config in %APPDATA%/HomepageClient (Win) or ~/.homepage_client (Linux/Mac)
 *   - Multi-server entries with active index
 *   - Seed file auto-application
 *   - Client ID generation
 *   - Old data directory migration
 */

'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const APP_NAME = 'HOMEPAGE Client';

const SEED_FILE_NAMES = [
  'homepage-client-seed.json',
  'house-fantastico-client-seed.json',
  'hf-client-seed.json',
];

/* ── helpers ────────────────────────────────────────────── */

function dataDir() {
  if (process.platform === 'win32') {
    const base = process.env.APPDATA || require('os').homedir();
    return path.join(base, 'HomepageClient');
  }
  return path.join(require('os').homedir(), '.homepage_client');
}

function bundleDir() {
  if (process.env.PORTABLE_EXECUTABLE_DIR) {
    return process.env.PORTABLE_EXECUTABLE_DIR;
  }
  if (require('electron').app?.isPackaged) {
    return path.dirname(process.execPath);
  }
  return path.resolve(__dirname);
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function ensureClientId(filePath) {
  ensureDir(path.dirname(filePath));
  if (fs.existsSync(filePath)) {
    const val = fs.readFileSync(filePath, 'utf-8').trim().toLowerCase();
    if (val) return val;
  }
  const id = crypto.randomUUID().replace(/-/g, '');
  fs.writeFileSync(filePath, id, 'utf-8');
  return id;
}

function migrateOldDataDir() {
  const newDir = dataDir();
  let oldDir;
  if (process.platform === 'win32') {
    const base = process.env.APPDATA || require('os').homedir();
    oldDir = path.join(base, 'HouseFantasticoClient');
  } else {
    oldDir = path.join(require('os').homedir(), '.house_fantastico_client');
  }
  if (fs.existsSync(oldDir) && !fs.existsSync(newDir)) {
    try {
      fs.cpSync(oldDir, newDir, { recursive: true });
    } catch (_) { /* ignore */ }
  }
}

/* ── empty entry template ───────────────────────────────── */

function emptyServerEntry(label) {
  return {
    label: label || 'New Server',
    server_url: '',
    target_server_id: '',
    user_renamed: false,
  };
}

/* ── main class ─────────────────────────────────────────── */

class ClientConfig {
  constructor() {
    migrateOldDataDir();
    this.dir = dataDir();
    ensureDir(this.dir);
    this._bundleDir = bundleDir();
    this.clientIdFile = path.join(this.dir, 'client_id.txt');
    this.configFile = path.join(this.dir, 'config.json');
    this.clientId = ensureClientId(this.clientIdFile);
    this.activeIndex = 0;
    this.servers = [];
    this.load();
  }

  get activeServer() {
    if (!this.servers.length) this.servers.push(emptyServerEntry());
    if (this.activeIndex >= this.servers.length) this.activeIndex = 0;
    return this.servers[this.activeIndex];
  }

  load() {
    let raw = {};
    if (fs.existsSync(this.configFile)) {
      try {
        raw = JSON.parse(fs.readFileSync(this.configFile, 'utf-8'));
      } catch (_) { /* ignore */ }
    }
    if (typeof raw !== 'object' || raw === null) raw = {};

    // Migration from single-server format
    if (!raw.servers && raw.server_url) {
      const entry = emptyServerEntry();
      entry.server_url = raw.server_url || '';
      entry.target_server_id = raw.target_server_id || '';
      const sid = String(entry.target_server_id || '').trim();
      entry.label = sid.slice(0, 16) || 'Server';
      this.servers = [entry];
      this.activeIndex = 0;
    } else {
      // Read servers, keeping only the fields we care about
      this.servers = (raw.servers || []).map(srv => ({
        label: srv.label || 'Server',
        server_url: srv.server_url || '',
        target_server_id: srv.target_server_id || '',
        user_renamed: !!srv.user_renamed,
      }));
      this.activeIndex = raw.active_server_index || 0;
    }

    if (!this.servers.length) this.servers.push(emptyServerEntry());
    if (this.activeIndex >= this.servers.length) this.activeIndex = 0;

    this._applySeedIfNeeded();
  }

  save() {
    const data = {
      active_server_index: this.activeIndex,
      servers: this.servers,
    };
    fs.writeFileSync(this.configFile, JSON.stringify(data, null, 2), 'utf-8');
  }

  addServer(label, seed) {
    const entry = emptyServerEntry(label);
    if (seed && typeof seed === 'object') {
      ClientConfig._applySeedToEntry(entry, seed);
    }
    this.servers.push(entry);
    return this.servers.length - 1;
  }

  removeServer(index) {
    if (this.servers.length <= 1) return;
    this.servers.splice(index, 1);
    if (this.activeIndex >= this.servers.length) {
      this.activeIndex = this.servers.length - 1;
    }
  }

  serverLabels() {
    return this.servers.map((srv, i) => {
      return srv.label || (srv.target_server_id || '').slice(0, 12) || `Server ${i + 1}`;
    });
  }

  _findSeedFile() {
    for (const name of SEED_FILE_NAMES) {
      const candidate = path.join(this._bundleDir, name);
      if (fs.existsSync(candidate)) return candidate;
    }
    return null;
  }

  static _applySeedToEntry(entry, raw) {
    if (raw.server_url != null) entry.server_url = String(raw.server_url);
    if (raw.target_server_id != null) entry.target_server_id = String(raw.target_server_id);
    if (!entry.user_renamed) {
      const sid = entry.target_server_id || '';
      const sname = raw.server_name || '';
      entry.label = String(sname || sid.slice(0, 16) || 'Seeded Server');
    }
  }

  _applySeedIfNeeded() {
    const seedPath = this._findSeedFile();
    if (!seedPath) return;
    let raw;
    try {
      raw = JSON.parse(fs.readFileSync(seedPath, 'utf-8'));
    } catch (_) { return; }
    if (typeof raw !== 'object' || raw === null) return;

    const targetId = String(raw.target_server_id || '').trim().toLowerCase();
    const applyAlways = ['1', 'true', 'yes', 'on'].includes(
      String(raw.apply_always || '').trim().toLowerCase()
    );

    let existingIdx = null;
    if (targetId) {
      for (let i = 0; i < this.servers.length; i++) {
        if (String(this.servers[i].target_server_id || '').trim().toLowerCase() === targetId) {
          existingIdx = i;
          break;
        }
      }
    }

    if (existingIdx !== null && !applyAlways) {
      const seedUrl = String(raw.server_url || '').trim();
      if (seedUrl && seedUrl !== this.servers[existingIdx].server_url) {
        this.servers[existingIdx].server_url = seedUrl;
        this.activeIndex = existingIdx;
        try { this.save(); } catch (_) { /* ignore */ }
      }
      return;
    }

    if (existingIdx !== null) {
      ClientConfig._applySeedToEntry(this.servers[existingIdx], raw);
      this.activeIndex = existingIdx;
    } else {
      const idx = this.addServer(null, raw);
      this.activeIndex = idx;
    }

    try { this.save(); } catch (_) { /* ignore */ }
  }
}

module.exports = { ClientConfig, APP_NAME, dataDir, bundleDir, emptyServerEntry };
