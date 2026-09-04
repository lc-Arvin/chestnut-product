const environment = require("../config/environment");

class MeetingSocket {
  constructor() {
    this.task = null;
    this.listeners = new Map();
    this.intentionalClose = false;
    this.generation = 0;
    this.reconnectTimer = null;
    this.reconnectAttempts = 0;
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
    const delay = Math.min(1000 * (2 ** this.reconnectAttempts), 10000);
    this.reconnectAttempts += 1;
    this.emit("state", {
      state: "reconnecting",
      message: `翻译连接中断，${Math.ceil(delay / 1000)} 秒后自动重连…`,
    });
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  connect() {
    this.close();
    this.intentionalClose = false;
    const generation = ++this.generation;
    this.emit("state", {
      state: "connecting",
      message: environment.isCloudEnabled() ? "正在连接云端翻译服务…" : "正在连接本地翻译服务…",
    });

    const connection = environment.isCloudEnabled()
      ? wx.cloud.connectContainer({
          service: environment.CLOUD_SERVICE,
          path: "/ws",
        })
      : Promise.resolve({
          socketTask: wx.connectSocket({
            url: environment.websocketUrl(),
            tcpNoDelay: true,
            timeout: 20000,
          }),
        });

    connection.then(({ socketTask: task }) => {
      if (generation !== this.generation) {
        task.close({ code: 1000, reason: "Superseded connection" });
        return;
      }
      this.task = task;

      task.onOpen(() => {
        if (generation !== this.generation) return;
        this.emit("state", { state: "connected", message: "服务已连接，正在准备翻译…" });
      });

      task.onMessage(({ data }) => {
        if (generation !== this.generation) return;
        if (typeof data !== "string") return;
        try {
          const event = JSON.parse(data);
          if (event.type === "session.updated") this.reconnectAttempts = 0;
          this.emit("event", event);
        } catch (error) {
          this.emit("error", { message: "收到无法解析的服务消息" });
        }
      });

      task.onError((error) => {
        if (generation !== this.generation) return;
        const serverHost = environment.getServerHost();
        const loopbackMessage = environment.isCloudEnabled()
          ? "无法连接云端翻译服务，请稍后重试"
          : environment.isLoopbackHost(serverHost)
            ? "真机不能使用 127.0.0.1，请返回 Setup 填写电脑的 Wi-Fi 地址"
            : `无法连接 ${environment.websocketUrl()}，请确认手机与电脑处于同一 Wi-Fi 且 Chestnut 服务已启动`;
        this.emit("state", { state: "reconnecting", message: loopbackMessage });
        this.scheduleReconnect();
      });

      task.onClose(() => {
        if (generation !== this.generation) return;
        this.task = null;
        if (!this.intentionalClose) {
          this.scheduleReconnect();
        }
      });
    }).catch((error) => {
      if (generation !== this.generation) return;
      this.emit("state", { state: "reconnecting", message: "无法建立翻译服务连接，正在自动重试…" });
      this.scheduleReconnect();
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
