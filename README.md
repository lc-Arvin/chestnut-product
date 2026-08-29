# ChestnutOne Conference Console

ChestnutOne 是一个面向国际会议工作人员的极简双语同传控制台原型。

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
- 断线重连与安全停止

实时识别与翻译由阿里云百炼 `qwen3.5-livetranslate-flash-realtime` 提供。

## macOS 快速启动

1. 在阿里云百炼创建 API Key，并获取业务空间的 API Host。
2. 复制 `.env.example` 为 `.env`，填写自己的 Key 和 API Host。
3. 双击 `StartChestnut.command`。
4. 浏览器会自动打开 `http://127.0.0.1:8080`。

`.env` 已被 Git 忽略，不会提交到仓库。若未填写 `.env`，启动器仍会在运行时询问 Key 和 API Host。

首次启动会自动创建 Python 虚拟环境并安装依赖。

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
index.html               页面结构
style.css                视觉设计与响应式布局
app.js                   会议流程、麦克风与字幕交互
server.py                本地静态服务与百炼 WebSocket 安全桥接
StartChestnut.command    macOS 启动器
requirements.txt         Python 依赖
```

## 安全说明

- 不要将 API Key 写入源码或提交到 Git。
- 真实配置只写入本地 `.env`，仓库仅保留 `.env.example`。
- 不要在浏览器前端直接暴露长期 API Key。
- 如果 Key 曾出现在聊天、截图或提交历史中，请立即撤销并重新生成。

## 当前范围

这是 Chestnut Conference Console Prototype。暂不包含用户账户、云端会议存档、说话人分离和生产环境部署。
