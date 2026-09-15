const DEFAULT_SERVER_HOST = "127.0.0.1";
const HTTP_PORT = 8080;
const STORAGE_KEY = "chestnut_server_host";

// Fill this after creating the WeChat CloudBase environment. Leaving it empty
// keeps the Mini Program in local/LAN development mode.
const CLOUD_ENV_ID = "chestnut-prod-d6ggcq8yzf8d2e322";
const CLOUD_SERVICE = "chestnut-api";
// auto: developer builds use local/LAN; trial/release builds use Cloud Run.
// Set cloud to test the deployed service in developer tools, or local for LAN.
const TRANSPORT_MODE = "auto";

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

function isLoopbackHost(host) {
  const clean = cleanHost(host).toLowerCase();
  return clean === "127.0.0.1" || clean === "localhost" || clean === "::1";
}

function isCloudEnabled() {
  if (TRANSPORT_MODE === "local") return false;
  if (TRANSPORT_MODE === "auto" && typeof wx.getAccountInfoSync === "function") {
    if (wx.getAccountInfoSync().miniProgram.envVersion === "develop") return false;
  }
  return Boolean(CLOUD_ENV_ID && CLOUD_SERVICE);
}

function cloudConfig() {
  return { env: CLOUD_ENV_ID };
}

function websocketUrl() {
  return `ws://${getServerHost()}:${HTTP_PORT}/ws`;
}

function apiUrl(path) {
  const suffix = String(path || "").startsWith("/") ? path : `/${path}`;
  return `http://${getServerHost()}:${HTTP_PORT}${suffix}`;
}

module.exports = {
  DEFAULT_SERVER_HOST,
  CLOUD_ENV_ID,
  CLOUD_SERVICE,
  TRANSPORT_MODE,
  getServerHost,
  setServerHost,
  isLoopbackHost,
  isCloudEnabled,
  cloudConfig,
  websocketUrl,
  apiUrl,
};
