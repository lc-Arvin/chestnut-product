# ChestnutOne 微信小程序 MVP

这是 ChestnutOne 的原生微信小程序客户端。它与仓库根目录的 Web 控制台共用 `server.py`，小程序代码中不包含百炼 API Key。

本地与微信云托管的完整配置见 [云端部署说明](../docs/CLOUD_DEPLOYMENT.md)。`config/environment.js` 中 `TRANSPORT_MODE=auto` 会让开发版走本地/LAN、体验版和正式版走云端；开发工具调试云服务时设为 `cloud`，并核对 `CLOUD_ENV_ID` 与 `CLOUD_SERVICE`。

## 云端适配与验收

小程序继续通过 `callContainer` 请求共用服务，通过 `connectContainer` 建立实时连接。HTTP 使用指定环境及 `X-WX-SERVICE` 路由；WebSocket 使用 `app.js` 初始化的同一云环境，并在连接打开后发送认证首帧。MySQL 仅由 Python 服务访问，小程序无需配置数据库账号。[官方 HTTP 接入](https://docs.cloudbase.net/run/develop/access/mini)、[官方 WebSocket 接入](https://docs.cloudbase.net/run/develop/access/websocket)。

- 凭证按云环境+服务名、本地完整地址+端口分别保存；试用缓存也按该范围隔离。旧接口的迟到响应不会清除新登录或覆盖新状态。此次升级更换了缓存键，已有用户需重新验证一次邀请码。
- 云端和本地 HTTP 请求均设置 15 秒超时；保存失败保留当前会议稿供重试，不自动重发保存请求。网关非 JSON 错误和微信 SDK 超时会显示可读提示。
- Setup 页面展示已验证邀请码的到期时间（北京时间）或长期有效；云服务标记“已配置”仅说明已选择云路由，实际连通性在接口请求中检查。
- 保存接口返回的 `mysql` / `cos` 在结果页统一显示“已保存到云端”，`local` 显示“已保存到本地服务”。不向用户暴露数据库连接信息，管理后台仍只通过 Web 提供。

在仓库根目录运行协议与页面逻辑测试：

```powershell
node --test tests/test_access.cjs tests/test_languages.cjs tests/test_environment.cjs tests/test_mini_cloud.cjs
```

正式验收须使用已关联云环境的 AppID，在开发工具中设 `TRANSPORT_MODE=cloud`，再用体验版真机检查：邀请码/试用、双向翻译、断网重连、邀请码停用后停止收音、会议稿保存与失败重试。测试完若恢复 `auto`，开发版会重新连接本地服务，体验版与正式版继续连接云端。Node 测试使用模拟微信 API，不等于已通过真机 SDK、录音和网络验收。

## 当前能力

- Meeting Setup 与本地服务地址配置
- 麦克风授权、5 秒 Audio Check 和音量反馈
- 16 kHz、单声道 PCM 分帧录音
- 中英文自动识别与双向字幕翻译
- 蓝色原文、紫色译文
- Pause / Resume / Stop
- 10 分钟录音上限前自动续录
- 中断和断线状态提示
- Stop 后由共用服务保存 Markdown 会议稿：本地文件、MySQL 或可选 COS

## 在微信开发者工具中运行

### 1. 启动 Chestnut 本地服务

在仓库根目录复制 `.env.example` 为 `.env`，填入自己的百炼配置，然后双击 `StartChestnut.command`。

### 2. 导入小程序

1. 打开微信开发者工具。
2. 选择「导入项目」。
3. 项目目录选择本仓库的 `miniprogram/`。
4. 核对 `project.config.json` 中的 AppID。云托管调用须使用已与目标环境关联的真实 AppID。
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
