const initialState = () => ({
  startedAt: "",
  elapsedSeconds: 0,
  entries: [],
  lastResult: null,
});

const state = initialState();

function reset() {
  Object.assign(state, initialState());
}

function start() {
  state.startedAt = new Date().toISOString();
  state.elapsedSeconds = 0;
  state.entries = [];
  state.lastResult = null;
}

function addEntry(entry) {
  const text = String(entry.text || "").trim();
  if (!text) return;
  state.entries.push({
    time_seconds: Math.max(0, Number(entry.time_seconds) || 0),
    language: entry.language === "zh" ? "zh" : "en",
    role: entry.role === "translation" ? "translation" : "original",
    text,
  });
}

module.exports = {
  state,
  reset,
  start,
  addEntry,
};
