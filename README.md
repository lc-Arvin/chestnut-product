# ChestnutOne

ChestnutOne 是一个面向国际会议工作人员的极简双语同传产品。仓库同时包含 Web 控制台和原生微信小程序 MVP，两端共用本地 Python 百炼安全桥接服务。

## 客户端

- 仓库根目录：现有 Web Conference Console
- `miniprogram/`：微信原生小程序客户端

小程序的开发者工具导入、局域网调试和已知限制请阅读 [`miniprogram/README.md`](miniprogram/README.md)。开发进度见 [`docs/MINIPROGRAM_MVP_PLAN.md`](docs/MINIPROGRAM_MVP_PLAN.md)。

当前版本包含完整的会议操作流程：

- Meeting Setup
- 真实麦克风 Audio Check
- 5 秒倒计时与输入音量反馈
- Live Meeting 计时器
- 紧凑 Logo 状态栏与沉浸式大屏字幕
- Pause / Resume 麦克风静音控制
- 英文原文实时字幕
- 中文实时翻译字幕
- 中英文双向自动识别与互译
- 蓝色原文、紫色译文角色提示
- 停止会议时自动保存完整双语会议稿
- 模型连接异常后的自动重连与安全停止
- 本地轮转诊断日志、音频发送超时与无响应看门狗

实时识别与翻译由阿里云百炼 `qwen3.5-livetranslate-flash-realtime` 提供。

## macOS 快速启动

1. 在阿里云百炼创建 API Key，并获取业务空间的 API Host。
2. 复制 `.env.example` 为 `.env`，填写自己的 Key 和 API Host。
3. 双击 `StartChestnut.command`。
4. 浏览器会自动打开 `http://127.0.0.1:8080`。

`.env` 已被 Git 忽略，不会提交到仓库。若未填写 `.env`，启动器仍会在运行时询问 Key 和 API Host。

首次启动会自动创建 Python 虚拟环境并安装依赖。

## 诊断日志

本地服务默认把运行日志同时输出到终端和以下文件：

```text
logs/chestnut.log
```

日志按 5 MB 轮转，最多保留 5 份历史文件。日志只包含会话编号、连接目标、事件数量、音频字节数、Request ID、超时和异常类型，不记录语音、字幕正文或 API Key。

可通过 `.env` 调整：

```text
CHESTNUT_LOG_FILE="/path/to/chestnut.log"
CHESTNUT_CLOUD_SEND_TIMEOUT="10"
CHESTNUT_CLOUD_RESPONSE_TIMEOUT="60"
```

当浏览器持续发送语音但百炼长时间没有返回任何事件时，服务端会主动结束异常连接；Web 和微信小程序客户端会使用指数退避自动重连。

## 会议稿

点击 `Stop Meeting` 后，系统会自动把本场会议的原文和译文保存为一个 Markdown 文件：

```text
meetings/meeting-YYYYMMDD-HHMMSS.md
```

会议稿包含开始时间、结束时间、会议时长、字幕时间戳、语言以及原文/译文标记。`meetings/` 已被 Git 忽略，会议内容不会上传到代码仓库。

## 手动启动

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export DASHSCOPE_API_KEY="your-api-key"
export BAILIAN_API_HOST="your-workspace.cn-beijing.maas.aliyuncs.com"
.venv/bin/python server.py
```

然后访问 `http://127.0.0.1:8080`。

## 项目结构

```text
index.html                    Web 页面结构
style.css                     Web 视觉设计与响应式布局
app.js                        Web 会议流程、麦克风与字幕交互
miniprogram/                  微信原生小程序 MVP
server.py                     两端共用的本地 HTTP/WebSocket 服务
StartChestnut.command         macOS 启动器
requirements.txt              Python 依赖
docs/MINIPROGRAM_MVP_PLAN.md  小程序开发计划与验收标准
```

## 小程序本地服务

微信开发者工具默认连接 `127.0.0.1`。局域网真机联调时，在 `.env` 中增加：

```text
CHESTNUT_HOST="0.0.0.0"
```

然后在小程序 Meeting Setup 页面填写电脑的局域网 IP。该模式只用于开发；正式发布必须使用 HTTPS/WSS 和微信后台合法域名。

## 微信云托管

仓库根目录包含云托管使用的 `Dockerfile`。容器将 HTTP、WebSocket 统一监听在端口 80：

```text
GET  /health        健康检查
GET  /ws            实时音频与字幕 WebSocket
POST /api/meetings  保存会议稿
```

在 `miniprogram/config/environment.js` 填写 `CLOUD_ENV_ID` 后，小程序会自动改用 `wx.cloud.connectContainer` 和 `wx.cloud.callContainer`；保持为空则继续使用本地局域网服务。

云托管服务需要设置 `DASHSCOPE_API_KEY` 与 `BAILIAN_API_HOST` 环境变量。不要把真实密钥写进代码。

设置 `CHESTNUT_COS_BUCKET` 后，会议稿会写入对象存储的 `meetings/{OpenID}/` 路径。地域默认读取 `TENCENTCLOUD_REGION`，也可通过 `CHESTNUT_COS_REGION` 配置。凭证优先使用云托管临时凭证；容器环境未注入凭证时，使用仅授权当前 Bucket 的 `CHESTNUT_COS_SECRET_ID` 与 `CHESTNUT_COS_SECRET_KEY` 子账号凭证。不要使用主账号密钥。未设置存储桶时，本地开发仍写入仓库的 `meetings/` 目录。

如需独立验证 COS 子账号，执行：

```bash
.venv/bin/python scripts/test_cos_access.py
```

按提示输入 Bucket、地域和凭证。SecretKey 使用隐藏输入，不会写入文件或终端历史。脚本不会打印 SecretKey，只会在 `meetings/_diagnostics/` 写入一个可安全删除的小文件。

## Web 私测访问控制

公网 Web 服务建议先使用邀请码保护。在云托管服务中配置：

```text
CHESTNUT_WEB_INVITE_CODES="customer-a=code-for-customer-a,customer-b=code-for-customer-b"
CHESTNUT_AUTH_SECRET="至少32字符、随机生成并长期保持不变的签名密钥"
CHESTNUT_WEB_TOKEN_TTL_SECONDS="43200"
CHESTNUT_MAX_MEETING_SECONDS="3600"
CHESTNUT_MEETING_WARNING_SECONDS="300"
CHESTNUT_MAX_CONCURRENT_MEETINGS="20"
CHESTNUT_ALLOWED_ORIGINS="https://your-web-domain.example"
```

配置邀请码后，Web 用户必须先验证才能连接实时翻译或保存会议稿。服务通过 `HttpOnly` Cookie 保存有时效的签名凭证，凭证不会出现在 WebSocket URL 中，页面脚本也无法读取。API Key 和签名密钥都不会进入前端。不同浏览器会得到独立用户标识，会议稿按标识隔离。

`CHESTNUT_MAX_MEETING_SECONDS` 是单场会议的服务端时间上限，默认 `3600` 秒；设为 `0` 表示不限制。`CHESTNUT_MEETING_WARNING_SECONDS` 控制结束前多少秒显示倒计时提醒。邀请码推荐使用 `客户标签=真实邀请码` 格式，会议稿文件名会使用客户标签，例如 `web-2026-09-05-customer-a-143022.md`，不会泄露真实邀请码。服务达到 `CHESTNUT_MAX_CONCURRENT_MEETINGS` 配置的并发数量后会拒绝新会议。同一用户只能进行一场会议，同一场会议的网络重连会替换旧连接。

登录和 WebSocket 建连频率分别通过以下变量控制：

```text
CHESTNUT_LOGIN_RATE_LIMIT="5"
CHESTNUT_LOGIN_RATE_WINDOW_SECONDS="600"
CHESTNUT_CONNECTION_RATE_LIMIT="10"
CHESTNUT_CONNECTION_RATE_WINDOW_SECONDS="60"
```

当前并发登记和频率限制保存在单个服务实例内。私测阶段应将云托管最大实例数设为 `1`；正式横向扩容前，需要迁移到 Redis 等共享状态服务。

未配置 `CHESTNUT_WEB_INVITE_CODES` 时，服务保持原有本地开发模式，不显示邀请码页面。微信小程序继续通过云托管注入的 `x-wx-openid` 识别用户，不使用 Web 邀请码。

## 安全说明

- 不要将 API Key 写入源码或提交到 Git。
- 真实配置只写入本地 `.env`，仓库仅保留 `.env.example`。
- 不要在浏览器前端直接暴露长期 API Key。
- 如果 Key 曾出现在聊天、截图或提交历史中，请立即撤销并重新生成。

## 当前范围

这是 Chestnut Conference Console Prototype。暂不包含用户账户、云端会议存档、说话人分离和生产环境部署。
