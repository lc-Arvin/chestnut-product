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
    this.setData({ serverHost: environment.getServerHost() });
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

  startMeeting() {
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
    wx.navigateTo({ url: "/pages/audio-check/audio-check" });
  },
});
