const meetingState = require("./services/meeting-state");
const environment = require("./config/environment");

App({
  globalData: {
    meetingState,
  },

  onLaunch() {
    if (environment.isCloudEnabled() && typeof wx.cloud?.init === "function") wx.cloud.init({ env: environment.CLOUD_ENV_ID });
    meetingState.reset();
  },
});
