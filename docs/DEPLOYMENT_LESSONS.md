# 云托管部署复盘与发布检查

记录日期：2026-09-16（北京时间）。本文是后续部署和排障的必读检查表；完整变量说明见 [CLOUD_DEPLOYMENT.md](CLOUD_DEPLOYMENT.md)。不要只凭构建成功、启动日志或一次 HTTP 200 宣布发布成功。

## 本次问题与明确结论

| 问题 | 已观察到的证据 | 后续规则 |
| --- | --- | --- |
| 本机配置没有进入云端 | 启动报缺少 `CHESTNUT_PUBLIC_ORIGIN`、MySQL 连接变量、首次管理员密码 | `.env` 不进入 Git 或镜像。发布前核对待发布版本的运行时配置；不能只核对本机文件 |
| MySQL 初始化错误过于笼统 | 旧日志只包含 `error_type=RuntimeError`，无法区分缺配置、建库与连接失败 | 保留安全错误分类、驱动错误码与启动阶段；禁止记录密码、连接串、token 或完整环境变量 |
| 初始化条件分批遗漏 | 数据库、管理员首次初始化分别阻止启动 | 一次核对数据库存在、权限、连接、签名密钥、Origin 和首次管理员密码。云库全新开始，不导入本地数据 |
| 新构建仍使用旧源码 | 036 的 `built_at_utc` 更新，但 `version`、`code_ref`、`source_sha256` 仍对应旧版本 | 构建时间不是代码版本。核对真实 Git SHA、标签和源码指纹。日志不足以证明具体是哪项平台配置导致旧源码重建，不作猜测 |
| 容器监听端口与探针不一致 | 036 探针连接 `:80` 被拒绝，进程记录监听 `:8080` | 修改默认端口前核对平台。云端 `PORT`、容器端口、Readiness、Liveness 必须一致；本项目云端默认 80、本地默认 8080 |
| 旧健康版本掩盖新版本失败 | 公网 `/health` 曾返回 200，但 `/admin` 404、前端仍为旧源码 | 同时验收所有关键路由的版本；新版本失败时旧版本可能继续提供流量，不应将旧服务可访问当成新发布成功 |
| 小程序开发版自动切本地 | Setup 出现 IP 输入框、`127.0.0.1`，与云端产品预期不符 | 小程序开发版、体验版、正式版全部使用云服务。旧 IP 缓存不参与路由；本地调试保留在 Web/Python |
| 实时握手把小程序按 Web 来源拦截 | 模拟器记录 cloud_connect 阶段 1006；其 SDK 实际域名在带 `Origin: https://servicewechat.com` 时返回 `403 Origin not allowed`，无 Origin 时返回 101 | `/ws?auth=message` 在云模式下只认首帧显式 token，不借用 Cookie/请求头，不套用 Web Cookie 的同源规则；普通 Web Socket 和管理接口继续严格校验来源。分别验证握手与认证，不把 1006 当作百炼错误 |

最终修复端口的提交是 `746d8165bf20ed6d5d8a053bd01a3c1392a83df2`，标签 `chestnut-20260915T180631645158Z`。该版本五个公网路由通过同版本验收，随后用户确认服务 ready。这只确认后端发布，不等于小程序录音及百炼实时翻译已通过验收。

## 发布前

1. 阅读当前代码与 `git status`，明确本次范围。对改动执行对应测试；小程序改动至少运行其协议与页面回归。检查 `.env`、数据库、日志没有被暂存。
2. 核对云端运行时配置：`CHESTNUT_ENV=cloud`、`PORT=80`、监听 `0.0.0.0`、MySQL、HTTPS Origin、稳定的签名密钥、百炼配置。首次建管理员时还需 12–200 字符的 `CHESTNUT_ADMIN_BOOTSTRAP_PASSWORD`。Origin 是纯 HTTPS 地址，不是 Markdown 链接，也不带 `/admin`。
3. MySQL 内网连接用于同网络的云服务，本机连接使用获准的外网地址及端口；连通性与权限分别检查。不得为了“先启动”把密码写入源码或绕过鉴权。
4. 核对容器端口和两类探针均为 80。镜像非 root 用户通过绑定低端口的 capability 监听，Dockerfile 的构建检查会以运行用户实际验证绑定能力。若改镜像用户、Python 路径或 capability，要重新验证，不能仅凭 `EXPOSE` 判断端口已监听。
5. 核对将要构建的分支/标签/提交。每次提交必须更新版本标记：先暂存本次文件，然后在根目录运行：

```powershell
.\.venv\Scripts\python.exe scripts/commit_version.py -m "本次改动说明"
git log -1 --format=fuller
Get-Content VERSION.json
```

需要发布且已获用户授权后，推送提交与对应附注标签，例如 `git push --follow-tags origin main`。不要普通 `git commit` 后沿用旧 VERSION.json；不要通过改密码、关闭校验、反复空推来替代诊断。

## 发布后必须逐层确认

1. **构建来源**：部署记录的 SHA 对应本次提交；启动日志的 version/code_ref/source_sha256 与本次一致。Git 提交时间精确到秒；stamp/build 时间可精确到微秒；不要混淆。
2. **进程初始化与监听**：`service_starting` 仅表示尝试启动。核对配置、数据库、管理员阶段结果和最终 `service_started` 的监听地址/端口。按部署版本及 boot_id 分组，以日志内 UTC 时间排序，避免混入重启中的旧容器。
3. **平台状态**：就绪与存活探针均通过，发布成功。`connection refused` 先查监听端口、绑定地址和进程是否存活；不要继续反复修改已经通过的数据库配置。
4. **公网实际版本**：运行只读验收脚本，全部路由返回预期版本、数据库健康、后台云模式才算通过：

```powershell
.\.venv\Scripts\python.exe scripts/verify_deployment.py --origin https://chestnut-api-305195-11-1477663536.sh.run.tcloudbase.com --attempts 12 --interval 20
```

脚本覆盖 `/health`、`/`、`/app.js`、`/admin`、`/api/admin/session`，不登录或修改业务数据。返回旧版本或混合版本时检查构建来源、发布失败记录与流量分配，不把增加等待次数当作修复。

5. **客户端链路**：Web 登录/试用/翻译/保存；小程序 SDK 环境关联、HTTP、WebSocket、真机录音分别验证。小程序代码上传与后端镜像部署是两次独立发布，服务端 ready 不会自动更新用户的小程序。

## 排障和交付记录

每次记录：提交 SHA、版本标签、云托管部署号、构建来源、关键启动阶段、端口/探针、验收命令及结果。仅记录非敏感配置；凭据保存在云端密钥配置中。

先复述已确认的失败层，再提出能区分原因的检查。无法读取控制台时明确说明，以用户提供的版本日志和公网结果为证据；不声称已检查未访问的页面。没有执行 Docker 构建、真实 MySQL、微信 SDK 或手机录音验证时，逐项说明范围，不用 mock 测试代替实际环境结论。
