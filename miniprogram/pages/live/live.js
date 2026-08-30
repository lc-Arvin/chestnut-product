const recorder = require("../../services/recorder");
const MeetingSocket = require("../../services/meeting-socket");
const meetingState = require("../../services/meeting-state");
const { saveMeeting } = require("../../services/meeting-api");
const { formatTime } = require("../../utils/time");
const { safeTopPadding } = require("../../utils/layout");

Page({
  data: {
    timer: "00:00:00",
    isPaused: false,
    ending: false,
    connectionState: "connecting",
    connectionMessage: "正在连接本地翻译服务…",
    englishEntries: [],
    chineseEntries: [],
    englishCurrent: "",
    chineseCurrent: "",
    englishCurrentRole: "original",
    chineseCurrentRole: "translation",
    englishCurrentLabel: "ORIGINAL",
    chineseCurrentLabel: "TRANSLATION",
    englishAnchor: "",
    chineseAnchor: "",
    showEnglishPlaceholder: true,
    showChinesePlaceholder: true,
    safeTop: safeTopPadding(4),
  },

  onLoad() {
    this.socket = new MeetingSocket();
    this.readyForAudio = false;
    this.entrySequence = 0;
    this.finishedTargets = new Set();
    this.unsubscribers = [
      this.socket.subscribe("state", (event) => this.handleConnectionState(event)),
      this.socket.subscribe("event", (event) => this.handleRealtimeEvent(event)),
      this.socket.subscribe("error", (error) => this.handleServiceError(error)),
      recorder.subscribe("frame", ({ frameBuffer }) => {
        if (this.readyForAudio && !this.data.isPaused && !this.data.ending) this.socket.sendAudio(frameBuffer);
      }),
      recorder.subscribe("error", (error) => this.handleRecorderError(error)),
      recorder.subscribe("interruption", ({ active }) => this.handleInterruption(active)),
    ];

    meetingState.start();
    this.startTimer();
    this.socket.connect();
  },

  onUnload() {
    clearInterval(this.timerInterval);
    clearTimeout(this.finishTimeout);
    this.unsubscribers?.forEach((unsubscribe) => unsubscribe());
    if (!this.data.ending) {
      recorder.stop();
      this.socket?.close();
    }
  },

  onHide() {
    if (this.data.ending || this.data.isPaused) return;
    recorder.pause();
    this.setData({ isPaused: true, connectionMessage: "小程序进入后台 · 麦克风已暂停" });
  },

  onResize() {
    this.setData({ safeTop: safeTopPadding(4) });
  },

  startTimer() {
    this.timerInterval = setInterval(() => {
      if (this.data.isPaused || this.data.ending) return;
      meetingState.state.elapsedSeconds += 1;
      this.setData({ timer: formatTime(meetingState.state.elapsedSeconds) });
    }, 1000);
  },

  handleConnectionState({ state, message }) {
    if (this.data.ending) return;
    this.setData({ connectionState: state, connectionMessage: message });
  },

  handleServiceError(error) {
    if (this.data.ending) return;
    this.readyForAudio = false;
    recorder.pause();
    this.setData({
      connectionState: "error",
      connectionMessage: error.message || "实时翻译连接失败",
      isPaused: true,
    });
  },

  handleRecorderError(error) {
    this.readyForAudio = false;
    this.setData({
      connectionState: "error",
      connectionMessage: error?.errMsg || "麦克风录音失败",
      isPaused: true,
    });
  },

  handleInterruption(active) {
    if (active) {
      this.setData({ isPaused: true, connectionMessage: "录音被通话或系统暂时中断" });
    } else if (!this.data.ending) {
      this.setData({ isPaused: false, connectionMessage: "录音已恢复" });
    }
  },

  handleRealtimeEvent(event) {
    if (event.type === "session.updated") {
      this.readyForAudio = true;
      if (!this.data.isPaused) recorder.resume();
      this.setData({
        connectionState: "connected",
        connectionMessage: this.data.isPaused ? "会议已暂停 · 麦克风关闭" : "正在收音 · 双向翻译已连接",
      });
      return;
    }

    if (event.type === "conversation.item.input_audio_transcription.text") {
      this.setCurrentCaption(`${event.text || ""}${event.stash || ""}`, event.language || "en", "original");
      return;
    }

    if (event.type === "conversation.item.input_audio_transcription.completed") {
      const language = this.normalizedLanguage(event.language || "en");
      this.appendCaption(event.transcript || this.currentText(language), language, "original");
      return;
    }

    if (event.type === "response.text.text") {
      this.setCurrentCaption(`${event.text || ""}${event.stash || ""}`, event.translation_target || "zh", "translation");
      return;
    }

    if (event.type === "response.text.done") {
      const language = this.normalizedLanguage(event.translation_target || "zh");
      this.appendCaption(event.text || this.currentText(language), language, "translation");
      return;
    }

    if (event.type === "error") {
      this.handleServiceError({ message: event.error?.message || "百炼实时翻译返回错误" });
      return;
    }

    if (event.type === "session.finished" && this.data.ending) {
      this.finishedTargets.add(event.translation_target || `target-${this.finishedTargets.size + 1}`);
      this.setData({ connectionMessage: "翻译已完成，正在保存会议稿…" });
      if (this.finishedTargets.size >= 2) setTimeout(() => this.finalizeMeeting(), 120);
    }
  },

  normalizedLanguage(language) {
    return ["zh", "yue"].includes(language) ? "zh" : "en";
  },

  currentText(language) {
    return language === "zh" ? this.data.chineseCurrent : this.data.englishCurrent;
  },

  setCurrentCaption(text, language, role) {
    const normalized = this.normalizedLanguage(language);
    const label = role === "original" ? "ORIGINAL" : "TRANSLATION";
    if (normalized === "zh") {
      this.setData({
        chineseCurrent: text,
        chineseCurrentRole: role,
        chineseCurrentLabel: label,
        chineseAnchor: "chinese-end",
        showChinesePlaceholder: false,
      });
    } else {
      this.setData({
        englishCurrent: text,
        englishCurrentRole: role,
        englishCurrentLabel: label,
        englishAnchor: "english-end",
        showEnglishPlaceholder: false,
      });
    }
  },

  appendCaption(text, language, role) {
    const cleanText = String(text || "").trim();
    if (!cleanText) return;
    const normalized = this.normalizedLanguage(language);
    const entry = {
      id: `caption-${++this.entrySequence}`,
      text: cleanText,
      role,
      label: role === "original" ? "ORIGINAL" : "TRANSLATION",
    };
    meetingState.addEntry({
      time_seconds: meetingState.state.elapsedSeconds,
      language: normalized,
      role,
      text: cleanText,
    });

    if (normalized === "zh") {
      this.setData({
        chineseEntries: [...this.data.chineseEntries.slice(-59), entry],
        chineseCurrent: "",
        chineseAnchor: "chinese-end",
        showChinesePlaceholder: false,
      });
    } else {
      this.setData({
        englishEntries: [...this.data.englishEntries.slice(-59), entry],
        englishCurrent: "",
        englishAnchor: "english-end",
        showEnglishPlaceholder: false,
      });
    }
  },

  togglePause() {
    if (this.data.ending || !this.readyForAudio) return;
    const isPaused = !this.data.isPaused;
    if (isPaused) recorder.pause();
    else recorder.resume();
    this.setData({
      isPaused,
      connectionMessage: isPaused ? "会议已暂停 · 麦克风关闭" : "正在收音 · 双向翻译已连接",
    });
  },

  reconnect() {
    if (this.data.ending) return;
    this.readyForAudio = false;
    recorder.pause();
    this.setData({ isPaused: false });
    this.socket.connect();
  },

  stopMeeting() {
    if (this.data.ending) return;
    this.setData({ ending: true, connectionState: "finishing", connectionMessage: "正在完成最后一句并保存会议稿…" });
    clearInterval(this.timerInterval);
    this.readyForAudio = false;
    recorder.stop();
    this.socket.finish();
    this.finishTimeout = setTimeout(() => this.finalizeMeeting(), 4500);
  },

  capturePendingCaptions() {
    if (this.data.englishCurrent) this.appendCaption(this.data.englishCurrent, "en", this.data.englishCurrentRole);
    if (this.data.chineseCurrent) this.appendCaption(this.data.chineseCurrent, "zh", this.data.chineseCurrentRole);
  },

  async finalizeMeeting() {
    if (this.finalizing) return;
    this.finalizing = true;
    clearTimeout(this.finishTimeout);
    this.socket.close();
    this.capturePendingCaptions();
    const payload = {
      started_at: meetingState.state.startedAt,
      ended_at: new Date().toISOString(),
      duration_seconds: meetingState.state.elapsedSeconds,
      entries: meetingState.state.entries,
    };

    let result;
    try {
      const saved = await saveMeeting(payload);
      result = {
        saved: true,
        filename: saved.filename,
        duration: formatTime(meetingState.state.elapsedSeconds),
        entryCount: meetingState.state.entries.length,
      };
    } catch (error) {
      result = {
        saved: false,
        error: error.message,
        duration: formatTime(meetingState.state.elapsedSeconds),
        entryCount: meetingState.state.entries.length,
      };
    }
    meetingState.state.lastResult = result;
    wx.redirectTo({ url: "/pages/meeting-result/meeting-result" });
  },
});
