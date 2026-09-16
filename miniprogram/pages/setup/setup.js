const access = require("../../services/access");
const meetingStateApi = require("../../services/meeting-api");
const languages = require("../../utils/languages");
const meetingState = require("../../services/meeting-state");
const recorder = require("../../services/recorder");
const { safeTopPadding } = require("../../utils/layout");

Page({
  data: {
    inviteVisible: false,
    busy: false,
    pendingSave: false,
    invitationHint: "",
    languageRanges: [languages.codes.map(code => languages.labels[code]), languages.codes.map(code => languages.labels[code])],
    languageIndices: [0, 1],
    languagePairLabel: "中文（简体） ⇄ English",
    cloudStatus: "checking",
    cloudStatusLabel: "连接中",
    cloudHint: "正在连接云端服务…",
    safeTop: safeTopPadding(),
  },

  onShow() {
    access.recordVisit();
    this.refreshAccess();
    recorder.stop();
    this.setData({ pendingSave: Boolean(meetingState.state.pendingPayload) });
    const pair = meetingState.state.languagePair;
    this.setData({ languageIndices: pair.map(code => languages.codes.indexOf(code)), languagePairLabel: pair.map(code => languages.labels[code]).join(" ⇄ ") });
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
    this.setData({ invitationHint: "", cloudStatus: "checking", cloudStatusLabel: "连接中", cloudHint: "正在连接云端服务…" });
    try {
      const result = await access.status();
      if (attempt !== this.accessRefresh) return;
      this.updateInvitationHint(result);
      this.setData({ cloudStatus: "connected", cloudStatusLabel: "已连接", cloudHint: "云端服务已连接，可开始会议" });
    } catch (error) {
      if (attempt !== this.accessRefresh) return;
      this.setData({ cloudStatus: "unavailable", cloudStatusLabel: "重试", cloudHint: error.message || "暂时无法连接，请检查网络后重试" });
    }
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
    await this.runAuthorized("start");
  },
});
