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
      trial: Boolean(result.trial),
      duration: result.duration || "00:00:00",
      entryCount: result.entryCount || 0,
      filename: result.filename || "Not saved",
      message: result.saved
        ? "完整的原文和译文已经保存。"
        : `会议稿暂未保存，返回服务页可重试：${result.error || "服务暂不可用"}`,
    });
  },

  newMeeting() {
    // Preserve unsaved transcript and selected languages for retry.
    wx.reLaunch({ url: "/pages/setup/setup" });
  },
});
