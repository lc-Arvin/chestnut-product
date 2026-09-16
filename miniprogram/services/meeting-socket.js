const access = require("./access");
const meetingState = require("./meeting-state");
const environment = require("../config/environment");

// Keep SDK diagnostics useful without logging credentials, signed URLs or audio.
function connectionError(error, fallback) {
  const raw = String(error?.errMsg || error?.message || error?.reason || "");
  const status = Number(error?.statusCode) || Number(raw.match(/(?:response(?: code)?|status(?:Code)?|HTTP)\s*[:=]?\s*(\d{3})/i)?.[1]);
  const code = String(error?.errCode ?? error?.code ?? (status || "")).replace(/[^a-zA-Z0-9_-]/g, "").slice(0, 40);
  let message = fallback;
  let retryable = true;
  if ([401, 403].includes(status) || /permission|unauthoriz|forbidden|权限/i.test(raw)) {
    message = "云端实时连接被拒绝，请检查小程序调用权限及服务接入配置"; retryable = false;
  } else if (status === 404 || /invalid.*env|env.*(?:not exist|invalid)|service.*not (?:exist|found)/i.test(raw)) {
    message = "未找到云端实时服务，请检查云环境和服务配置"; retryable = false;
  } else if (/timeout|timed out/i.test(raw)) message = "云端实时连接超时";
  else if (/network|offline|网络/i.test(raw)) message = "网络不可用，无法连接云端实时服务";
  return { message: code ? `${message}（${code}）` : message, code, retryable };
}

class MeetingSocket {
  constructor() {
    this.languagePair = [...meetingState.state.languagePair];
    this.task = null;
    this.listeners = new Map();
    this.intentionalClose = false;
    this.generation = 0;
    this.reconnectTimer = null;
    this.reconnectAttempts = 0;
    this.meetingId = access.trialInfo?.()?.meeting_id || "";
    this.lastFailure = "";
    this.phase = "idle";
  }

  subscribe(event, listener) {
    if (!this.listeners.has(event)) this.listeners.set(event, new Set());
    this.listeners.get(event).add(listener);
    return () => this.listeners.get(event)?.delete(listener);
  }

  emit(event, payload) {
    this.listeners.get(event)?.forEach((listener) => listener(payload));
  }

  scheduleReconnect() {
    if (this.intentionalClose || this.reconnectTimer) return;
    if (this.reconnectAttempts >= 5) {
      this.failPermanently(`${this.lastFailure || "云端实时连接失败"}；已暂停自动重试，请点击重新连接`);
      return;
    }
    const delay = Math.min(1000 * (2 ** this.reconnectAttempts), 10000);
    this.reconnectAttempts += 1;
    this.emit("state", {
      state: "reconnecting",
      message: `${this.lastFailure || "翻译连接中断"}，${Math.ceil(delay / 1000)} 秒后自动重连…`,
    });
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect(true);
    }, delay);
  }

  failPermanently(message) {
    this.close();
    this.emit("event", { type: "error", retryable: false, error: { message } });
  }

  reportFailure(error, fallback) {
    const detail = connectionError(error, fallback);
    this.lastFailure = detail.message;
    console.warn("[Chestnut realtime]", JSON.stringify({ event: "connection_failed", phase: this.phase,
      code: detail.code, retryable: detail.retryable, attempt: this.reconnectAttempts,
      environment: environment.CLOUD_ENV_ID, service: environment.CLOUD_SERVICE }));
    if (!detail.retryable) this.failPermanently(detail.message);
    else this.scheduleReconnect();
  }

  connect(retrying = false) {
    this.close();
    if (!retrying) { this.reconnectAttempts = 0; this.lastFailure = ""; }
    this.intentionalClose = false;
    this.phase = "cloud_connect";
    const generation = ++this.generation;
    this.emit("state", {
      state: "connecting",
      message: "正在连接云端翻译服务…",
    });

    this.meetingId = this.meetingId || meetingState.state.startedAt || "mini-meeting";
    const query = `?auth=message&meeting_id=${encodeURIComponent(this.meetingId)}&languages=${encodeURIComponent(this.languagePair.join(","))}`;
    if (typeof wx.cloud?.connectContainer !== "function") {
      this.intentionalClose = true;
      this.emit("event", { type: "error", retryable: false, error: { message: "当前微信不支持云端实时翻译，请升级微信后重试" } });
      return;
    }
    let connection;
    try {
      connection = wx.cloud.connectContainer({
        config: environment.cloudConfig(),
        service: environment.CLOUD_SERVICE,
        path: `/ws${query}`,
        timeout: 20000,
      });
    } catch (error) {
      this.reportFailure(error, "无法启动实时连接，请检查微信版本和云服务配置");
      return;
    }

    connection.then(({ socketTask: task }) => {
      if (generation !== this.generation) {
        task.close({ code: 1000, reason: "Superseded connection" });
        return;
      }
      this.task = task;

      task.onOpen(() => {
        if (generation !== this.generation) return;
        this.phase = "authenticating";
        this.emit("state", { state: "connected", message: "服务已连接，正在准备翻译…" });
        task.send({ data: JSON.stringify({ type: "auth.authenticate", token: access.token() }),
          fail: error => { if (generation === this.generation) this.reportFailure(error, "实时连接认证消息发送失败"); } });
      });

      task.onMessage(({ data }) => {
        if (generation !== this.generation) return;
        if (typeof data !== "string") return;
        try {
          const event = JSON.parse(data);
          if (event.type === "connection.rate_limited") {
            this.failPermanently("连接过于频繁，请稍等一分钟后点击重新连接");
            return;
          }
          if (event.type === "access.denied" || event.type === "meeting.rejected" || event.type === "trial.ended" || (event.type === "error" && event.retryable === false)) { this.intentionalClose = true; clearTimeout(this.reconnectTimer); this.reconnectTimer = null; }
          if (event.type === "error") {
            this.phase = "translation";
            this.lastFailure = event.error?.message || "云端翻译服务返回错误";
          }
          if (event.type === "session.created") this.phase = "translation_setup";
          if (event.type === "session.updated") { this.reconnectAttempts = 0; this.lastFailure = ""; this.phase = "streaming"; }
          this.emit("event", event);
        } catch (error) {
          this.emit("error", { message: "收到无法解析的服务消息" });
        }
      });

      task.onError((error) => {
        if (generation !== this.generation || this.intentionalClose) return;
        this.reportFailure(error, this.lastFailure || "无法连接云端实时服务");
      });

      task.onClose((event) => {
        if (generation !== this.generation) return;
        this.task = null;
        if (!this.intentionalClose) {
          if (event?.code === 1008) this.failPermanently(this.lastFailure || "实时连接被服务拒绝，请返回首页重新验证邀请码");
          else this.reportFailure(event, this.lastFailure || (this.phase === "cloud_connect" ? "云端 WebSocket 握手失败" : "翻译连接中断"));
        }
      });
    }).catch((error) => {
      if (generation !== this.generation) return;
      this.reportFailure(error, "无法建立云端实时连接");
    });
  }

  sendAudio(frameBuffer) {
    if (!this.task) return false;
    this.task.send({
      data: frameBuffer,
      fail: (error) => this.emit("error", { message: "音频发送失败", detail: error }),
    });
    return true;
  }

  finish() {
    if (!this.task) return;
    // A finished or expired meeting must never reconnect into a fresh server
    // session after the current socket closes.
    this.intentionalClose = true;
    this.task.send({ data: JSON.stringify({ type: "session.finish" }) });
  }

  close() {
    this.intentionalClose = true;
    clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    this.generation += 1;
    const task = this.task;
    this.task = null;
    if (task) task.close({ code: 1000, reason: "Meeting finished" });
  }
}

module.exports = MeetingSocket;
