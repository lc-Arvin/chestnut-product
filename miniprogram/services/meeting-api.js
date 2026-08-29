const environment = require("../config/environment");

function saveMeeting(payload) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: environment.apiUrl("/api/meetings"),
      method: "POST",
      data: payload,
      header: { "content-type": "application/json" },
      timeout: 15000,
      success(response) {
        if (response.statusCode >= 200 && response.statusCode < 300) resolve(response.data);
        else reject(new Error(response.data?.error || `保存失败 (${response.statusCode})`));
      },
      fail(error) {
        reject(new Error(error.errMsg || "无法连接会议稿服务"));
      },
    });
  });
}

module.exports = { saveMeeting };
