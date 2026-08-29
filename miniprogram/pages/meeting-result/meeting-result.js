const meetingState = require("../../services/meeting-state");
const { safeTopPadding } = require("../../utils/layout");

Page({
  data: {
    saved: false,
    duration: "00:00:00",
    entryCount: 0,
    filename: "Not saved",
    message: "会议已经结束。",
    safeTop: safeTopPadding(),
  },

  onLoad() {
    const result = meetingState.state.lastResult || {};
    this.setData({
      saved: Boolean(result.saved),
      duration: result.duration || "00:00:00",
      entryCount: result.entryCount || 0,
      filename: result.filename || "Not saved",
      message: result.saved
        ? "完整的原文和译文已经保存到电脑。"
        : `会议已结束，但会议稿未能写入电脑：${result.error || "本地服务不可用"}`,
    });
  },

  newMeeting() {
    meetingState.reset();
    wx.reLaunch({ url: "/pages/setup/setup" });
  },
});
