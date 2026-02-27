/**
 * Preload script — exposes safe IPC channels to the renderer.
 * contextIsolation is ON, so this is the only bridge.
 *
 * Simplified: no join requests, no password reset, no secure setup, no polling.
 */
'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('homepage', {
  // Config
  getConfig:      ()              => ipcRenderer.invoke('config:get'),
  saveConfig:     (data)          => ipcRenderer.invoke('config:save', data),
  switchServer:   (index)         => ipcRenderer.invoke('config:switchServer', index),
  addServer:      ()              => ipcRenderer.invoke('config:addServer'),
  removeServer:   (index)         => ipcRenderer.invoke('config:removeServer', index),
  renameServer:   (index, name)   => ipcRenderer.invoke('config:renameServer', index, name),

  // Server
  verifyServer:   (data)          => ipcRenderer.invoke('server:verify', data),

  // App
  openApp:        (data)          => ipcRenderer.invoke('app:open', data),
  writeClipboard: (text)          => ipcRenderer.invoke('clipboard:write', text),
});
