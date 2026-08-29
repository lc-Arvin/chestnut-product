# ChestnutOne 微信小程序 MVP 开发计划

## 目标

在同一个仓库中新增原生微信小程序客户端，复用现有 Python 百炼桥接服务，完成可在微信开发者工具运行、具备真机联调条件的中英双向同传 MVP。

## 仓库策略

- 仓库根目录暂时保留现有 Web 控制台，避免改变 `StartChestnut.command` 和用户现有启动方式。
- `miniprogram/` 独立承载微信小程序。
- `server.py` 和 `meetings/` 由两个客户端共用。
- 小程序稳定后，再用独立提交将 Web 静态文件平移到 `webapp/`。

## 里程碑

### M1 · 工程与音频技术验证

- [x] 原生小程序工程骨架
- [x] `touristappid` 开发者工具配置
- [x] 16 kHz 单声道 PCM 分帧录音
- [x] Audio Check 音量检测
- [x] 10 分钟自动续录机制
- [ ] iPhone PCM 真机验证（等待 AppID）
- [ ] Android PCM 真机验证（等待 AppID）

### M2 · 完整会议流程

- [x] Meeting Setup
- [x] Audio Check
- [x] Live Caption
- [x] Meeting Result
- [x] Pause / Resume / Stop
- [x] 沉浸式双语字幕布局

### M3 · 本地实时翻译

- [x] 小程序 WebSocket 客户端
- [x] 二进制 PCM 音频发送
- [x] 百炼事件解析
- [x] 原文与译文颜色区分
- [x] 本地会议稿保存请求
- [x] Python 服务局域网监听选项
- [ ] 微信开发者工具人工联调

### M4 · 真机与稳定性

- [ ] 麦克风授权拒绝与恢复
- [ ] 来电/语音导致的系统中断
- [ ] Wi-Fi 短暂断开与手动重连
- [ ] 连续运行 15 分钟
- [ ] 最后一句字幕完整保存
- [ ] iOS/Android UI 验收

### M5 · 正式网络准备（不属于本地 MVP）

- [ ] 申请正式小程序 AppID
- [ ] 准备已备案域名
- [ ] 部署公网 Python WebSocket 服务
- [ ] 配置 HTTPS/WSS 和合法域名
- [ ] 加入微信静默登录与服务端鉴权
- [ ] 准备隐私说明并提交审核

## MVP 验收标准

1. 微信开发者工具能够无编译错误打开四个页面。
2. 本地服务启动后，小程序能够连接 `ws://127.0.0.1:8765`。
3. 麦克风 PCM 帧只发送给本地 Python 服务。
4. 中文发言显示中文原文和英文译文。
5. 英文发言显示英文原文和中文译文。
6. Pause 后停止发送音频，Resume 后继续同一会议。
7. Stop 后保存 Markdown 会议稿并显示结果。
8. 小程序目录中不存在百炼 API Key 或微信 AppSecret。

## 当前外部依赖

- 真机测试：需要微信小程序 AppID。
- 正式发布：需要备案域名、HTTPS/WSS 和云服务器。
- iOS/Android 验收：需要各一台测试设备。
