# ChestnutOne

ChestnutOne 是一个面向国际会议工作人员的极简双语同传产品。仓库同时包含 Web 控制台和原生微信小程序 MVP，两端共用支持本地与微信云托管的 Python 百炼安全桥接服务。

## 客户端

- 仓库根目录：现有 Web Conference Console
- `miniprogram/`：微信原生小程序客户端

小程序的开发者工具导入、局域网调试和已知限制请阅读 [`miniprogram/README.md`](miniprogram/README.md)。开发进度见 [`docs/MINIPROGRAM_MVP_PLAN.md`](docs/MINIPROGRAM_MVP_PLAN.md)。

## 本地管理后台

启用邀请码保护后，新用户也可在邀请码弹窗选择 3 分钟免费试用。连接翻译成功才开始计时，暂停和重连不增加额度；结束后保存会议稿并引导输入邀请码。用户旅途、短期防重复边界和验证方式见 [`docs/TRIAL_EXPERIENCE.md`](docs/TRIAL_EXPERIENCE.md)。

在项目虚拟环境中执行 `python scripts/start_admin_local.py`，打开 `http://127.0.0.1:8080/admin`。Windows 可直接运行 `.\.venv\Scripts\python.exe scripts/start_admin_local.py`。首次打开时设置至少 12 个字符的独立管理员密码。

后台支持批量生成 6 位数字邀请码、明文查看和复制、标签/备注搜索、快捷有效期、生成/失效时间排序、临近过期高亮、成功验证次数上限和停用/恢复，以及每日访问、按码使用情况和异常提示。活动记录自动合并重复接入，访问按日汇总，历史默认保留 90 天。数据持久保存在 `data/admin.sqlite3`，已排除 Git 和 Docker 打包。具体操作、统计口径、验证方式和本地边界见 [`docs/LOCAL_ADMIN.md`](docs/LOCAL_ADMIN.md)。

使用本地管理启动器时将开启邀请码保护：即使没有生成码，也不会回退到免验证模式。上述本地后台仅允许直接从本机访问；云端 Web 后台的配置见下方部署说明。

当前版本包含完整的会议操作流程：

- Meeting Setup
- 真实麦克风 Audio Check
- 5 秒倒计时与输入音量反馈
- Live Meeting 计时器
- 紧凑 Logo 状态栏与沉浸式大屏字幕
- Pause / Resume 麦克风静音控制
- 可选两种会议语言，自动识别与双向字幕翻译
- 默认中文 ⇄ English，无需额外设置
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

完整部署与配置说明见 [`docs/CLOUD_DEPLOYMENT.md`](docs/CLOUD_DEPLOYMENT.md)，完整变量模板为 [`.env.example`](.env.example)，云端模板为 [`.env.cloud.example`](.env.cloud.example)。

本地继续使用 SQLite 和本地文件；云端使用全新的 MySQL 库，默认将 Markdown 会议稿一并存入 MySQL，也可选择私有 COS。不会同步或导入本地数据。管理后台通过 HTTPS Web 访问，小程序不提供管理入口；云端初始管理员密码由部署配置设置。

容器统一监听端口 8080，启动命令 `python server.py`，健康检查 `/health`。镜像默认云端模式，缺少必要配置会拒绝启动。第一版须配置单实例、单进程，试用计时和在线会议状态尚未迁移到共享存储。

小程序 `miniprogram/config/environment.js` 的 `TRANSPORT_MODE=auto` 在开发版使用本地/LAN，在体验版及正式版使用云托管。开发工具联调云端时设为 `cloud`；`CLOUD_ENV_ID`、`CLOUD_SERVICE` 须填写实际环境和服务名。

## Web 与小程序访问控制

用户可公开浏览服务页，开始会议时使用邀请码或短期试用。MySQL 模式始终启用保护，邀请码通过 Web 后台创建；`CHESTNUT_WEB_INVITE_CODES` 仅保留旧本地兼容用途，不导入云库。

Web 使用 HttpOnly Cookie，小程序使用签名 token，百炼密钥仅在服务端。邀请码到期或停用后撤销访问；会议稿按用户隔离。云端使用 `CHESTNUT_PUBLIC_ORIGIN` 检查 Web 来源，并仅信任指定网关的转发身份。单场时长、并发和验证限流的逐项配置见部署文档。

## 安全说明

- 不要将 API Key 写入源码或提交到 Git。
- 真实配置只写入本地 `.env`，仓库仅保留 `.env.example`。
- 不要在浏览器前端直接暴露长期 API Key。
- 如果 Key 曾出现在聊天、截图或提交历史中，请立即撤销并重新生成。

## 当前范围

这是 Chestnut Conference Console Prototype。暂不包含用户账户、云端会议存档、说话人分离和生产环境部署。

## 会议语言选择

Web 的 Meeting Setup 使用两个语言下拉框；微信小程序点击「会议语言」选择两种不同语言。
默认是中文 ⇄ English。当前提供中文（简体）、粤语、英语、日语、韩语、法语、德语、西班牙语、俄语、葡萄牙语、阿拉伯语，可任选两种互译（例如日语 ⇄ 英语）。

语言对在开始会议时固定，重连沿用本场设置；修改语言请结束会议后返回 Setup。Web 刷新页面、小程序重新启动或完成后开始新会议时默认中英文；同一 Setup 页面中明确选择的语言会保留。
字幕栏显示所选语言，蓝色标记原文，紫色标记译文；会议稿保存实际语言代码和对应语言名称。未选语言对中的原文不会被误放到其他语言栏。

服务端和两端共用 `shared/languages.json` 语言目录，Web 通过 `GET /api/languages` 读取。`shared/` 是服务端运行必需文件，必须随 Docker 镜像一同发布；不要将其加入 `.dockerignore`。更新目录后运行 `python scripts/sync_languages.py` 同步小程序使用的 JS 模块。WebSocket 使用 `languages=zh,en` 参数，未传或空值保持中英文兼容；重复、未知或非两种语言的组合在模型连接前拒绝。始终使用两路模型连接，不随可选语种数量增加。

语言能力参考：[百炼实时翻译文档](https://www.alibabacloud.com/help/en/model-studio/qwen3-5-livetranslate-flash-realtime)。此处开放的是产品首批语种，不代表模型完整语言列表。新增语种需核对模型翻译和原文转写能力，并进行真实语音验收。

验证：`python -m unittest discover -s tests` 和 `node --test tests/test_languages.cjs`。自动化测试不调用付费模型；仍需使用实际百炼凭证在浏览器和微信真机验证双向语音效果。

### 粤语与简体中文

两端均可选择「粤语」和「中文（简体）」进行双向字幕翻译，也可选择粤语与英语等语言。默认仍为中英文。
粤语使用独立代码 `yue`，原文不再归入 `zh`；粤语原文与中文译文分别显示和保存。中文译文由模型生成后用 OpenCC 统一为简体字（实时字幕与会议稿一致），粤语原文保留模型原貌。字形转换不负责将粤语词汇改写为普通话，语义翻译仍由百炼模型完成。

粤语/中文组合关闭同语言文本跳过，避免中文目标端不返回文本。会议仍自动识别语言，支持双向发言，暂不新增单向模式或语音播报。参考百炼 [语言代码与能力](https://www.alibabacloud.com/help/en/model-studio/qwen3-5-livetranslate-flash-realtime) 和 [会话参数](https://www.alibabacloud.com/help/en/model-studio/live-translator-client-events)。

部署须重新安装 requirements.txt 中新增的 OpenCC 依赖（云托管重新构建镜像即可）。真实粤语识别、语义翻译效果及同语言返回行为需要真机验收，自动化测试不调用付费模型。

## 公开服务页与统一邀请码入口

- 两端首页公开展示功能、流程和语言选择。只在点击「开始会议」或「重试保存」需要授权时弹出邀请码窗口；取消后留在服务页，保留语言设置。
- Web 使用 HttpOnly Cookie；小程序使用 `/api/auth/invite` 返回的签名凭证，通过请求头访问 HTTP API。微信凭证绑定云托管注入的 OpenID，沿用 `wechat-{OpenID}` 会议稿归属。
- 小程序 WebSocket 使用 `auth=message`，打开后第一条消息发送 `auth.authenticate` 和凭证。服务端在 10 秒内验证首条消息，通过前不连接模型或转发音频。凭证不放进 URL。旧的无凭证小程序连接会被拒绝。
- 有效期内无需重复输入。到达会议时长上限后先尝试保存，再清除客户端凭证，下次点击开始要求验证。普通结束保留凭证。
- 保存失败保留原始会议稿供重试：Web 服务页有 Retry save，小程序结果页返回服务页后可点击重试。验证过期可重新输入邀请码后保存；保存成功前不会开启新会议覆盖旧内容。未保存稿目前保留在当前页面/小程序进程内存中，请勿刷新网页或彻底关闭小程序。

部署：
1. 沿用现有 `CHESTNUT_WEB_INVITE_CODES`、`CHESTNUT_AUTH_SECRET` 和有效期配置；不需要新增密钥。保持签名密钥稳定以兼容已有 Web 登录。
2. 从本次版本重新构建并部署服务端（Web 随服务端更新）。
3. 微信开发者工具上传同一版本的 `miniprogram/`，设置为体验版，提醒体验用户重新打开。服务端启用后旧小程序将无法直接开始会议，应安排同步更新。
4. 用 Web 无痕窗口和微信体验成员分别验证公开浏览、取消、错误码、成功继续、重连与到期保存。微信体验成员权限和产品邀请码为两层独立权限。

回归命令：`python -m unittest discover -s tests`；`node --test tests/test_access.cjs tests/test_languages.cjs tests/test_environment.cjs`。不调用付费模型。尚需微信真机验收云托管通道和麦克风权限弹窗。
