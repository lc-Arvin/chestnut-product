function pcmLevel(frameBuffer) {
  if (!frameBuffer || frameBuffer.byteLength < 2) return 0;
  const view = new DataView(frameBuffer);
  const sampleCount = Math.floor(view.byteLength / 2);
  const stride = Math.max(1, Math.floor(sampleCount / 600));
  let sum = 0;
  let count = 0;
  for (let index = 0; index < sampleCount; index += stride) {
    const sample = view.getInt16(index * 2, true) / 32768;
    sum += sample * sample;
    count += 1;
  }
  const rms = Math.sqrt(sum / Math.max(1, count));
  return Math.max(0, Math.min(1, rms * 8));
}

module.exports = { pcmLevel };
