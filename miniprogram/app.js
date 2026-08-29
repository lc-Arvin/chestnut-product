const meetingState = require("./services/meeting-state");

App({
  globalData: {
    meetingState,
  },

  onLaunch() {
    meetingState.reset();
  },
});
