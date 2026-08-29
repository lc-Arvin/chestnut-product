const recorder = require("../../services/recorder");
const { pcmLevel } = require("../../utils/audio");
const { safeTopPadding } = require("../../utils/layout");

Page({
  data: {
    countdown: "05",
    meterHeight: 28,
    passed: false,
    permissionDenied: false,
    safeTop: safeTopPadding(),
    eyebrow: "Checking Microphone",
    title: "Audio Check",
    message: "请允许麦克风权限，然后正常说话。",
  },

  onLoad() {
    this.remaining = 5;
    this.voiceDetected = false;
    this.leavingForLive = false;
    this.unsubscribeFrame = recorder.subscribe("frame", ({ frameBuffer }) => {
      const level = pcmLevel(frameBuffer);
      if (level > 0.07) this.voiceDetected = true;
      this.setData({ meterHeight: Math.round(24 + Math.max(0.04, level) * 96) });
    });
    this.unsubscribeState = recorder.subscribe("state", ({ state }) => this.handleRecorderState(state));
    this.unsubscribeError = recorder.subscribe("error", (error) => this.handleRecorderError(error));
    this.requestMicrophone();
  },

  onUnload() {
    clearInterval(this.countdownTimer);
    clearTimeout(this.recorderStartFallback);
    this.unsubscribeFrame?.();
    this.unsubscribeState?.();
    this.unsubscribeError?.();
    if (!this.leavingForLive) recorder.stop();
  },

  handleRecorderState(state) {
    if (state !== "recording") return;
    clearTimeout(this.recorderStartFallback);
    this.beginCountdown(false);
  },

  beginCountdown(simulated) {
    if (this.countdownTimer || this.data.passed || this.data.permissionDenied) return;
    this.setData({
      eyebrow: simulated ? "Simulator Check" : "Listening",
      message: simulated ? "开发者工具正在模拟检查；真实声音输入将在真机验证。" : "请正常说话，音量条会随声音变化。",
      permissionDenied: false,
    });
    this.countdownTimer = setInterval(() => {
      this.remaining -= 1;
      this.setData({ countdown: String(this.remaining).padStart(2, "0") });
      if (this.remaining <= 0) this.finishCheck();
    }, 1000);
  },

  startAuthorizedRecorder() {
    recorder.start();
    clearTimeout(this.recorderStartFallback);
    if (this.isDevTools()) {
      this.recorderStartFallback = setTimeout(() => this.beginCountdown(true), 1500);
    }
  },

  isDevTools() {
    try {
      return wx.getDeviceInfo().platform === "devtools";
    } catch (error) {
      return wx.getSystemInfoSync().platform === "devtools";
    }
  },

  requestMicrophone() {
    wx.getSetting({
      success: ({ authSetting }) => {
        if (authSetting["scope.record"] === false) {
          this.showPermissionDenied();
          return;
        }
        wx.authorize({
          scope: "scope.record",
          success: () => this.startAuthorizedRecorder(),
          fail: () => this.showPermissionDenied(),
        });
      },
      fail: () => this.startAuthorizedRecorder(),
    });
  },

  showPermissionDenied() {
    clearInterval(this.countdownTimer);
    clearTimeout(this.recorderStartFallback);
    this.countdownTimer = null;
    this.setData({
      permissionDenied: true,
      eyebrow: "Permission Needed",
      title: "需要麦克风权限",
      message: "请允许 ChestnutOne 使用麦克风，会议音频只会发送到你的本地服务。",
    });
  },

  handleRecorderError(error) {
    clearInterval(this.countdownTimer);
    this.countdownTimer = null;
    const denied = String(error?.errMsg || "").toLowerCase().includes("auth");
    this.setData({
      permissionDenied: true,
      eyebrow: "Permission Needed",
      title: "无法使用麦克风",
      message: denied ? "请在微信设置中允许 ChestnutOne 使用麦克风。" : "麦克风启动失败，请检查权限后重试。",
    });
  },

  finishCheck() {
    clearInterval(this.countdownTimer);
    this.countdownTimer = null;
    recorder.pause();
    this.setData({
      passed: true,
      eyebrow: "Ready for Live Meeting",
      title: this.voiceDetected ? "Audio Check Passed" : "Microphone Connected",
      message: this.voiceDetected ? "声音输入清晰，可以开始会议。" : "麦克风已连接，会议开始后请靠近设备发言。",
    });
  },

  openSettings() {
    wx.openSetting({
      success: ({ authSetting }) => {
        if (!authSetting["scope.record"]) return;
        this.setData({ permissionDenied: false, message: "正在重新连接麦克风…" });
        this.startAuthorizedRecorder();
      },
    });
  },

  goBack() {
    recorder.stop();
    wx.navigateBack();
  },

  continueMeeting() {
    this.leavingForLive = true;
    wx.redirectTo({ url: "/pages/live/live" });
  },
});
