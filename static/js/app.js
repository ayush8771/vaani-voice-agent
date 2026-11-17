// VAANI voice client.
//
// Two ways to hear the caller:
//  * "gnani"   - record the microphone, detect when the caller stops talking, send WAV
//                to /voice (Gnani Prisma transcribes and detects the language).
//  * "browser" - the browser's own speech recognition (fallback without a Gnani key).
// Replies come back as text + MP3 audio in the caller's language.

// ---------- Elements ----------
const chat = document.getElementById("chat");
const startBtn = document.getElementById("startBtn");
const endBtn = document.getElementById("endBtn");
const statusPill = document.getElementById("status-pill");
const statusDot = document.getElementById("status-dot");
const statusText = document.getElementById("status-text");
const errorBanner = document.getElementById("error-banner");
const langBadge = document.getElementById("lang-badge");

// ---------- State ----------
let mode = "browser";        // set from /voice/config
let started = false;
let busy = false;            // waiting for the server
let speaking = false;        // Vaani's audio is playing
let currentAudio = null;
let ignoreInputUntil = 0;
const ECHO_GUARD_MS = 700;

const LANG_LABEL = { en: "English", hi: "हिंदी", hinglish: "Hinglish", kn: "ಕನ್ನಡ" };

// ---------- UI ----------
function setStatus(state) {
  if (!statusPill) return;
  statusPill.className = "status-pill";
  if (state) statusPill.classList.add(state);
  statusDot.textContent = "●";
  statusText.textContent = {
    listening: "Listening…",
    hearing: "Hearing you…",
    processing: "Processing…",
    speaking: "Tap to interrupt",
  }[state] || (started ? "Ready to assist" : "Ready to start");
  if (!state) statusPill.classList.add("ready");
}

function setLanguage(lang) {
  if (langBadge && lang) {
    langBadge.textContent = LANG_LABEL[lang] || lang;
    langBadge.style.display = "inline-block";
  }
}

function hidePlaceholder() {
  const placeholder = document.getElementById("chat-placeholder");
  if (placeholder) placeholder.style.display = "none";
}

function showError(msg) {
  if (!errorBanner) return;
  errorBanner.textContent = msg;
  errorBanner.style.display = "block";
  setTimeout(() => { errorBanner.style.display = "none"; }, 5000);
}

function addMessage(text, who) {
  hidePlaceholder();
  const typing = document.getElementById("typing");
  if (typing) typing.remove();
  const div = document.createElement("div");
  div.className = who;
  div.textContent = text;
  chat.appendChild(div);
  requestAnimationFrame(() => { chat.scrollTop = chat.scrollHeight; });
}

function showTyping() {
  setStatus("processing");
  if (document.getElementById("typing")) return;
  const row = document.createElement("div");
  row.id = "typing";
  row.className = "typing";
  row.innerHTML = `<span class="dot"></span><span class="dot"></span><span class="dot"></span> Processing…`;
  chat.appendChild(row);
  requestAnimationFrame(() => { chat.scrollTop = chat.scrollHeight; });
}

function removeTyping() {
  const typing = document.getElementById("typing");
  if (typing) typing.remove();
}

function playChime() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.type = "sine";
    o.frequency.value = 660;
    o.connect(g);
    g.connect(ctx.destination);
    g.gain.setValueAtTime(0.0001, ctx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.07, ctx.currentTime + 0.03);
    g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.30);
    o.start();
    o.stop(ctx.currentTime + 0.32);
  } catch (e) { /* chime is optional */ }
}

// ---------- Playback ----------
function listenAgain(delay) {
  ignoreInputUntil = Date.now() + delay;
  if (!started) return;
  if (mode === "browser") scheduleBrowserListening(delay);
  else setStatus("listening");
}

function playReply(data) {
  if (!data.audio) {
    speaking = false;
    listenAgain(200);
    return;
  }
  speaking = true;
  setStatus("speaking");
  currentAudio = new Audio(`data:${data.audio_mime || "audio/mpeg"};base64,${data.audio}`);
  const done = (delay) => {
    speaking = false;
    currentAudio = null;
    listenAgain(delay);
  };
  currentAudio.onended = () => done(ECHO_GUARD_MS);
  currentAudio.onerror = () => done(250);
  currentAudio.play().catch((err) => {
    console.warn("Audio playback failed:", err);
    done(250);
  });
}

// Tap the status pill to stop Vaani talking
if (statusPill) {
  statusPill.onclick = () => {
    if (speaking && currentAudio) {
      currentAudio.pause();
      currentAudio = null;
      speaking = false;
      listenAgain(300);
    }
  };
}

function handleReply(data) {
  removeTyping();
  if (data.language) setLanguage(data.language);
  if (data.response) addMessage(data.response, "bot");
  playReply(data);
}

// ---------- Server calls ----------
async function resetServerState() {
  try {
    await fetch("/reset_state", { method: "POST", headers: { "Content-Type": "application/json" } });
  } catch (err) {
    console.warn("Could not reset server state:", err);
  }
}

async function sendMessage(message) {
  busy = true;
  stopBrowserListening();
  showTyping();
  playChime();
  try {
    const res = await fetch("/send_message", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    if (!res.ok) throw new Error("Server error " + res.status);
    handleReply(await res.json());
  } catch (err) {
    console.error(err);
    removeTyping();
    addMessage("Network Error: Could not reach the AI.", "error-msg");
    listenAgain(350);
  } finally {
    busy = false;
  }
}

async function sendAudio(wavBlob) {
  busy = true;
  showTyping();
  playChime();
  try {
    const form = new FormData();
    form.append("audio", wavBlob, "speech.wav");
    const res = await fetch("/voice", { method: "POST", body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Server error " + res.status);
    if (!data.transcript) {          // silence or noise: just keep listening
      removeTyping();
      listenAgain(150);
      return;
    }
    addMessage(data.transcript, "user");
    handleReply(data);
  } catch (err) {
    console.error(err);
    removeTyping();
    showError("I could not hear that clearly. Please try again.");
    listenAgain(400);
  } finally {
    busy = false;
  }
}

// ---------- Engine 1: microphone recorder + voice detection (Gnani Prisma) ----------
const SAMPLE_RATE = 16000;
const START_FRAMES = 6;              // ~50 ms of speech to start an utterance
const END_SILENCE_MS = 900;          // stop after this much silence
const PRE_ROLL_MS = 300;             // keep audio from just before speech started
let maxUtteranceMs = 25000;

let audioCtx = null;
let micStream = null;
let workletNode = null;
let noiseFloor = 0.01;
let inSpeech = false;
let loudFrames = 0;
let silenceMs = 0;
let utterance = [];
let utteranceMs = 0;
let preRoll = [];

function rms(frame) {
  let sum = 0;
  for (let i = 0; i < frame.length; i++) sum += frame[i] * frame[i];
  return Math.sqrt(sum / frame.length);
}

function resetUtterance() {
  inSpeech = false;
  loudFrames = 0;
  silenceMs = 0;
  utterance = [];
  utteranceMs = 0;
}

function onFrame(frame) {
  if (!started || busy || speaking || Date.now() < ignoreInputUntil) {
    resetUtterance();
    preRoll = [];
    return;
  }
  const frameMs = (frame.length / audioCtx.sampleRate) * 1000;
  const level = rms(frame);
  const threshold = Math.max(0.012, noiseFloor * 3);

  if (!inSpeech) {
    preRoll.push(frame);
    let preMs = preRoll.length * frameMs;
    while (preMs > PRE_ROLL_MS) { preRoll.shift(); preMs -= frameMs; }
    if (level > threshold) {
      loudFrames += 1;
      if (loudFrames >= START_FRAMES) {
        inSpeech = true;
        utterance = preRoll.slice();
        utteranceMs = utterance.length * frameMs;
        preRoll = [];
        setStatus("hearing");
      }
    } else {
      loudFrames = 0;
      noiseFloor = noiseFloor * 0.95 + level * 0.05;   // adapt to the room
    }
    return;
  }

  utterance.push(frame);
  utteranceMs += frameMs;
  silenceMs = level > threshold ? 0 : silenceMs + frameMs;
  if (silenceMs >= END_SILENCE_MS || utteranceMs >= maxUtteranceMs) {
    const frames = utterance;
    resetUtterance();
    void sendAudio(encodeWav(frames, audioCtx.sampleRate));
  }
}

function encodeWav(frames, sampleRate) {
  const length = frames.reduce((n, f) => n + f.length, 0);
  const buffer = new ArrayBuffer(44 + length * 2);
  const view = new DataView(buffer);
  const writeString = (offset, s) => { for (let i = 0; i < s.length; i++) view.setUint8(offset + i, s.charCodeAt(i)); };
  writeString(0, "RIFF");
  view.setUint32(4, 36 + length * 2, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);              // PCM
  view.setUint16(22, 1, true);              // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, "data");
  view.setUint32(40, length * 2, true);
  let offset = 44;
  for (const frame of frames) {
    for (let i = 0; i < frame.length; i++, offset += 2) {
      const s = Math.max(-1, Math.min(1, frame[i]));
      view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
  }
  return new Blob([buffer], { type: "audio/wav" });
}

async function startRecorder() {
  micStream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
  });
  audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: SAMPLE_RATE });
  await audioCtx.audioWorklet.addModule("/static/js/recorder-worklet.js");
  const source = audioCtx.createMediaStreamSource(micStream);
  workletNode = new AudioWorkletNode(audioCtx, "vaani-recorder");
  workletNode.port.onmessage = (ev) => onFrame(ev.data);
  source.connect(workletNode);
  resetUtterance();
}

function stopRecorder() {
  resetUtterance();
  if (workletNode) { workletNode.port.onmessage = null; workletNode.disconnect(); workletNode = null; }
  if (micStream) { micStream.getTracks().forEach((t) => t.stop()); micStream = null; }
  if (audioCtx) { audioCtx.close(); audioCtx = null; }
}

// ---------- Engine 2: browser speech recognition (fallback) ----------
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const rec = SR ? new SR() : null;
let recognitionActive = false;
let listenTimer = null;
let lastUtterance = "";
let lastUtteranceAt = 0;

function scheduleBrowserListening(delayMs = 0) {
  if (listenTimer) clearTimeout(listenTimer);
  listenTimer = setTimeout(() => {
    listenTimer = null;
    if (!rec || !started || speaking || busy || recognitionActive) return;
    try { rec.start(); } catch (e) { scheduleBrowserListening(300); }
  }, Math.max(0, delayMs));
}

function stopBrowserListening() {
  if (listenTimer) { clearTimeout(listenTimer); listenTimer = null; }
  if (rec && recognitionActive) { try { rec.stop(); } catch (e) {} }
}

if (rec) {
  rec.lang = "en-IN";
  rec.continuous = false;
  rec.interimResults = false;
  rec.onstart = () => { recognitionActive = true; if (!busy && !speaking) setStatus("listening"); };
  rec.onerror = (ev) => {
    recognitionActive = false;
    if (["not-allowed", "service-not-allowed", "audio-capture"].includes(ev.error)) {
      showError("Microphone access denied or unavailable. Please enable it in browser settings.");
      endCall(false);
      return;
    }
    if (started && ev.error !== "aborted") scheduleBrowserListening(ev.error === "network" ? 1200 : 400);
  };
  rec.onresult = (ev) => {
    if (speaking || busy || Date.now() < ignoreInputUntil) return;
    const result = ev.results[ev.results.length - 1];
    if (!result || !result.isFinal) return;
    const text = (result[0].transcript || "").replace(/\s+/g, " ").trim();
    const now = Date.now();
    if (text.length < 2 || (text.toLowerCase() === lastUtterance && now - lastUtteranceAt < 2500)) return;
    lastUtterance = text.toLowerCase();
    lastUtteranceAt = now;
    addMessage(text, "user");
    void sendMessage(text);
  };
  rec.onend = () => {
    recognitionActive = false;
    if (started && !speaking && !busy) scheduleBrowserListening(Math.max(150, ignoreInputUntil - Date.now()));
  };
}

// ---------- Call controls ----------
async function loadVoiceConfig() {
  try {
    const cfg = await (await fetch("/voice/config")).json();
    mode = cfg.stt === "gnani" && window.AudioWorkletNode ? "gnani" : "browser";
    if (cfg.max_seconds) maxUtteranceMs = cfg.max_seconds * 1000;
  } catch (e) {
    mode = "browser";
  }
  if (mode === "browser" && !rec) {
    showError("Speech recognition is not available in this browser. Try Chrome or Edge.");
    if (startBtn) startBtn.disabled = true;
  }
}

async function startCall() {
  if (started) return;
  try {
    if (mode === "gnani") await startRecorder();
    else await navigator.mediaDevices.getUserMedia({ audio: true }).then((s) => s.getTracks().forEach((t) => t.stop()));
  } catch (err) {
    console.warn("Microphone error:", err);
    showError("Please allow microphone access to talk to VAANI.");
    return false;
  }
  started = true;
  startBtn.disabled = true;
  endBtn.disabled = false;
  hidePlaceholder();
  await resetServerState();
  return true;
}

function endCall(announce = true) {
  started = false;
  busy = false;
  if (currentAudio) { currentAudio.pause(); currentAudio = null; }
  speaking = false;
  stopBrowserListening();
  stopRecorder();
  startBtn.disabled = false;
  endBtn.disabled = true;
  void resetServerState();
  if (announce) addMessage("Call ended.", "bot");
  setStatus("");
}

if (startBtn) {
  startBtn.onclick = async () => {
    if (!(await startCall())) return;
    setStatus("processing");
    try {
      const res = await fetch("/send_message", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: "__WELCOME__" }),
      });
      if (!res.ok) throw new Error("Server connection rejected");
      handleReply(await res.json());
    } catch (err) {
      showError("Failed to wake up VAANI server.");
      endCall(false);
    }
  };
}

if (endBtn) endBtn.onclick = () => endCall(true);

// Quick chips send typed text through the same pipeline
window.sendChip = async (text) => {
  const canonical = {
    "book table": "I want to book a table.",
    "hours": "What are your opening hours and timings?",
    "menu": "Please share your menu highlights with prices.",
    "cancel": "I want to cancel my reservation.",
  };
  const query = canonical[(text || "").toLowerCase()] || text;
  if (!started && !(await startCall())) return;
  if (busy || speaking) return;
  addMessage(text, "user");
  await sendMessage(query);
};

void loadVoiceConfig();
setStatus("");
