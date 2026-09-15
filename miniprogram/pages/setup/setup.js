const access = require("../../services/access");
const meetingStateApi = require("../../services/meeting-api");
const languages = require("../../utils/languages");
const meetingState = require("../../services/meeting-state");
const environment = require("../../config/environment");
const recorder = require("../../services/recorder");
const { safeTopPadding } = require("../../utils/layout");

function isDevTools() {
  try {
    return wx.getDeviceInfo().platform === "devtools";
  } catch (error) {
    return wx.getSystemInfoSync().platform === "devtools";
  }
}

Page({
  data: {
    inviteVisible: false,
    busy: false,
    pendingSave: false,
    invitationHint: "",
    languageRanges: [languages.codes.map(code => languages.labels[code]), languages.codes.map(code => languages.labels[code])],
    languageIndices: [0, 1],
    languagePairLabel: "中文（简体） ⇄ English",
    cloudEnabled: environment.isCloudEnabled(),
    serverHost: environment.getServerHost(),
    serverHint: environment.isCloudEnabled()
      ? "微信云托管 · 安全连接"
      : isDevTools() ? "开发者工具可使用 127.0.0.1" : "真机请填写电脑的 Wi-Fi 地址",
    serverPlaceholder: isDevTools() ? "127.0.0.1" : "例如 192.168.1.20",
    isDevTools: isDevTools(),
    safeTop: safeTopPadding(),
  },

  onShow() {
    access.recordVisit();
    this.refreshAccess();
    recorder.stop();
    this.setData({ pendingSave: Boolean(meetingState.state.pendingPayload) });
    const pair = meetingState.state.languagePair;
    this.setData({ serverHost: environment.getServerHost(), languageIndices: pair.map(code => languages.codes.indexOf(code)), languagePairLabel: pair.map(code => languages.labels[code]).join(" ⇄ ") });
    if (meetingState.state.trialComplete) {
      meetingState.state.trialComplete = false;
      this.action = this.data.pendingSave ? "save" : "start";
      this.setData({ inviteVisible: true });
    }
  },

  onHide() { this.accessRefresh = (this.accessRefresh || 0) + 1; },
  onUnload() { this.onHide(); },
  async refreshAccess() {
    const attempt = this.accessRefresh = (this.accessRefresh || 0) + 1;
    this.setData({ invitationHint: "" });
    try {
      const result = await access.status?.();
      if (attempt === this.accessRefresh) this.updateInvitationHint(result);
    } catch { /* Starting a meeting performs its own access check. */ }
  },
  updateInvitationHint(result) {
    let invitationHint = "";
    if (result?.authenticated && result.access_mode === "invitation") {
      if (result.invitation_expires_at === null) invitationHint = "邀请码长期有效";
      else if (Number.isFinite(result.invitation_expires_at)) {
        const date = new Date((result.invitation_expires_at + 8 * 3600) * 1000);
        const pad = value => String(value).padStart(2, "0");
        invitationHint = `邀请码有效期至 ${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())} ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}（北京时间）`;
      }
    }
    this.setData({ invitationHint });
  },

  handleLanguageChange(event) {
    const indices = event.detail.value.map(Number);
    const pair = indices.map(index => languages.codes[index]);
    if (!languages.validPair(pair)) {
      wx.showToast({ title: "请选择两种不同语言", icon: "none" });
      this.setData({ languageIndices: [...this.data.languageIndices] });
      return;
    }
    meetingState.state.languagePair = pair;
    this.setData({ languageIndices: indices, languagePairLabel: pair.map(code => languages.labels[code]).join(" ⇄ ") });
  },

  handleHostInput(event) {
    this.setData({ serverHost: event.detail.value });
  },

  saveHost() {
    if (this.data.cloudEnabled) return environment.getServerHost();
    const serverHost = environment.setServerHost(this.data.serverHost);
    this.setData({ serverHost });
    this.refreshAccess();
    return serverHost;
  },

  cancelInvite() { this.action = null; this.setData({ inviteVisible: false }); },
  async verified() {
    this.updateInvitationHint(access.currentStatus?.());
    this.setData({ inviteVisible: false });
    const action = this.action;
    this.action = null;
    if (action === "save") await this.savePending();
    else if (action === "start") wx.navigateTo({ url: "/pages/audio-check/audio-check" });
  },
  async runAuthorized(action) {
    this.setData({ busy: true });
    this.action = action;
    try {
      const authorized = await access.authorized();
      const canSaveTrial = action === "save" && access.trialInfo?.()?.meeting_id === meetingState.state.pendingPayload?.meeting_id && Boolean(meetingState.state.pendingPayload?.meeting_id);
      if (authorized || canSaveTrial) await this.verified();
      else this.setData({ inviteVisible: true });
    } catch (error) { wx.showToast({ title: error.message || "无法连接服务，请重试", icon: "none" }); }
    finally { this.setData({ busy: false }); }
  },
  retrySave() { if (!this.data.busy) this.runAuthorized("save"); },
  async savePending() {
    if (this.saving || !meetingState.state.pendingPayload) return;
    this.saving = true;
    this.setData({ busy: true });
    try {
      await meetingStateApi.saveMeeting(meetingState.state.pendingPayload);
      meetingState.state.pendingPayload = null;
      this.setData({ pendingSave: false });
      wx.showToast({ title: "会议稿已保存" });
    } catch (error) {
      if (error.status === 401) { this.action = "save"; this.setData({ inviteVisible: true }); }
      else wx.showToast({ title: "保存失败，可再次重试", icon: "none" });
    } finally {
      this.saving = false;
      this.setData({ busy: false });
    }
  },

  async startMeeting() {
    if (this.data.busy) return;
    if (meetingState.state.pendingPayload) { wx.showToast({ title: "请先重试保存上一场会议稿", icon: "none" }); return; }
    const serverHost = this.saveHost();
    if (!this.data.cloudEnabled && !this.data.isDevTools && environment.isLoopbackHost(serverHost)) {
      wx.showModal({
        title: "请填写电脑地址",
        content: "手机中的 127.0.0.1 指向手机自身。请填写电脑在同一 Wi-Fi 下的局域网 IP，例如 192.168.1.20。",
        showCancel: false,
        confirmText: "我知道了",
      });
      return;
    }
    await this.runAuthorized("start");
  },
});
