const DEFAULT_SERVER_HOST = "127.0.0.1";
const HTTP_PORT = 8080;
const WEBSOCKET_PORT = 8765;
const STORAGE_KEY = "chestnut_server_host";

function cleanHost(value) {
  return String(value || "")
    .trim()
    .replace(/^https?:\/\//i, "")
    .replace(/^wss?:\/\//i, "")
    .replace(/\/.*$/, "")
    .replace(/:\d+$/, "") || DEFAULT_SERVER_HOST;
}

function getServerHost() {
  return cleanHost(wx.getStorageSync(STORAGE_KEY) || DEFAULT_SERVER_HOST);
}

function setServerHost(host) {
  const clean = cleanHost(host);
  wx.setStorageSync(STORAGE_KEY, clean);
  return clean;
}

function websocketUrl() {
  return `ws://${getServerHost()}:${WEBSOCKET_PORT}`;
}

function apiUrl(path) {
  const suffix = String(path || "").startsWith("/") ? path : `/${path}`;
  return `http://${getServerHost()}:${HTTP_PORT}${suffix}`;
}

module.exports = {
  DEFAULT_SERVER_HOST,
  getServerHost,
  setServerHost,
  websocketUrl,
  apiUrl,
};
