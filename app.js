const screens = {
  setup: document.querySelector("#setup-screen"),
  audio: document.querySelector("#audio-screen"),
  live: document.querySelector("#live-screen"),
};

const startButton = document.querySelector("#start-button");
const continueButton = document.querySelector("#continue-button");
const pauseButton = document.querySelector("#pause-button");
const pauseLabel = document.querySelector("#pause-label");
const stopButton = document.querySelector("#stop-button");
const checkPanel = document.querySelector(".check-panel");
const audioVisual = document.querySelector("#audio-visual");
const countdown = document.querySelector("#countdown");
const audioEyebrow = document.querySelector("#audio-eyebrow");
const audioTitle = document.querySelector("#audio-title");
const audioMessage = document.querySelector("#audio-message");
const meetingTimer = document.querySelector("#meeting-timer");
const livePill = document.querySelector("#live-pill");
const liveStateLabel = document.querySelector("#live-state-label");
const connectionStatus = document.querySelector("#connection-status");
const connectionLabel = document.querySelector("#connection-label");
const retryButton = document.querySelector("#retry-button");
const englishStage = document.querySelector("#english-stage");
const englishPlaceholder = document.querySelector("#english-placeholder");
const englishHistory = document.querySelector("#english-history");
const englishCurrent = document.querySelector("#english-current");
const chineseStage = document.querySelector("#chinese-stage");
const chinesePlaceholder = document.querySelector("#chinese-placeholder");
const chineseHistory = document.querySelector("#chinese-history");
const chineseCurrent = document.querySelector("#chinese-current");
const meetingSaveNotice = document.querySelector("#meeting-save-notice");
const meetingSaveMessage = document.querySelector("#meeting-save-message");
const meetingFileLink = document.querySelector("#meeting-file-link");

let countdownInterval;
let meetingInterval;
let meetingSeconds = 0;
let microphoneStream;
let audioContext;
let analyser;
let meterFrame;
let inputDetected = false;
let bailianSocket;
let audioProcessor;
let streamingSource;
let silentOutput;
let connectingRealtime = false;
let meetingStartedAt;
let meetingRecords = [];
let isPaused = false;
let reconnectTimer;
let reconnectAttempts = 0;
let droppedAudioFrames = 0;
let stoppingMeeting = false;

const MAX_SOCKET_BUFFER_BYTES = 512 * 1024;
const MAX_DROPPED_AUDIO_FRAMES = 240;
const MAX_VISIBLE_CAPTIONS_PER_LANGUAGE = 6;

function showScreen(name) {
  Object.values(screens).forEach((screen) => screen.classList.remove("is-active"));
  screens[name].classList.add("is-active");
  document.body.classList.toggle("is-live", name === "live");
  if (name !== "live") document.body.classList.remove("is-paused");
}

function resetAudioCheck() {
  clearInterval(countdownInterval);
  checkPanel.classList.remove("is-passed", "is-ready");
  countdown.textContent = "05";
  countdown.setAttribute("aria-label", "5 seconds remaining");
  audioEyebrow.textContent = "Checking Microphone";
  audioTitle.textContent = "Audio Check";
  audioMessage.textContent = "Allow microphone access, then speak normally.";
  audioVisual.classList.add("is-waiting");
  audioVisual.style.setProperty("--audio-level", 0);
  inputDetected = false;
}

function monitorMicrophone(stream) {
  audioContext = new (window.AudioContext || window.webkitAudioContext)();
  const source = audioContext.createMediaStreamSource(stream);
  analyser = audioContext.createAnalyser();
  analyser.fftSize = 256;
  analyser.smoothingTimeConstant = 0.72;
  source.connect(analyser);

  const samples = new Uint8Array(analyser.frequencyBinCount);
  const drawMeter = () => {
    analyser.getByteFrequencyData(samples);
    const average = samples.reduce((sum, value) => sum + value, 0) / samples.length;
    const level = Math.min(1, average / 70);
    if (level > 0.08) inputDetected = true;
    audioVisual.style.setProperty("--audio-level", level.toFixed(2));
    meterFrame = requestAnimationFrame(drawMeter);
  };
  drawMeter();
}

function releaseMicrophone() {
  cancelAnimationFrame(meterFrame);
  microphoneStream?.getTracks().forEach((track) => track.stop());
  microphoneStream = undefined;
  audioContext?.close();
  audioContext = undefined;
}

async function beginAudioCheck() {
  resetAudioCheck();
  showScreen("audio");

  if (!navigator.mediaDevices?.getUserMedia) {
    audioEyebrow.textContent = "Microphone Unavailable";
    audioTitle.textContent = "Audio Access Needed";
    audioMessage.textContent = "Open this page in a browser that allows microphone access.";
    return;
  }

  try {
    audioEyebrow.textContent = "Waiting for Permission";
    audioMessage.textContent = "Choose Allow when your browser asks for microphone access.";
    microphoneStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      video: false,
    });
    monitorMicrophone(microphoneStream);
    audioVisual.classList.remove("is-waiting");
    audioEyebrow.textContent = "Listening";
    audioMessage.textContent = "Speak normally — the bars will respond to your voice.";
  } catch (error) {
    audioVisual.classList.remove("is-waiting");
    audioEyebrow.textContent = "Permission Needed";
    audioTitle.textContent = "Microphone Not Available";
    audioMessage.textContent = "Allow microphone access in your browser, then return to Meeting Setup and try again.";
    window.setTimeout(() => showScreen("setup"), 3200);
    return;
  }

  let remaining = 5;
  countdownInterval = window.setInterval(() => {
    remaining -= 1;
    countdown.textContent = String(remaining).padStart(2, "0");
    countdown.setAttribute("aria-label", `${remaining} seconds remaining`);

    if (remaining === 0) {
      clearInterval(countdownInterval);
      checkPanel.classList.add("is-passed");
      audioEyebrow.textContent = inputDetected ? "Ready for Live Meeting" : "Microphone Connected";
      audioTitle.textContent = inputDetected ? "Audio Check Passed" : "Ready to Listen";
      audioMessage.textContent = inputDetected
        ? "Your voice was detected clearly."
        : "The microphone is connected, but no voice was detected yet.";

      window.setTimeout(() => checkPanel.classList.add("is-ready"), 1200);
    }
  }, 1000);
}

function formatTime(totalSeconds) {
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return [hours, minutes, seconds].map((part) => String(part).padStart(2, "0")).join(":");
}

function setConnectionState(state, label) {
  connectionStatus.classList.toggle("is-connected", state === "connected");
  connectionStatus.classList.toggle("is-error", state === "error");
  connectionLabel.textContent = label;
}

function captionColumn(language) {
  const chinese = ["zh", "yue"].includes(language);
  return chinese
    ? { history: chineseHistory, current: chineseCurrent, placeholder: chinesePlaceholder, stage: chineseStage }
    : { history: englishHistory, current: englishCurrent, placeholder: englishPlaceholder, stage: englishStage };
}

function setCurrentCaption(text, language, role) {
  const { history, current, placeholder, stage } = captionColumn(language);
  const caption = text?.trim() || "";
  placeholder.hidden = Boolean(caption || history.children.length);
  current.textContent = caption;
  current.className = `transcript-current is-${role}`;
  current.dataset.label = role === "original" ? "ORIGINAL" : "TRANSLATION";
  stage.scrollTop = stage.scrollHeight;
}

function clearCurrentCaption(current) {
  current.textContent = "";
  current.className = "transcript-current";
  current.removeAttribute("data-label");
}

function appendCaption(text, language, role) {
  if (!text?.trim()) return;
  const { history, current, placeholder, stage } = captionColumn(language);
  placeholder.hidden = true;
  const entry = document.createElement("div");
  entry.className = `caption-entry is-${role}`;
  const badge = document.createElement("span");
  badge.className = "caption-role";
  badge.textContent = role === "original" ? "ORIGINAL" : "TRANSLATION";
  const line = document.createElement("p");
  line.className = "transcript-line";
  line.textContent = text.trim();
  entry.append(badge, line);
  history.append(entry);
  while (history.children.length > MAX_VISIBLE_CAPTIONS_PER_LANGUAGE) {
    history.firstElementChild.remove();
  }
  if (current.classList.contains(`is-${role}`)) clearCurrentCaption(current);
  stage.scrollTop = stage.scrollHeight;
  meetingRecords.push({
    time_seconds: meetingSeconds,
    language: ["zh", "yue"].includes(language) ? "zh" : "en",
    role,
    text: text.trim(),
  });
}

function handleRealtimeEvent(event) {
  if (event.type === "session.updated") {
    reconnectAttempts = 0;
    droppedAudioFrames = 0;
    if (isPaused) {
      setConnectionState("paused", "Paused · Microphone muted");
    } else {
      setConnectionState("connected", "Listening · Automatic bilingual translation connected");
      startPcmStreaming();
    }
  }

  if (event.type === "conversation.item.input_audio_transcription.text") {
    setCurrentCaption(`${event.text || ""}${event.stash || ""}`, event.language || "en", "original");
  }

  if (event.type === "conversation.item.input_audio_transcription.completed") {
    const column = captionColumn(event.language || "en");
    appendCaption(event.transcript || column.current.textContent, event.language || "en", "original");
  }

  if (event.type === "response.text.text") {
    setCurrentCaption(
      `${event.text || ""}${event.stash || ""}`,
      event.translation_target || "zh",
      "translation",
    );
  }

  if (event.type === "response.text.done") {
    const language = event.translation_target || "zh";
    const column = captionColumn(language);
    appendCaption(event.text || column.current.textContent, language, "translation");
  }

  if (event.type === "error") {
    setConnectionState("error", event.error?.message || "Live translation encountered an error");
  }
}

function downsampleToPcm16(input, inputRate, outputRate = 16000) {
  const ratio = inputRate / outputRate;
  const length = Math.floor(input.length / ratio);
  const pcm = new Int16Array(length);
  for (let index = 0; index < length; index += 1) {
    const start = Math.floor(index * ratio);
    const end = Math.max(start + 1, Math.floor((index + 1) * ratio));
    let sum = 0;
    for (let sample = start; sample < end && sample < input.length; sample += 1) sum += input[sample];
    const value = Math.max(-1, Math.min(1, sum / (end - start)));
    pcm[index] = value < 0 ? value * 0x8000 : value * 0x7fff;
  }
  return pcm.buffer;
}

function startPcmStreaming() {
  if (isPaused || audioProcessor || !microphoneStream?.active || bailianSocket?.readyState !== WebSocket.OPEN) return;
  streamingSource = audioContext.createMediaStreamSource(microphoneStream);
  audioProcessor = audioContext.createScriptProcessor(4096, 1, 1);
  silentOutput = audioContext.createGain();
  silentOutput.gain.value = 0;
  audioProcessor.onaudioprocess = ({ inputBuffer }) => {
    if (bailianSocket?.readyState === WebSocket.OPEN) {
      if (bailianSocket.bufferedAmount > MAX_SOCKET_BUFFER_BYTES) {
        droppedAudioFrames += 1;
        if (droppedAudioFrames === 1) {
          setConnectionState("connecting", "Translation connection is congested · Recovering…");
        }
        if (droppedAudioFrames >= MAX_DROPPED_AUDIO_FRAMES) {
          bailianSocket.close(1013, "Audio backpressure");
        }
        return;
      }
      droppedAudioFrames = 0;
      bailianSocket.send(downsampleToPcm16(inputBuffer.getChannelData(0), inputBuffer.sampleRate));
    }
  };
  streamingSource.connect(audioProcessor);
  audioProcessor.connect(silentOutput);
  silentOutput.connect(audioContext.destination);
}

function stopPcmStreaming() {
  audioProcessor?.disconnect();
  streamingSource?.disconnect();
  silentOutput?.disconnect();
  if (audioProcessor) audioProcessor.onaudioprocess = null;
  audioProcessor = undefined;
  streamingSource = undefined;
  silentOutput = undefined;
}

function scheduleRealtimeReconnect() {
  if (reconnectTimer || stoppingMeeting || isPaused || !microphoneStream?.active || !screens.live.classList.contains("is-active")) return;
  const delay = Math.min(1000 * (2 ** reconnectAttempts), 10000);
  reconnectAttempts += 1;
  setConnectionState("connecting", `Translation disconnected · Reconnecting in ${Math.ceil(delay / 1000)}s…`);
  reconnectTimer = window.setTimeout(() => {
    reconnectTimer = undefined;
    connectBailian();
  }, delay);
}

function connectBailian() {
  if (connectingRealtime || !microphoneStream?.active) return;
  clearTimeout(reconnectTimer);
  reconnectTimer = undefined;
  connectingRealtime = true;
  stopPcmStreaming();
  const previousSocket = bailianSocket;
  bailianSocket = undefined;
  previousSocket?.close();
  setConnectionState("connecting", "Connecting to Bailian live translation…");

  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${scheme}://${location.host}/ws`);
  bailianSocket = socket;
  socket.addEventListener("message", ({ data }) => {
    if (bailianSocket !== socket) return;
    try { handleRealtimeEvent(JSON.parse(data)); } catch { /* Ignore malformed service events. */ }
  });
  socket.addEventListener("open", () => {
    if (bailianSocket !== socket) return;
    connectingRealtime = false;
  });
  socket.addEventListener("error", () => {
    if (bailianSocket !== socket) return;
    connectingRealtime = false;
    setConnectionState("connecting", "Live translation connection failed · Recovering…");
  });
  socket.addEventListener("close", () => {
    if (bailianSocket !== socket) return;
    connectingRealtime = false;
    stopPcmStreaming();
    if (isPaused && screens.live.classList.contains("is-active")) {
      setConnectionState("paused", "Paused · Connection will resume when needed");
    } else if (screens.live.classList.contains("is-active")) {
      scheduleRealtimeReconnect();
    }
  });
}

function updatePauseState() {
  document.body.classList.toggle("is-paused", isPaused);
  livePill.classList.toggle("is-paused", isPaused);
  liveStateLabel.textContent = isPaused ? "PAUSED" : "LIVE";
  pauseLabel.textContent = isPaused ? "Resume" : "Pause";
  pauseButton.setAttribute("aria-pressed", String(isPaused));
}

function togglePause() {
  if (!screens.live.classList.contains("is-active") || !microphoneStream?.active) return;
  isPaused = !isPaused;
  microphoneStream.getAudioTracks().forEach((track) => { track.enabled = !isPaused; });
  updatePauseState();

  if (isPaused) {
    clearTimeout(reconnectTimer);
    reconnectTimer = undefined;
    stopPcmStreaming();
    setConnectionState("paused", "Paused · Microphone muted");
  } else if (bailianSocket?.readyState === WebSocket.OPEN) {
    setConnectionState("connected", "Listening · Automatic bilingual translation connected");
    startPcmStreaming();
  } else {
    connectBailian();
  }
}

async function beginMeeting() {
  clearInterval(meetingInterval);
  clearTimeout(reconnectTimer);
  reconnectTimer = undefined;
  reconnectAttempts = 0;
  droppedAudioFrames = 0;
  stoppingMeeting = false;
  meetingSeconds = 0;
  isPaused = false;
  updatePauseState();
  meetingStartedAt = new Date();
  meetingRecords = [];
  meetingSaveNotice.hidden = true;
  meetingTimer.textContent = "00:00:00";
  meetingTimer.dateTime = "PT0S";
  englishHistory.replaceChildren();
  chineseHistory.replaceChildren();
  clearCurrentCaption(englishCurrent);
  clearCurrentCaption(chineseCurrent);
  englishPlaceholder.hidden = false;
  chinesePlaceholder.hidden = false;
  setConnectionState("connecting", "Connecting to Bailian live translation…");
  showScreen("live");

  meetingInterval = window.setInterval(() => {
    if (isPaused) return;
    meetingSeconds += 1;
    meetingTimer.textContent = formatTime(meetingSeconds);
    meetingTimer.dateTime = `PT${meetingSeconds}S`;
  }, 1000);

  connectBailian();
}

function capturePendingCaption(current, language) {
  const text = current.textContent.trim();
  if (!text) return;
  meetingRecords.push({
    time_seconds: meetingSeconds,
    language,
    role: current.classList.contains("is-translation") ? "translation" : "original",
    text,
  });
}

async function saveMeetingTranscript() {
  meetingSaveNotice.hidden = false;
  meetingSaveNotice.classList.remove("is-error");
  meetingSaveMessage.textContent = "Saving meeting transcript…";
  meetingFileLink.hidden = true;

  try {
    const response = await fetch("/api/meetings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        started_at: meetingStartedAt?.toISOString(),
        ended_at: new Date().toISOString(),
        duration_seconds: meetingSeconds,
        entries: meetingRecords,
      }),
    });
    if (!response.ok) throw new Error(await response.text());
    const result = await response.json();
    meetingSaveMessage.textContent = `Transcript saved · ${result.filename}`;
    meetingFileLink.href = result.url;
    meetingFileLink.hidden = false;
  } catch (error) {
    meetingSaveNotice.classList.add("is-error");
    meetingSaveMessage.textContent = "Transcript could not be saved. Keep this window open and try stopping again.";
    meetingFileLink.hidden = true;
  }
}

async function stopMeeting() {
  stoppingMeeting = true;
  clearInterval(meetingInterval);
  clearTimeout(reconnectTimer);
  reconnectTimer = undefined;
  meetingInterval = undefined;
  capturePendingCaption(englishCurrent, "en");
  capturePendingCaption(chineseCurrent, "zh");
  stopPcmStreaming();
  const socket = bailianSocket;
  bailianSocket = undefined;
  isPaused = false;
  updatePauseState();
  releaseMicrophone();
  showScreen("setup");
  if (socket?.readyState === WebSocket.OPEN) {
    await new Promise((resolve) => {
      const timeout = window.setTimeout(() => {
        socket.close();
        resolve();
      }, 6000);
      socket.addEventListener("close", () => {
        clearTimeout(timeout);
        resolve();
      }, { once: true });
      socket.send(JSON.stringify({ type: "session.finish" }));
    });
  } else {
    socket?.close();
  }
  await saveMeetingTranscript();
  stoppingMeeting = false;
}

startButton.addEventListener("click", beginAudioCheck);
continueButton.addEventListener("click", beginMeeting);
pauseButton.addEventListener("click", togglePause);
stopButton.addEventListener("click", stopMeeting);
retryButton.addEventListener("click", connectBailian);
