# ChestnutOne 微信小程序

原生微信客户端与 Web 共用云端 Python 服务。开发版、体验版和正式版全部默认连接微信云托管，不提供 IP 输入或本地连接入口。Web/Python 仍可本地调试。

## 运行与配置

在微信开发者工具中导入本仓库 `miniprogram/`，核对真实 AppID 与目标云环境已关联、具有服务调用权限，然后重新编译。无需先启动本地 Python，也无需填写 IP 或关闭域名校验。配置与部署过程见 [云端部署说明](../docs/CLOUD_DEPLOYMENT.md) 和 [部署复盘与发布检查](../docs/DEPLOYMENT_LESSONS.md)。

客户端仅在 `config/environment.js` 保存公开的路由标识：

| 配置 | 当前值 |
| --- | --- |
| `CLOUD_ENV_ID` | `chestnut-prod-d6ggcq8yzf8d2e322` |
| `CLOUD_SERVICE` | `chestnut-api` |

应用启动时 `wx.cloud.init` 选择环境；HTTP 使用 `wx.cloud.callContainer` 的环境配置及 `X-WX-SERVICE`；实时翻译通过 `wx.cloud.connectContainer` 显式传入同一 `config.env` 与服务名，连接打开后发送认证首帧。无本地 HTTP/WS 回退。MySQL、百炼、COS 和管理员凭据只配置在服务端。[官方 HTTP 接入](https://docs.cloudbase.net/run/develop/access/mini)、[官方 WebSocket 接入](https://docs.cloudbase.net/run/develop/access/websocket)。

旧 IP 缓存不会被读取，旧本地凭据不会用于云请求；现有云凭据继续按环境与服务隔离保存。开发版升级后可能需要重新验证云端邀请码。

## 页面与用户流程

- Setup 选择会议语言，自动检查云服务：请求中显示“连接中”，接口成功后显示“已连接”，失败显示原因与“重试”。这检查的是业务 HTTP 接口，实时翻译连接在开始会议后建立。
- 点击 Start Meeting 后，未授权用户输入邀请码或选择免费试用；取消后保持语言选择。已验证邀请码显示北京时间到期时间或长期有效。
- 经过麦克风检查进入双向实时字幕；支持暂停、恢复、停止及断网重连，试用重连不重置额度。
- 停止会议后保存会议稿到云端。失败保留当前进程内的稿件并提供重试，保存完成前不能开启新会议覆盖内容。
- 管理员通过 Web `/admin` 操作，不在小程序提供管理页。

## 验证与发布

在仓库根目录运行：

```powershell
node --test tests/test_access.cjs tests/test_languages.cjs tests/test_environment.cjs tests/test_mini_cloud.cjs tests/test_mini_socket.cjs
```

测试检查云端路由、缓存隔离、SDK 缺失、错误响应、状态重试、邀请码/试用、认证首帧和保存流程，使用模拟微信 API。真实验收还需：

1. 开发工具重新编译后 Setup 无 IP 输入，云服务显示“已连接”；网络记录使用云托管通道。
2. 核对 AppID 和环境关联；邀请码/试用成功，实时翻译双向有字幕，停止后稿件保存成功。
3. 体验版在 iPhone/Android 检查麦克风授权、音频帧、断网重连、中断恢复、试用结束与邀请码失效后停止收音。开发工具麦克风表现不能替代真机。
4. 上传小程序体验/正式版本与后端部署独立进行。推送 Git 或后端 ready 不代表小程序已更新。

若仍出现“本地服务地址”，核对打开的项目路径及代码版本，重新编译当前项目；不要靠修改 IP 或清空所有用户缓存解决。云服务失败应检查错误、环境关联、权限和后端状态，不切回本地。微信 SDK 不支持云接口时会提示升级。

### 本次验证记录（2026-09-16）

首次改为云连接时，原有 42 项 Node 测试全部通过。微信开发工具官方 CLI 的 `compile_wxml`、`compile_wxss` 均返回成功；`simulator_refresh` 已触发重新编译。运行信息及截图接口返回 `timeout waiting for automator response`，因此未确认模拟器视觉结果或真实 SDK 云调用。控制台查询没有匹配到 error 行，不能据此宣称运行正常。真机录音、翻译和保存仍待设备验收。

随后排查反复断线时，补充了 7 项实时连接测试，共 49 项全部通过。实时连接显式指定环境，20 秒连接超时，暂时性错误最多自动重试 5 次；HTTP 401/403/404、策略关闭和服务限流会停止自动重连，显示可处理的提示。手动“重新连接”重新开始尝试。服务返回的翻译错误不会立即被通用断线文案覆盖。官方 `automation_wx_api` 诊断仍因自动化通道超时无法取得 SDK 结果，本次实际断线根因尚待重新复现后的客户端错误码确认。

开发工具 Console 可筛选 `[Chestnut realtime]` 获取失败阶段（cloud_connect / authenticating / translation_setup / streaming）、错误码与尝试次数。日志不输出认证凭据、SDK 签名 URL 或音频。排障应保留当前页面错误与该日志，不能只提供“连接中断”。公网无凭据握手诊断已确认 `/ws` 能升级为 101 并拒绝无效认证；这不等于微信通道或百炼会话通过验证。

用户再次复现 1006 后，已读取到 cloud_connect 阶段的失败，且对 SDK 实际域名的对照请求复现 `Origin: https://servicewechat.com` 被服务端返回 `403 Origin not allowed`。服务端现在将云端 `auth=message` 的显式 token 认证与 Web Cookie 的 Origin 校验分开，空/无效首帧不能借用 Cookie 或 Authorization 通过认证。相关 21 项服务端测试通过；需部署此后端修复，小程序重新编译本身无法更新后端规则。最终 SDK 连接和音频结果仍需在新后端上确认。
