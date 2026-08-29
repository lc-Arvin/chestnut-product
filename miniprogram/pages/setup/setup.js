const environment = require("../../config/environment");
const recorder = require("../../services/recorder");
const { safeTopPadding } = require("../../utils/layout");

Page({
  data: {
    serverHost: environment.getServerHost(),
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
    this.setData({ serverHost: environment.setServerHost(this.data.serverHost) });
  },

  startMeeting() {
    this.saveHost();
    wx.navigateTo({ url: "/pages/audio-check/audio-check" });
  },
});
