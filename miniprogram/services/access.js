const environment = require("../config/environment");
const key = () => `chestnut_access:v2:cloud:${environment.CLOUD_ENV_ID}:${environment.CLOUD_SERVICE}`;
let latestStatus = null;
let revision = 0;
function currentStatus() { return latestStatus?.key === key() ? latestStatus.value : null; }
function trialInfo() { const value = currentStatus(); return value?.access_mode === "trial" ? value.trial : null; }
function token() { return wx.getStorageSync(key()) || ""; }
function clear() { wx.removeStorageSync(key()); latestStatus = null; revision += 1; }
async function request(path, method = "GET", data) {
  const requestKey = key();
  const requestToken = token();
  const header = { "content-type": "application/json", "X-WX-SERVICE": environment.CLOUD_SERVICE, "X-Chestnut-Client-ID": clientId() };
  if (requestToken) header.Authorization = `Bearer ${requestToken}`;
  let response;
  try {
    if (typeof wx.cloud?.callContainer !== "function") throw new Error("当前微信不支持云端服务，请升级微信后重试");
    response = await wx.cloud.callContainer({ config: environment.cloudConfig(), path, method, header, data, timeout: 15000 });
  } catch (cause) {
    if (cause instanceof Error) throw cause;
    throw new Error(/timeout/i.test(cause?.errMsg || "") ? "服务响应超时，请稍后重试" : "无法连接服务，请检查网络后重试");
  }
  if (response.statusCode >= 200 && response.statusCode < 300) {
    if (!response.data || typeof response.data !== "object") throw new Error("云服务响应异常，请稍后重试");
    return response.data;
  }
  const detail = response.data?.error;
  const error = new Error(typeof detail === "string" ? detail : `服务暂不可用 (${response.statusCode})，请稍后重试`);
  error.status = response.statusCode;
  // A late response from the previous endpoint/session must not erase a new login.
  if (error.status === 401 && requestKey === key() && requestToken === token()) clear();
  throw error;
}
async function authorized() {
  const result = await status();
  return !result.auth_required || result.authenticated;
}
async function status() {
  const requestKey = key(), requestRevision = revision;
  const result = await request("/api/auth/status");
  if (requestKey !== key()) throw new Error("服务地址已切换，请重试");
  if (requestRevision !== revision) {
    const current = currentStatus();
    if (!current) throw new Error("登录状态已变化，请重试");
    return current;
  }
  latestStatus = { key: requestKey, value: result };
  return result;
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
  const requestKey = key(), requestRevision = revision;
  const client = clientId();
  const result = await request("/api/auth/invite", "POST", { code, client_id: client, client_type: "miniprogram" });
  if (requestKey !== key() || requestRevision !== revision) throw new Error("登录状态已变化，请重试");
  if (result.access_token) wx.setStorageSync(key(), result.access_token);
  else if (result.auth_required !== false) throw new Error("验证未完成，请重试");
  revision += 1;
  latestStatus = { key: requestKey, value: result };
}
async function startTrial() {
  const requestKey = key(), requestRevision = revision;
  const result = await request("/api/auth/trial", "POST", { client_id: clientId(), client_type: "miniprogram" });
  if (requestKey !== key() || requestRevision !== revision) throw new Error("登录状态已变化，请重试");
  if (!result.access_token) throw new Error("Unable to start the trial.");
  wx.setStorageSync(key(), result.access_token);
  revision += 1;
  latestStatus = { key: requestKey, value: result };
  return result;
}
module.exports = { token, clear, request, authorized, login, recordVisit, status, startTrial, trialInfo, currentStatus };
