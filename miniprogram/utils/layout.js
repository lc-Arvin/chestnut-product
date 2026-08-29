function safeTopPadding(extra = 6) {
  try {
    const capsule = wx.getMenuButtonBoundingClientRect?.();
    if (capsule?.bottom) return capsule.bottom + extra;
    const windowInfo = wx.getWindowInfo?.();
    if (windowInfo?.statusBarHeight) return windowInfo.statusBarHeight + 44 + extra;
  } catch (error) {
    // The simulator can omit capsule information while it is starting.
  }
  return 50 + extra;
}

module.exports = { safeTopPadding };
