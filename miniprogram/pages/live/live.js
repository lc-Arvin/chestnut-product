const access = require("../../services/access");
const languages = require("../../utils/languages");
const recorder = require("../../services/recorder");
const MeetingSocket = require("../../services/meeting-socket");
const meetingState = require("../../services/meeting-state");
const { saveMeeting } = require("../../services/meeting-api");
const { formatTime } = require("../../utils/time");
const { safeTopPadding } = require("../../utils/layout");

Page({
  data: {
    timer: "00:00:00",
    trialTime: "",
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
    meetingWarningVisible: false,
    meetingWarningTime: "",
    safeTop: safeTopPadding(4),
  },

  onLoad() {
    this.trial = access.trialInfo?.();
    this.trialDeadline = this.trial?.state === "active" ? Date.now() + this.trial.remaining_seconds * 1000 : null;
    this.languagePair = [...meetingState.state.languagePair];
    this.setData({
      firstLanguage: languages.labels[this.languagePair[0]],
      secondLanguage: languages.labels[this.languagePair[1]],
      firstCode: this.languagePair[0].toUpperCase(),
      secondCode: this.languagePair[1].toUpperCase(),
    });
    this.socket = new MeetingSocket();
    this.readyForAudio = false;
    this.entrySequence = 0;
    this.finishedTargets = new Set();
    this.meetingWarningRemaining = 0;
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
      this.updateTrialCountdown();
      if (this.meetingWarningRemaining > 0 && !this.data.ending) {
        this.meetingWarningRemaining -= 1;
        this.setData({
          meetingWarningVisible: this.meetingWarningRemaining > 0,
          meetingWarningTime: formatTime(this.meetingWarningRemaining).slice(3),
        });
      }
      if (this.data.isPaused || this.data.ending) return;
      meetingState.state.elapsedSeconds += 1;
      this.setData({ timer: formatTime(meetingState.state.elapsedSeconds) });
    }, 1000);
  },

  handleConnectionState({ state, message }) {
    if (this.data.ending) return;
    if (["connecting", "reconnecting", "disconnected"].includes(state)) {
      this.readyForAudio = false;
      recorder.pause();
    }
    this.setData({ connectionState: state, connectionMessage: message });
  },

  handleServiceError(error) {
    if (this.data.ending) return;
    this.readyForAudio = false;
    recorder.pause();
    this.setData({
      connectionState: "reconnecting",
      connectionMessage: error.message || "实时翻译连接失败，正在自动恢复…",
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
    if (event.type === "trial.status") {
      this.trial = event;
      this.trialDeadline = event.state === "active" ? Date.now() + event.remaining_seconds * 1000 : null;
      this.updateTrialCountdown();
      return;
    }
    if (event.type === "trial.ended") { this.stopMeeting("trial"); return; }
    if (event.type === "access.denied") {
      access.clear();
      this.socket.close();
      this.stopMeeting("auth");
      return;
    }
    if (event.type === "meeting.rejected") {
      this.socket.close();
      recorder.stop();
      this.readyForAudio = false;
      this.setData({ connectionState: "error", connectionMessage: event.message || event.error?.message || "会议无法启动", isPaused: true });
      return;
    }

    if (event.type === "meeting.limit_warning") {
      this.meetingWarningRemaining = Math.max(0, Number(event.remaining_seconds) || 0);
      this.setData({
        meetingWarningVisible: this.meetingWarningRemaining > 0,
        meetingWarningTime: formatTime(this.meetingWarningRemaining).slice(3),
      });
      return;
    }

    if (event.type === "meeting.limit_reached") {
      this.meetingWarningRemaining = 0;
      this.stopMeeting("limit");
      return;
    }

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
      this.setCurrentCaption(`${event.text || ""}${event.stash || ""}`, event.language || this.languagePair[1], "original");
      return;
    }

    if (event.type === "conversation.item.input_audio_transcription.completed") {
      const language = this.normalizedLanguage(event.language || this.languagePair[1]);
      this.appendCaption(event.transcript || this.currentText(language), language, "original");
      return;
    }

    if (event.type === "response.text.text") {
      this.setCurrentCaption(`${event.text || ""}${event.stash || ""}`, event.translation_target || this.languagePair[0], "translation");
      return;
    }

    if (event.type === "response.text.done") {
      const language = this.normalizedLanguage(event.translation_target || this.languagePair[0]);
      this.appendCaption(event.text || this.currentText(language), language, "translation");
      return;
    }

    if (event.type === "error") {
      if (event.retryable === false) {
        this.socket.close();
        this.readyForAudio = false;
        recorder.pause();
        this.setData({ isPaused: true, connectionState: "error", connectionMessage: event.error?.message || "请检查翻译服务配置后重新连接" });
        return;
      }
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
    return languages.normalize(language);
  },

  currentText(language) {
    return language === this.languagePair[0] ? this.data.chineseCurrent : this.data.englishCurrent;
  },

  setCurrentCaption(text, language, role) {
    const normalized = this.normalizedLanguage(language);
    if (!this.languagePair.includes(normalized)) return;
    const label = role === "original" ? "ORIGINAL" : "TRANSLATION";
    if (normalized === this.languagePair[0]) {
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
    if (!this.languagePair.includes(normalized)) return;
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

    if (normalized === this.languagePair[0]) {
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

  updateTrialCountdown() {
    if (!this.trial || this.data.ending) return;
    const remaining = this.trialDeadline === null ? this.trial.duration_seconds : Math.max(0, Math.ceil((this.trialDeadline - Date.now()) / 1000));
    this.setData({ trialTime: formatTime(remaining).slice(3), trialEnding: remaining <= 30 });
    if (remaining <= 0) this.stopMeeting("trial");
  },

  stopMeeting(trigger) {
    if (this.data.ending) return;
    const limitReached = trigger === "limit";
    this.requireReauth = limitReached || trigger === "auth";
    this.meetingWarningRemaining = 0;
    this.setData({
      ending: true,
      meetingWarningVisible: false,
      connectionState: "finishing",
      connectionMessage: limitReached
        ? "会议已到达时长上限 · 正在保存会议稿…"
        : "正在完成最后一句并保存会议稿…",
    });
    clearInterval(this.timerInterval);
    this.readyForAudio = false;
    recorder.stop();
    this.socket.finish();
    this.finishTimeout = setTimeout(() => this.finalizeMeeting(), 4500);
  },

  capturePendingCaptions() {
    if (this.data.englishCurrent) this.appendCaption(this.data.englishCurrent, this.languagePair[1], this.data.englishCurrentRole);
    if (this.data.chineseCurrent) this.appendCaption(this.data.chineseCurrent, this.languagePair[0], this.data.chineseCurrentRole);
  },

  async finalizeMeeting() {
    if (this.finalizing) return;
    this.finalizing = true;
    clearTimeout(this.finishTimeout);
    this.socket.close();
    this.capturePendingCaptions();
    const payload = {
      meeting_id: this.socket.meetingId,
      started_at: meetingState.state.startedAt,
      ended_at: new Date().toISOString(),
      duration_seconds: meetingState.state.elapsedSeconds,
      entries: meetingState.state.entries,
    };

    meetingState.state.pendingPayload = payload;
    let result;
    if (this.trial) {
      try { await access.request("/api/auth/trial/finish", "POST"); } catch { /* Server deadline still applies. */ }
    }
    try {
      const saved = await saveMeeting(payload);
      meetingState.state.pendingPayload = null;
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
    if (this.requireReauth) access.clear();
    if (this.trial) { result.trial = true; meetingState.state.trialComplete = true; }
    meetingState.state.lastResult = result;
    wx.redirectTo({ url: "/pages/meeting-result/meeting-result" });
  },
});
