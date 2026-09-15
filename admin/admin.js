"use strict";
const $ = selector => document.querySelector(selector);
const state = { csrf: "", setup: false, tab: "codes", codePage: 1, codeStatus: "", search: "", eventPage: 1, eventCode: "", eventLabel: "", eventKind: "", alertPage: 1, alertState: "open", generated: [], pendingCode: null };
const titles = { codes: "邀请码管理", overview: "访问与用码", alerts: "异常提示", events: "活动记录" };
const statusNames = { active: "可使用", disabled: "已停用", expired: "已过期", exhausted: "额度用完" };
const kindNames = { visit: "服务页访问", invite_success: "验证成功", invite_failure: "验证失败", invite_rate_limited: "验证限流", access_denied: "凭证失效", connection_limited: "连接限流", meeting_started: "会议接入", meeting_ended: "会议连接结束", meeting_rejected: "会议被拒绝", transcript_saved: "会议稿已保存", admin_created: "生成邀请码", admin_enabled: "恢复邀请码", admin_disabled: "停用邀请码", admin_revealed: "查看明文", admin_login: "管理员登录", admin_login_failure: "管理员登录失败", admin_acknowledged: "核实异常提示" };
const reasons = { unknown: "邀请码不存在", disabled: "邀请码已停用", expired: "邀请码已过期", exhausted: "验证额度用完", websocket: "实时连接凭证无效" };
const alertNames = { repeated_failures: "短时间连续验证失败", shared_code: "同一码被多个客户端使用", unavailable_code: "不可用邀请码被反复尝试", invite_rate_limited: "频繁触发验证限流", admin_login_failure: "管理员登录连续失败" };
const channelNames = { web: "Web", wechat: "微信", miniprogram: "小程序", admin: "管理后台", local: "本地" };
let toastTimer;
let codeRequest = 0;
let eventRequest = 0;
let alertRequest = 0;
state.codeSort = "created_desc";
state.expiryPreset = "forever";
function node(tag, cls, text) {
  const element = document.createElement(tag);
  if (cls) element.className = cls;
  if (text !== undefined) element.textContent = text;
  return element;
}
function button(label, action, cls = "inline-button") {
  const element = node("button", cls, label);
  element.type = "button";
  element.addEventListener("click", () => run(async () => {
    element.disabled = true;
    try { await action(element); } finally { element.disabled = false; }
  }));
  return element;
}
function timeLabel(value, short = false) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", ...(short ? {} : { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }) }).format(new Date(value * 1000));
}
function toast(message) {
  clearTimeout(toastTimer);
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  toastTimer = setTimeout(() => { $("#toast").hidden = true; }, 3200);
}
function showLogin() {
  state.csrf = "";
  state.generated = [];
  state.pendingCode = null;
  codeRequest++; eventRequest++; alertRequest++;
  for (const dialog of document.querySelectorAll("dialog[open]")) dialog.close();
  $("#workspace").hidden = true;
  $("#login-screen").hidden = false;
  $("#code-rows").replaceChildren();
  $("#generated-list").replaceChildren();
  $("#password").value = "";
  $("#confirm-password").value = "";
  $("#login-title").textContent = state.setup ? "设置管理工作台" : "欢迎回到管理后台";
  $("#login-description").textContent = state.setup ? "首次使用，请为这台电脑设置一个至少 12 个字符的管理员密码。" : "输入管理员密码，继续管理邀请与访问。";
  $("#login-submit").textContent = state.setup ? "设置密码并进入" : "登录后台";
  $("#login-submit").disabled = false;
  $("#confirm-label").hidden = !state.setup;
  $("#confirm-password").required = state.setup;
  $("#password").autocomplete = state.setup ? "new-password" : "current-password";
}
async function api(path, { method = "GET", data } = {}) {
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), 15000);
  try {
    const response = await fetch(`/api/admin/${path}`, { method, signal: abort.signal, cache: "no-store", headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf }, ...(data === undefined ? {} : { body: JSON.stringify(data) }) });
    const result = await response.json();
    if (!response.ok) {
      if (response.status === 401 && path !== "login") { state.setup = false; showLogin(); }
      throw new Error(result.error || `请求失败 (${response.status})`);
    }
    return result;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("请求超时，请刷新数据确认操作结果后再试。");
    throw error;
  } finally { clearTimeout(timer); }
}
async function run(action) {
  $("#global-error").hidden = true;
  try { await action(); }
  catch (error) {
    if (!state.csrf) $("#login-error").textContent = error.message;
    else { $("#global-error").textContent = `${error.message} 可点击页面底部“刷新数据”重试。`; $("#global-error").hidden = false; }
  }
}
async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast("已复制到剪贴板"); }
  catch { throw new Error("无法访问剪贴板，请选中页面上的邀请码手动复制。"); }
}
function pagination(prefix, data) {
  const pages = Math.max(1, Math.ceil(data.total / data.page_size));
  $(`#${prefix}-total`).textContent = `共 ${data.total} 条`;
  $(`#${prefix}-page`).textContent = `${data.page} / ${pages}`;
  $(`#${prefix}-prev`).disabled = data.page <= 1;
  $(`#${prefix}-next`).disabled = data.page >= pages;
}
function updated() { $("#updated-at").textContent = `更新于 ${timeLabel(Date.now()/1000)}`; }
async function summary() {
  const result = await api("overview");
  if (!state.csrf) return;
  $("#summary-codes").textContent = result.total_codes;
  $("#summary-successes").textContent = result.successes;
  $("#summary-alerts").textContent = result.open_alerts;
  $("#code-count").textContent = result.total_codes;
  $("#alert-count").textContent = result.open_alerts;
  updated();
}
async function loadCodes() {
  const request = ++codeRequest;
  const result = await api(`codes?${new URLSearchParams({ q: state.search, status: state.codeStatus, page: state.codePage, sort: state.codeSort })}`);
  if (request !== codeRequest || !state.csrf) return;
  const rows = result.items.map(code => {
    const row = node("tr");
    row.dataset.codeId = code.id;
    row.dataset.createdAt = code.created_at;
    row.dataset.expiresAt = code.expires_at || "";
    if (code.expiry_warning) row.classList.add(`expiry-${code.expiry_warning}`);
    const name = node("td");
    name.append(node("strong", "", code.label));
    const line = node("div", "code-line");
    const value = node("span", "code", code.masked_code);
    let revealed = "";
    line.append(value, button("查看", async control => {
      if (control.textContent === "隐藏") { revealed = ""; value.textContent = code.masked_code; control.textContent = "查看"; return; }
      const result = await api(`codes/${code.id}/reveal`, { method: "POST", data: {} });
      if (!state.csrf) return;
      revealed = result.code; value.textContent = revealed; control.textContent = "隐藏";
    }), button("复制", async () => {
      if (!revealed) revealed = (await api(`codes/${code.id}/reveal`, { method: "POST", data: {} })).code;
      await copy(revealed);
    }));
    name.append(line);
    if (code.note) { const note = node("span", "sub note", code.note); note.title = code.note; name.append(note); }
    if (code.source === "environment") name.append(node("span", "sub", "来自原有配置"));
    const status = node("td"); status.append(node("span", `badge ${code.status}`, statusNames[code.status]));
    const uses = node("td"); uses.append(node("strong", "", `${code.use_count} / ${code.max_uses || "不限"}`), node("span", "sub", `近 90 天失败 ${code.failures} 次`));
    const users = node("td"); users.append(node("strong", "", `${code.clients} 个`), node("span", "sub", "近 90 天匿名客户端"));
    const dates = node("td"); dates.append(node("span", "", code.expires_at ? timeLabel(code.expires_at) : "长期有效"), node("span", "sub", `最近：${timeLabel(code.last_used_at)}`));
    dates.append(node("span", "sub", `生成：${timeLabel(code.created_at)}`));
    if (code.expiry_warning) dates.prepend(node("span", `expiry-label ${code.expiry_warning}`, code.expiry_warning === "urgent" ? "1 小时内过期" : "24 小时内过期"));
    const actions = node("td"); const wrap = node("div", "row-actions");
    wrap.append(button("记录", () => viewEvents(code.id, `${code.label} · ${code.masked_code}`)), button(code.enabled ? "停用" : "恢复", () => confirmCode(code), code.enabled ? "inline-button danger-text" : "inline-button"));
    actions.append(wrap); row.append(name, status, uses, users, dates, actions); return row;
  });
  $("#code-rows").replaceChildren(...rows);
  $("#codes-empty").hidden = Boolean(result.total || state.search || state.codeStatus);
  $("#codes-no-match").hidden = Boolean(result.total || (!state.search && !state.codeStatus));
  pagination("codes", result); updated();
}
function confirmCode(code) {
  state.pendingCode = code;
  const disabling = code.enabled;
  $("#confirm-title").textContent = disabling ? "停用这个邀请码？" : "恢复这个邀请码？";
  $("#confirm-description").textContent = `客户：${code.label}。` + (disabling ? "停用后不能再验证，已签发的凭证会失效，正在使用该码的会议也会断开。" : "恢复后可重新验证；已有的失效凭证不会恢复。有效期和验证额度仍按原设置执行。");
  $("#confirm-submit").textContent = disabling ? "确认停用" : "确认恢复";
  $("#confirm-submit").className = disabling ? "button danger" : "button primary";
  $("#confirm-error").textContent = "";
  $("#confirm-dialog").showModal();
}
async function switchTab(tab) {
  state.tab = tab;
  for (const item of document.querySelectorAll("[data-tab]")) { item.classList.toggle("active", item.dataset.tab === tab); item.setAttribute("aria-current", item.dataset.tab === tab ? "page" : "false"); }
  for (const panel of document.querySelectorAll(".tab-panel")) panel.hidden = panel.id !== `${tab}-panel`;
  $("#breadcrumb").textContent = titles[tab];
  await ({ codes: loadCodes, overview: loadOverview, alerts: loadAlerts, events: loadEvents })[tab]();
}
async function viewEvents(codeId, label) {
  state.eventCode = codeId; state.eventLabel = label; state.eventPage = 1; state.eventKind = "";
  $("#event-kind").value = "";
  await switchTab("events");
}
function todayString() { return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date()); }
function dateRange(days) {
  const today = todayString();
  const start = new Date(`${today}T00:00:00Z`); start.setUTCDate(start.getUTCDate() - days + 1);
  $("#date-start").value = start.toISOString().slice(0,10); $("#date-end").value = today;
  $("#date-start").max = today; $("#date-end").max = today;
  const earliest = new Date(`${today}T00:00:00Z`); earliest.setUTCDate(earliest.getUTCDate()-89);
  $("#date-start").min = earliest.toISOString().slice(0,10); $("#date-end").min = $("#date-start").min;
}
async function loadOverview() {
  const result = await api(`overview?${new URLSearchParams({ start: $("#date-start").value, end: $("#date-end").value })}`);
  if (!state.csrf) return;
  const metrics = [["服务页访问", result.visits, "服务页展示次数"], ["访问客户端", result.visitors, "所选日期内去重"], ["成功验证", result.successes, `${result.users} 个去重客户端 · ${result.failures} 次失败/限流`], ["会议接入", result.meetings, "相同客户端、同场重连去重"]];
  $("#metrics").replaceChildren(...metrics.map(([label, value, hint]) => { const element = node("div", "metric"); element.append(node("span", "small muted", label), node("strong", "", value), node("p", "", hint)); return element; }));
  const maximum = Math.max(1, ...result.daily.map(day => day.visits));
  $("#traffic-chart").replaceChildren(...result.daily.map(day => {
    const column = node("div", "chart-column");
    column.title = `${day.date}：${day.visits} 次访问，${day.visitors} 个客户端`;
    const bars = node("div", "bars");
    for (const key of ["visits", "visitors"]) { const bar = node("div", `bar ${key}`); bar.style.height = `${Math.max(1, day[key]/maximum*115)}px`; bars.append(bar); }
    column.append(node("span", "chart-value", `${day.visits} / ${day.visitors}`), bars, node("span", "chart-label", day.date.slice(5))); return column;
  }));
  const daily = node("table"); const head = node("thead"); const heading = node("tr");
  for (const label of ["日期", "访问次数", "访问客户端", "成功验证", "失败 / 限流", "会议接入"]) heading.append(node("th", "", label));
  head.append(heading); const tbody = node("tbody");
  for (const day of [...result.daily].reverse()) { const row = node("tr"); for (const value of [day.date, day.visits, day.visitors, day.successes, day.failures, day.meetings]) row.append(node("td", "", value)); tbody.append(row); }
  daily.append(head, tbody); $("#daily-table").replaceChildren(daily);
  $("#usage-rows").replaceChildren(...result.usage.map(item => {
    const row = node("tr");
    const name = node("td"); name.append(node("strong", "", item.label), node("span", "sub", `尾号 ${item.code_suffix}`)); row.append(name);
    for (const value of [item.successes, item.failures, item.clients, item.meetings]) row.append(node("td", "", value));
    const action = node("td"); action.append(button("查看记录", () => viewEvents(item.code_id, `${item.label} · 尾号 ${item.code_suffix}`))); row.append(action); return row;
  }));
  $("#usage-empty").hidden = result.usage.length > 0;
  updated();
}
async function loadEvents() {
  const request = ++eventRequest;
  const result = await api(`events?${new URLSearchParams({ code_id: state.eventCode, page: state.eventPage, kind: state.eventKind })}`);
  if (request !== eventRequest || !state.csrf) return;
  $("#event-scope").textContent = state.eventCode ? `客户：${state.eventLabel}` : "全部邀请码";
  $("#event-rows").replaceChildren(...result.items.map(item => {
    const row = node("tr");
    const when = node("td", "", timeLabel(item.last_at || item.created_at));
    const activity = node("td", "", kindNames[item.kind] || item.kind);
    if (item.occurrences > 1) {
      activity.append(node("span", "sub", item.kind === "meeting_started" ? `接入 ${item.occurrences} 次（含重连）` : `合并 ${item.occurrences} 次`));
      when.append(node("span", "sub", `首次：${timeLabel(item.created_at)}`));
    }
    row.append(when, activity);
    const name = node("td"); name.append(node("span", "", item.label || "—"));
    if (item.code_suffix) name.append(node("span", "sub", `尾号 ${item.code_suffix}`)); row.append(name);
    const who = node("td"); who.append(node("span", "code", item.client || "—"), node("span", "sub", `网络：${item.network || "—"}`));
    row.append(who, node("td", "", channelNames[item.channel] || "—"), node("td", "", reasons[item.reason] || item.reason || "—")); return row;
  }));
  $("#events-empty").hidden = result.items.length > 0;
  pagination("events", result); updated();
}
async function loadAlerts() {
  const request = ++alertRequest;
  const result = await api(`alerts?${new URLSearchParams({ state: state.alertState, page: state.alertPage })}`);
  if (request !== alertRequest || !state.csrf) return;
  $("#alert-list").replaceChildren(...result.items.map(item => {
    const element = node("article", "alert-item");
    const content = node("div", "alert-content");
    content.append(node("h3", "", alertNames[item.kind] || item.kind), node("p", "", item.detail));
    content.append(node("div", "alert-meta", `${item.label ? `客户：${item.label} · 尾号 ${item.code_suffix} · ` : ""}${item.subject ? `网络：${item.subject} · ` : ""}最近触发 ${timeLabel(item.last_at)} · 命中 ${item.occurrences} 次`));
    if (item.acknowledged_at) content.append(node("div", "alert-meta", `已核实于 ${timeLabel(item.acknowledged_at)}`));
    const actions = node("div", "alert-actions");
    if (!item.acknowledged_at) actions.append(button("标记已核实", async () => {
      await api(`alerts/${item.id}/acknowledge`, { method: "POST", data: {} });
      await Promise.all([loadAlerts(), summary()]); toast("已标记核实；再次触发会重新提示");
    }, "button secondary"));
    if (item.code_id) actions.append(button("查看用码记录", () => viewEvents(item.code_id, item.label)));
    element.append(node("span", "alert-symbol", item.acknowledged_at ? "✓" : "◇"), content, actions); return element;
  }));
  $("#alerts-empty").hidden = result.items.length > 0;
  pagination("alerts", result); updated();
}
function selectExpiry(preset) {
  state.expiryPreset = preset;
  const durations = { "1h": 1, "4h": 4, "12h": 12, "24h": 24, "7d": 168 };
  const input = $('[name="expires_at"]');
  if (preset === "forever") input.value = "";
  else if (durations[preset]) input.value = new Date(Date.now() + (durations[preset]+8)*3600000).toISOString().slice(0,16);
  for (const control of document.querySelectorAll("[data-expiry]")) {
    const selected = control.dataset.expiry === preset;
    control.classList.toggle("selected", selected); control.setAttribute("aria-pressed", String(selected));
  }
  $("#expiry-hint").textContent = durations[preset] ? "从点击生成时开始计算有效期；也可手动调整下方日期。" : preset === "forever" ? "长期有效；也可手动指定失效时间。" : "按下方指定的北京时间失效。";
}
function openCreate() { $("#create-error").textContent = ""; selectExpiry(state.expiryPreset); $("#create-dialog").showModal(); $("#code-label").focus(); }
for (const control of document.querySelectorAll("[data-expiry]")) control.addEventListener("click", () => selectExpiry(control.dataset.expiry));
$('[name="expires_at"]').addEventListener("input", () => selectExpiry($('[name="expires_at"]').value ? "custom" : "forever"));
$("#code-sort").addEventListener("change", () => { state.codeSort = $("#code-sort").value; state.codePage = 1; run(loadCodes); });
$("#new-code").addEventListener("click", openCreate);
$("#empty-create").addEventListener("click", openCreate);
for (const control of document.querySelectorAll("[data-close]")) control.addEventListener("click", () => $(`#${control.dataset.close}`).close());
$("#generated-dialog").addEventListener("close", () => { state.generated = []; $("#generated-list").replaceChildren(); });
for (const control of document.querySelectorAll("[data-tab]")) control.addEventListener("click", () => run(() => switchTab(control.dataset.tab)));
for (const control of document.querySelectorAll("[data-status]")) control.addEventListener("click", () => run(async () => {
  state.codeStatus = control.dataset.status; state.codePage = 1;
  for (const sibling of document.querySelectorAll("[data-status]")) sibling.classList.toggle("selected", sibling === control);
  await loadCodes();
}));
for (const control of document.querySelectorAll("[data-state]")) control.addEventListener("click", () => run(async () => {
  state.alertState = control.dataset.state; state.alertPage = 1;
  for (const sibling of document.querySelectorAll("[data-state]")) sibling.classList.toggle("selected", sibling === control);
  await loadAlerts();
}));
for (const [prefix, key, loader] of [["codes", "codePage", loadCodes], ["events", "eventPage", loadEvents], ["alerts", "alertPage", loadAlerts]]) {
  $(`#${prefix}-prev`).addEventListener("click", () => run(async () => { state[key] = Math.max(1, state[key]-1); await loader(); }));
  $(`#${prefix}-next`).addEventListener("click", () => run(async () => { state[key]++; await loader(); }));
}
$("#search-form").addEventListener("submit", event => { event.preventDefault(); state.search = $("#search").value.trim(); state.codePage = 1; run(loadCodes); });
$("#date-form").addEventListener("submit", event => { event.preventDefault(); run(loadOverview); });
$("#today").addEventListener("click", () => { dateRange(1); run(loadOverview); });
$("#week").addEventListener("click", () => { dateRange(7); run(loadOverview); });
$("#event-kind").addEventListener("change", () => { state.eventKind = $("#event-kind").value; state.eventPage = 1; run(loadEvents); });
$("#clear-event-filter").addEventListener("click", () => { state.eventCode = ""; state.eventLabel = ""; state.eventKind = ""; state.eventPage = 1; $("#event-kind").value = ""; run(loadEvents); });
$("#refresh-events").addEventListener("click", () => run(loadEvents));
$("#refresh-alerts").addEventListener("click", () => run(async () => { await Promise.all([loadAlerts(), summary()]); }));
$("#refresh-all").addEventListener("click", () => run(async () => { await Promise.all([switchTab(state.tab), summary()]); }));
$("#copy-batch").addEventListener("click", () => run(() => copy(state.generated.map(code => `${code.label}\t${code.code}`).join("\n"))));
$("#create-form").addEventListener("submit", async event => {
  event.preventDefault(); if ($("#create-submit").disabled) return;
  $("#create-error").textContent = ""; $("#create-submit").disabled = true;
  const values = new FormData(event.target);
  try {
    const rawExpiry = values.get("expires_at");
    const result = await api("codes", { method: "POST", data: { label: values.get("label"), quantity: Number(values.get("quantity")), max_uses: Number(values.get("max_uses")), note: values.get("note"), expiry_preset: state.expiryPreset, expires_at: rawExpiry ? new Date(`${rawExpiry}+08:00`).toISOString() : null } });
    state.generated = result.items;
    $("#generated-list").replaceChildren(...result.items.map(code => { const item = node("div", "generated-item"); const text = node("div"); text.append(node("strong", "", code.label), node("code", "", code.code)); item.append(text, button("复制", () => copy(code.code))); return item; }));
    $("#create-dialog").close(); $("#generated-dialog").showModal(); event.target.reset(); selectExpiry("forever");
    state.codePage = 1; await run(async () => { await Promise.all([loadCodes(), summary()]); });
  } catch (error) { $("#create-error").textContent = error.message; }
  finally { $("#create-submit").disabled = false; }
});
$("#confirm-form").addEventListener("submit", async event => {
  event.preventDefault(); if ($("#confirm-submit").disabled) return;
  const code = state.pendingCode; if (!code) return;
  $("#confirm-submit").disabled = true;
  try {
    await api(`codes/${code.id}`, { method: "PATCH", data: { enabled: !code.enabled } });
    $("#confirm-dialog").close(); toast(code.enabled ? "邀请码已停用" : "邀请码已恢复");
    await run(async () => { await Promise.all([loadCodes(), summary()]); });
  } catch (error) { $("#confirm-error").textContent = error.message; }
  finally { $("#confirm-submit").disabled = false; }
});
$("#login-form").addEventListener("submit", async event => {
  event.preventDefault(); if ($("#login-submit").disabled) return;
  $("#login-error").textContent = "";
  if (state.setup && $("#password").value !== $("#confirm-password").value) { $("#login-error").textContent = "两次输入的密码不一致"; return; }
  $("#login-submit").disabled = true;
  try {
    const result = await api(state.setup ? "setup" : "login", { method: "POST", data: { password: $("#password").value } });
    state.csrf = result.csrf; state.setup = false;
    $("#password").value = ""; $("#confirm-password").value = "";
    $("#login-screen").hidden = true; $("#workspace").hidden = false;
    await run(async () => { await Promise.all([switchTab(state.tab), summary()]); });
  } catch (error) { $("#login-error").textContent = error.message; }
  finally { $("#login-submit").disabled = false; }
});
$("#logout").addEventListener("click", () => run(async () => { await api("logout", { method: "POST", data: {} }); showLogin(); }));
async function boot() {
  dateRange(7);
  try {
    const result = await api("session"); state.setup = result.setup_required;
    const cloud = result.environment === "cloud";
    document.querySelectorAll(".local-dot").forEach(element => { element.textContent = cloud ? "云端" : "本地"; });
    $("#admin-access-note").textContent = `${cloud ? "通过 HTTPS 安全访问" : "仅本机可访问"} · 管理员密码与会议邀请码相互独立`;
    if (result.authenticated) { state.csrf = result.csrf; $("#login-screen").hidden = true; $("#workspace").hidden = false; await run(async () => { await Promise.all([loadCodes(), summary()]); }); }
    else showLogin();
  } catch { $("#login-error").textContent = "无法连接本地后台。请确认使用本地管理启动命令，然后刷新页面。"; }
}
boot();
