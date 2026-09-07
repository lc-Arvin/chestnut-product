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
    recorder.stop();
    this.setData({ pendingSave: Boolean(meetingState.state.pendingPayload) });
    const pair = meetingState.state.languagePair;
    this.setData({ serverHost: environment.getServerHost(), languageIndices: pair.map(code => languages.codes.indexOf(code)), languagePairLabel: pair.map(code => languages.labels[code]).join(" ⇄ ") });
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
    return serverHost;
  },

  cancelInvite() { this.action = null; this.setData({ inviteVisible: false }); },
  async verified() {
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
      if (await access.authorized()) await this.verified();
      else this.setData({ inviteVisible: true });
    } catch (error) { wx.showToast({ title: "无法连接服务，请重试", icon: "none" }); }
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
