const environment = require("../config/environment");
const key = () => `chestnut_access:${environment.isCloudEnabled() ? environment.CLOUD_ENV_ID : environment.getServerHost()}`;
function token() { return wx.getStorageSync(key()) || ""; }
function clear() { wx.removeStorageSync(key()); }
function request(path, method = "GET", data) {
  const header = { "content-type": "application/json", "X-WX-SERVICE": environment.CLOUD_SERVICE };
  if (token()) header.Authorization = `Bearer ${token()}`;
  const call = environment.isCloudEnabled()
    ? wx.cloud.callContainer({ config: environment.cloudConfig(), path, method, header, data })
    : new Promise((resolve, reject) => wx.request({ url: environment.apiUrl(path), method, header, data, timeout: 15000, success: resolve, fail: reject }));
  return call.then(response => {
    if (response.statusCode >= 200 && response.statusCode < 300) return response.data;
    const error = new Error(response.data?.error || `请求失败 (${response.statusCode})`);
    error.status = response.statusCode;
    if (error.status === 401) clear();
    throw error;
  });
}
async function authorized() {
  const status = await request("/api/auth/status");
  return !status.auth_required || status.authenticated;
}
function clientId() {
  let client = wx.getStorageSync("chestnut_client_id");
  if (!client) { client = `${Date.now()}-${Math.random().toString(36).slice(2)}`; wx.setStorageSync("chestnut_client_id", client); }
  return client;
}
async function recordVisit() {
  try { await request("/api/visits", "POST", { client_id: clientId(), channel: "miniprogram" }); }
  catch { /* Analytics must not block the service page. */ }
}
async function login(code) {
  const client = clientId();
  const result = await request("/api/auth/invite", "POST", { code, client_id: client, client_type: "miniprogram" });
  if (result.access_token) wx.setStorageSync(key(), result.access_token);
  else if (result.auth_required !== false) throw new Error("验证未完成，请重试");
}
module.exports = { token, clear, request, authorized, login, recordVisit };
