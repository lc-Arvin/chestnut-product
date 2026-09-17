const meetingState = require("./services/meeting-state");
const environment = require("./config/environment");

App({
  globalData: {
    meetingState,
  },

  onLaunch(options = {}) {
    if (options.scene !== 1154 && typeof wx.cloud?.init === "function") wx.cloud.init(environment.cloudConfig());
    meetingState.reset();
  },
});
