const RECORD_DURATION_MS = 590000;
const FRAME_SIZE_KB = 4;

class ConferenceRecorder {
  constructor() {
    this.manager = wx.getRecorderManager();
    this.listeners = new Map();
    this.state = "idle";
    this.keepAlive = false;
    this.restarting = false;
    this.bindEvents();
  }

  bindEvents() {
    this.manager.onStart(() => {
      this.state = "recording";
      this.restarting = false;
      this.emit("state", { state: this.state });
    });

    this.manager.onFrameRecorded(({ frameBuffer, isLastFrame }) => {
      this.emit("frame", { frameBuffer, isLastFrame });
    });

    this.manager.onPause(() => {
      this.state = "paused";
      this.emit("state", { state: this.state });
    });

    this.manager.onResume(() => {
      this.state = "recording";
      this.emit("state", { state: this.state });
    });

    this.manager.onStop(() => {
      this.state = "idle";
      this.emit("state", { state: this.state });
      if (this.keepAlive) this.restartAfterRotation();
    });

    this.manager.onError((error) => {
      this.state = "error";
      this.keepAlive = false;
      this.emit("error", error);
      this.emit("state", { state: this.state });
    });

    if (this.manager.onInterruptionBegin) {
      this.manager.onInterruptionBegin(() => {
        this.state = "interrupted";
        this.emit("interruption", { active: true });
        this.emit("state", { state: this.state });
      });
    }

    if (this.manager.onInterruptionEnd) {
      this.manager.onInterruptionEnd(() => {
        this.emit("interruption", { active: false });
        if (this.keepAlive) this.resume();
      });
    }
  }

  subscribe(event, listener) {
    if (!this.listeners.has(event)) this.listeners.set(event, new Set());
    this.listeners.get(event).add(listener);
    return () => this.listeners.get(event)?.delete(listener);
  }

  emit(event, payload) {
    this.listeners.get(event)?.forEach((listener) => listener(payload));
  }

  recordOptions() {
    return {
      duration: RECORD_DURATION_MS,
      sampleRate: 16000,
      numberOfChannels: 1,
      format: "PCM",
      frameSize: FRAME_SIZE_KB,
      audioSource: "auto",
    };
  }

  start() {
    this.keepAlive = true;
    if (this.state === "recording") return;
    if (this.state === "paused" || this.state === "interrupted") {
      this.resume();
      return;
    }
    this.manager.start(this.recordOptions());
  }

  pause() {
    if (this.state !== "recording") return;
    this.manager.pause();
  }

  resume() {
    this.keepAlive = true;
    if (this.state === "paused" || this.state === "interrupted") {
      this.manager.resume();
      return;
    }
    if (this.state === "idle" || this.state === "error") this.manager.start(this.recordOptions());
  }

  stop() {
    this.keepAlive = false;
    this.restarting = false;
    if (["recording", "paused", "interrupted"].includes(this.state)) this.manager.stop();
    else this.state = "idle";
  }

  restartAfterRotation() {
    if (this.restarting) return;
    this.restarting = true;
    setTimeout(() => {
      if (!this.keepAlive) return;
      this.manager.start(this.recordOptions());
    }, 80);
  }
}

module.exports = new ConferenceRecorder();
