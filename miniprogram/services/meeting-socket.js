const environment = require("../config/environment");

class MeetingSocket {
  constructor() {
    this.task = null;
    this.listeners = new Map();
    this.intentionalClose = false;
    this.generation = 0;
  }

  subscribe(event, listener) {
    if (!this.listeners.has(event)) this.listeners.set(event, new Set());
    this.listeners.get(event).add(listener);
    return () => this.listeners.get(event)?.delete(listener);
  }

  emit(event, payload) {
    this.listeners.get(event)?.forEach((listener) => listener(payload));
  }

  connect() {
    this.close();
    this.intentionalClose = false;
    const generation = ++this.generation;
    this.emit("state", { state: "connecting", message: "正在连接本地翻译服务…" });
    const task = wx.connectSocket({
      url: environment.websocketUrl(),
      tcpNoDelay: true,
      timeout: 20000,
    });
    this.task = task;

    task.onOpen(() => {
      if (generation !== this.generation) return;
      this.emit("state", { state: "connected", message: "服务已连接，正在准备翻译…" });
    });

    task.onMessage(({ data }) => {
      if (generation !== this.generation) return;
      if (typeof data !== "string") return;
      try {
        this.emit("event", JSON.parse(data));
      } catch (error) {
        this.emit("error", { message: "收到无法解析的服务消息" });
      }
    });

    task.onError((error) => {
      if (generation !== this.generation) return;
      this.emit("error", {
        message: `无法连接 ${environment.websocketUrl()}，请确认 Chestnut 本地服务已启动`,
        detail: error,
      });
    });

    task.onClose(() => {
      if (generation !== this.generation) return;
      this.task = null;
      if (!this.intentionalClose) {
        this.emit("state", { state: "disconnected", message: "翻译连接已断开" });
      }
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
    this.task.send({ data: JSON.stringify({ type: "session.finish" }) });
  }

  close() {
    this.intentionalClose = true;
    this.generation += 1;
    const task = this.task;
    this.task = null;
    if (task) task.close({ code: 1000, reason: "Meeting finished" });
  }
}

module.exports = MeetingSocket;
