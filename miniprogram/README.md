# ChestnutOne 微信小程序 MVP

这是 ChestnutOne 的原生微信小程序客户端。它与仓库根目录的 Web 控制台共用 `server.py`，小程序代码中不包含百炼 API Key。

## 当前能力

- Meeting Setup 与本地服务地址配置
- 麦克风授权、5 秒 Audio Check 和音量反馈
- 16 kHz、单声道 PCM 分帧录音
- 中英文自动识别与双向字幕翻译
- 蓝色原文、紫色译文
- Pause / Resume / Stop
- 10 分钟录音上限前自动续录
- 中断和断线状态提示
- Stop 后由电脑端保存统一 Markdown 会议稿

## 在微信开发者工具中运行

### 1. 启动 Chestnut 本地服务

在仓库根目录复制 `.env.example` 为 `.env`，填入自己的百炼配置，然后双击 `StartChestnut.command`。

### 2. 导入小程序

1. 打开微信开发者工具。
2. 选择「导入项目」。
3. 项目目录选择本仓库的 `miniprogram/`。
4. 当前 `project.config.json` 使用 `touristappid`，可以先以测试号运行模拟器。
5. 在「详情 → 本地设置」中确认开发阶段不校验合法域名、TLS 版本及 HTTPS 证书。
6. 编译后，Setup 页面本地服务地址保持 `127.0.0.1`。

> 模拟器只能验证页面、流程和与电脑本地服务的连接。麦克风帧格式和中断恢复必须在真机再次测试。

## 局域网真机开发

微信真机预览必须先拥有一个真实小程序 AppID。获得 AppID 后：

1. 把 `miniprogram/project.config.json` 中的 `touristappid` 替换为真实 AppID。
2. 在根目录 `.env` 中设置 `CHESTNUT_HOST="0.0.0.0"`。
3. 确保手机与电脑连接同一个可信 Wi-Fi。
4. 查询电脑局域网 IP，例如 macOS Wi-Fi 通常可执行 `ipconfig getifaddr en0`。
5. 在小程序 Setup 页面把服务地址改成该局域网 IP，例如 `192.168.1.20`。
6. 重新启动 Chestnut，再通过开发者工具预览或真机调试。

局域网 HTTP/WS 仅用于开发。正式版本通过微信云托管的 `callContainer` 与 `connectContainer` 访问后端，无需把百炼 Key 或服务器域名写入小程序。

## 配置文件

`config/environment.js` 保存本地开发端口以及云托管环境配置：

```text
HTTP + WebSocket  8080
CLOUD_ENV_ID      留空时使用本地服务
CLOUD_SERVICE     chestnut-api
```

Setup 页面填写的服务器地址保存在微信本地存储中。这里只有主机地址，API Key 始终位于电脑端 `.env`。

## 已知 MVP 边界

- 没有真实 AppID 时，无法完成扫码真机预览。
- 没有正式域名时，无法提交微信审核或发布。
- 本地局域网服务没有生产级用户鉴权，只能用于可信网络开发。
- 微信开发者工具的麦克风行为不能替代 iPhone 和 Android 真机验收。
