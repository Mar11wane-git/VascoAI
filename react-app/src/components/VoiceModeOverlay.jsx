import React, { useEffect, useRef, useState } from 'react';
import { apiFetch } from '../lib/api.js';

/*
 * Live voice conversation.
 *
 *   mic ──► voice-activity detection (in the browser) ──► WAV 16 kHz ──► /api/voice/transcribe (Gemini)
 *       ──► /api/voice/reply (agent, spoken style, Darija-aware) ──► /api/tts (Moroccan / French voice)
 *
 * Without a Gemini key on the server, the browser's own speech recognition is used instead.
 *
 * LIVE MODE (preferred): a WebSocket to /api/voice/live relays Gemini Live — speech in, speech out,
 * ~0.5 s to answer, natural Darija voice, and the user can interrupt Vasco at any moment.
 * The pipeline above is kept as the fallback when Live is unavailable (e.g. quota reached).
 */

const TARGET_RATE = 16000;
const SILENCE_END_MS = 1000; // pause that ends a sentence
const MIN_SPEECH_MS = 280; // shorter blips are treated as noise
const MAX_UTTERANCE_MS = 20000;
const NOISE_WINDOW = 70; // ~3 s of frames used to estimate the room's background noise
const CALIBRATION_MS = 500; // noise is measured before speech can trigger
const PRE_ROLL_MS = 350; // audio kept from just before speech was detected
const HISTORY_TURNS = 12;

const VOICES = {
  male: { label: 'صوت راجل', icon: '👨' },
  female: { label: 'صوت مرا', icon: '👩' },
};

const LIVE_STATUS = {
  connecting: '⚡ كنتاصل بفاسكو... / Connexion à Vasco…',
  listening: '🎙️ هضر معايا عادي — تقدر تقاطعني فأي وقت / Parlez librement, vous pouvez m’interrompre',
  speaking: '🔊 فاسكو كيهضر — هضر باش تقاطعو / Parlez pour l’interrompre',
  thinking: '🔎 فاسكو كيقلب ليك... / Vasco cherche…',
};

// Float32 mic frame -> 16 kHz PCM16 (silence when gated, to keep the stream's timing).
function toPcm16k(frame, rate, silent) {
  const ratio = rate / 16000;
  const n = Math.floor(frame.length / ratio);
  const out = new Int16Array(n);
  if (!silent) {
    for (let i = 0; i < n; i++) {
      const a = Math.floor(i * ratio);
      const b = Math.min(frame.length, Math.floor((i + 1) * ratio));
      let sum = 0;
      for (let j = a; j < b; j++) sum += frame[j];
      const v = Math.max(-1, Math.min(1, sum / Math.max(1, b - a)));
      out[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
    }
  }
  return out.buffer;
}

const STATUS = {
  connecting: '⚡ كنتاصل بفاسكو... / Connexion à Vasco…',
  starting: '⏳ كنوجد الميكرو... / Préparation du micro…',
  listening: '🎙️ هضر، أنا كنسمع ليك — ولا نقر على الكرة / Parlez, ou touchez la sphère',
  recording: '🔴 كنسمع... سكت باش نصيفط، ولا نقر على الكرة / Je vous écoute… (touchez pour envoyer)',
  transcribing: '✍️ كنفهم شنو قلتي... / Transcription…',
  thinking: '🧠 فاسكو كيفكر... / Vasco réfléchit…',
  speaking: '🔊 فاسكو كيجاوبك — نقر على الكرة باش تقاطعو / Touchez la sphère pour l’interrompre',
  error: '⚠️',
};

function encodeWav(chunks, inputRate) {
  const total = chunks.reduce((n, c) => n + c.length, 0);
  const ratio = inputRate / TARGET_RATE;
  const outLen = Math.floor(total / ratio);
  const merged = new Float32Array(total);
  let off = 0;
  for (const c of chunks) {
    merged.set(c, off);
    off += c.length;
  }
  // Downsample by averaging (a cheap low-pass) then convert to 16-bit PCM.
  const pcm = new Int16Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const start = Math.floor(i * ratio);
    const end = Math.min(total, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = start; j < end; j++) sum += merged[j];
    const s = Math.max(-1, Math.min(1, sum / Math.max(1, end - start)));
    pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  const buf = new ArrayBuffer(44 + pcm.length * 2);
  const v = new DataView(buf);
  const str = (o, s) => [...s].forEach((ch, i) => v.setUint8(o + i, ch.charCodeAt(0)));
  str(0, 'RIFF');
  v.setUint32(4, 36 + pcm.length * 2, true);
  str(8, 'WAVE');
  str(12, 'fmt ');
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true);
  v.setUint16(22, 1, true);
  v.setUint32(24, TARGET_RATE, true);
  v.setUint32(28, TARGET_RATE * 2, true);
  v.setUint16(32, 2, true);
  v.setUint16(34, 16, true);
  str(36, 'data');
  v.setUint32(40, pcm.length * 2, true);
  new Int16Array(buf, 44).set(pcm);
  return new Blob([buf], { type: 'audio/wav' });
}

function micErrorMessage(err) {
  if (!window.isSecureContext) return 'الميكرو خاصو HTTPS ولا localhost. / Le micro exige HTTPS (ou localhost).';
  switch (err?.name) {
    case 'NotAllowedError':
    case 'SecurityError':
      return 'ما عطيتيش الإذن للميكرو. فعّلو من القفل 🔒 حدا العنوان. / Autorisez le micro (icône 🔒 de la barre d’adresse).';
    case 'NotFoundError':
      return 'ما لقيت حتى ميكرو. / Aucun micro détecté.';
    case 'NotReadableError':
      return 'الميكرو مستعمل فبرنامج آخر. / Le micro est utilisé par une autre application.';
    default:
      return `مشكل فالميكرو / Erreur micro : ${err?.message || err}`;
  }
}

async function readError(res) {
  try {
    const d = await res.json();
    return d.detail || `Erreur ${res.status}`;
  } catch {
    return `Erreur ${res.status}`;
  }
}

export default function VoiceModeOverlay({
  isOpen,
  onClose,
  selectedProvider,
  selectedModel,
  webSearch,
  systemPrompt,
  conversationMessages,
  onNewVoiceTurnSaved,
}) {
  const [phase, setPhase] = useState('starting');
  const [isMuted, setIsMuted] = useState(false);
  const [caption, setCaption] = useState('');
  const [userTranscript, setUserTranscript] = useState('');
  const [errorText, setErrorText] = useState('');
  const [gender, setGender] = useState(() => {
    try {
      return localStorage.getItem('vasco_voice_gender') || 'male';
    } catch {
      return 'male';
    }
  });
  const [browserLang, setBrowserLang] = useState('ar-MA');
  const [engine, setEngine] = useState('server'); // 'server' (Gemini) | 'browser'
  const [hint, setHint] = useState(''); // '' | 'tap' | 'silent'
  const [mics, setMics] = useState([]);
  const [micId, setMicId] = useState(() => {
    try {
      return localStorage.getItem('vasco_mic_id') || '';
    } catch {
      return '';
    }
  });
  const meterRef = useRef(null);
  const diagRef = useRef({ frames: 0, startedAt: 0, peak: 0, peakSince: 0 });

  const phaseRef = useRef('starting');
  const activeRef = useRef(false);
  const mutedRef = useRef(false);
  const genderRef = useRef(gender);
  const browserLangRef = useRef(browserLang);
  const messagesRef = useRef(conversationMessages || []);
  const propsRef = useRef({});
  propsRef.current = { selectedProvider, selectedModel, webSearch, systemPrompt, onNewVoiceTurnSaved };

  const streamRef = useRef(null);
  const ctxRef = useRef(null);
  const procRef = useRef(null);
  const audioElRef = useRef(null);
  const audioUrlRef = useRef(null);
  const recognitionRef = useRef(null);
  const orbRef = useRef(null);
  const levelRef = useRef(0);
  const rafRef = useRef(0);
  const abortRef = useRef(null);
  const modeRef = useRef('classic'); // 'live' | 'classic'
  const wsRef = useRef(null);
  const playCtxRef = useRef(null);
  const playSourcesRef = useRef(new Set());
  const nextPlayRef = useRef(0);
  const liveRef = useRef({ user: '', vasco: '', closed: true, drop: false });
  const lastGenderRef = useRef(gender);
  const [toolNote, setToolNote] = useState('');
  const vad = useRef({ history: [], noise: 0.004, speechFrames: 0, recording: false, manual: false, chunks: [], preRoll: [], voicedMs: 0, lastVoice: 0, startedAt: 0, listenAt: 0 });

  useEffect(() => {
    messagesRef.current = conversationMessages || [];
  }, [conversationMessages]);
  useEffect(() => {
    genderRef.current = gender;
    try {
      localStorage.setItem('vasco_voice_gender', gender);
    } catch {}
  }, [gender]);
  useEffect(() => {
    browserLangRef.current = browserLang;
  }, [browserLang]);
  useEffect(() => {
    if (lastGenderRef.current === gender) return;
    lastGenderRef.current = gender;
    if (activeRef.current && modeRef.current === 'live' && wsRef.current) reconnectLive();
  }, [gender]);

  const setPhaseBoth = (p) => {
    phaseRef.current = p;
    setPhase(p);
  };

  // ---------------------------------------------------------------- lifecycle
  useEffect(() => {
    if (!isOpen) return undefined;
    activeRef.current = true;
    mutedRef.current = false;
    setIsMuted(false);
    setErrorText('');
    setUserTranscript('');
    const lastAssistant = [...messagesRef.current].reverse().find((m) => m.role === 'assistant');
    setCaption(
      lastAssistant?.content
        ? lastAssistant.content.replace(/[#*_`~>|]/g, '').slice(0, 200)
        : 'مرحبا بيك! هضر معايا بالدارجة، بالعربية، بالفرنسية ولا بالإنجليزية.'
    );
    // Created during the click that opened the overlay, so later playback isn't blocked.
    audioElRef.current = new Audio();
    start();
    const tick = () => {
      if (orbRef.current) {
        const lvl = levelRef.current;
        const p = phaseRef.current;
        const scale = p === 'recording' || p === 'listening' ? 1 + Math.min(0.22, lvl * 3.2) : 1;
        orbRef.current.style.transform = `scale(${scale.toFixed(3)})`;
      }
      if (meterRef.current) {
        const v = vad.current;
        const lvl = Math.min(1, levelRef.current / 0.12);
        const thr = Math.min(1, Math.max(0.006, v.noise * 2.6 + 0.003) / 0.12);
        meterRef.current.style.setProperty('--lvl', `${(lvl * 100).toFixed(1)}%`);
        meterRef.current.style.setProperty('--thr', `${(thr * 100).toFixed(1)}%`);
      }
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    // Watchdog: tell the user *why* nothing happens instead of staying silent.
    const watchdog = setInterval(() => {
      const d = diagRef.current;
      const ctx = ctxRef.current;
      if (!ctx || !d.startedAt || recognitionRef.current) return setHint('');
      const now = performance.now();
      if (ctx.state !== 'running' || (d.frames === 0 && now - d.startedAt > 1500)) {
        ctx.resume().catch(() => {});
        return setHint('tap');
      }
      if (now - d.startedAt > 4000 && d.peak < 0.0015 && now - d.peakSince > 4000) return setHint('silent');
      setHint('');
    }, 500);
    return () => {
      activeRef.current = false;
      clearInterval(watchdog);
      cancelAnimationFrame(rafRef.current);
      teardown();
    };
  }, [isOpen]);

  const teardown = () => {
    abortRef.current?.abort();
    closeLive();
    stopPlayback();
    try {
      recognitionRef.current?.abort();
    } catch {}
    recognitionRef.current = null;
    if (procRef.current) {
      procRef.current.onaudioprocess = null;
      try {
        procRef.current.disconnect();
      } catch {}
    }
    procRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    ctxRef.current?.close().catch(() => {});
    ctxRef.current = null;
  };

  const start = async () => {
    setPhaseBoth('starting');
    let serverStt = false;
    let live = false;
    try {
      const cfg = await (await fetch('/api/voice/config')).json();
      serverStt = Boolean(cfg.server_stt);
      live = Boolean(cfg.live);
    } catch {}
    if (!activeRef.current) return;
    if (live && (await startLive())) return;
    if (!activeRef.current) return;

    const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!serverStt) {
      if (!SpeechRec) {
        fail('ضيف مفتاح Gemini فـ « Clés API » باش يخدم الصوت فهاد المتصفح. / Ajoutez une clé Gemini pour activer la voix dans ce navigateur.');
        return;
      }
      setEngine('browser');
      startBrowserRecognition();
      return;
    }
    setEngine('server');
    await openMic(micId);
  };

  // ------------------------------------------------------------- live mode
  const startLive = async () => {
    modeRef.current = 'live';
    setEngine('live');
    setPhaseBoth('connecting');
    const Ctx = window.AudioContext || window.webkitAudioContext;
    try {
      playCtxRef.current = new Ctx({ sampleRate: 24000 });
    } catch {
      playCtxRef.current = new Ctx();
    }
    playCtxRef.current.resume().catch(() => {});
    const ok = await connectLive();
    if (!activeRef.current) return true;
    if (!ok) {
      closeLive();
      modeRef.current = 'classic';
      setEngine('server');
      setCaption('الوضع المباشر ما خدامش دابا، غادي نخدم بالطريقة العادية. / Mode direct indisponible : mode classique.');
      return false;
    }
    await openMic(micId);
    return true;
  };

  const connectLive = () =>
    new Promise((resolve) => {
      const token = localStorage.getItem('vasco_auth_token') || '';
      const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
      const ws = new WebSocket(`${proto}://${window.location.host}/api/voice/live?token=${encodeURIComponent(token)}`);
      ws.binaryType = 'arraybuffer';
      wsRef.current = ws;
      let settled = false;
      const settle = (v) => {
        if (!settled) {
          settled = true;
          resolve(v);
        }
      };
      const timer = setTimeout(() => settle(false), 15000);
      ws.onopen = () => {
        const { webSearch: web, systemPrompt: sys } = propsRef.current;
        const history = messagesRef.current
          .filter((m) => m.content && (m.role === 'user' || m.role === 'assistant'))
          .slice(-10)
          .map((m) => ({ role: m.role, content: m.content }));
        ws.send(JSON.stringify({ type: 'start', voice: genderRef.current, web_search: web, system_prompt: sys || '', history }));
      };
      ws.onmessage = (e) => {
        if (typeof e.data !== 'string') {
          playPcm(e.data);
          return;
        }
        let d;
        try {
          d = JSON.parse(e.data);
        } catch {
          return;
        }
        if (d.type === 'ready') {
          clearTimeout(timer);
          liveRef.current = { user: '', vasco: '', closed: true, drop: false };
          settle(true);
        } else if (d.type === 'error') {
          clearTimeout(timer);
          if (settled) {
            setErrorText(d.message);
            setPhaseBoth('error');
          }
          settle(false);
        } else {
          onLiveEvent(d);
        }
      };
      ws.onclose = () => {
        clearTimeout(timer);
        settle(false);
        if (wsRef.current === ws && activeRef.current && modeRef.current === 'live') {
          wsRef.current = null;
          stopLivePlayback();
          setErrorText('انقطع الاتصال مع فاسكو. نقر على الكرة باش تعاود. / Connexion perdue : touchez la sphère.');
          setPhaseBoth('error');
        }
      };
    });

  const reconnectLive = async () => {
    const old = wsRef.current;
    wsRef.current = null;
    try {
      old?.close();
    } catch {}
    stopLivePlayback();
    setPhaseBoth('connecting');
    const ok = await connectLive();
    if (!activeRef.current) return;
    if (ok) setPhaseBoth('listening');
    else {
      setErrorText('ما قدرتش نتاصل بفاسكو. نقر على الكرة باش تعاود. / Reconnexion impossible : touchez la sphère.');
      setPhaseBoth('error');
    }
  };

  const onLiveEvent = (d) => {
    const L = liveRef.current;
    switch (d.type) {
      case 'user_text':
        if (L.closed) {
          L.user = '';
          L.vasco = '';
          L.closed = false;
          L.drop = false;
        }
        L.user += d.text;
        setUserTranscript(L.user.trim());
        break;
      case 'vasco_text':
        if (L.closed) {
          L.vasco = '';
          L.closed = false;
        }
        L.vasco += d.text;
        setToolNote('');
        setCaption(L.vasco.trim());
        break;
      case 'tool':
        setToolNote(`${d.icon || '🛠️'} ${d.label || d.name}…`);
        if (phaseRef.current !== 'speaking') setPhaseBoth('thinking');
        break;
      case 'interrupted':
        stopLivePlayback();
        setPhaseBoth('listening');
        break;
      case 'turn_complete':
        L.drop = false;
        // A tool call closes an empty turn first: only save turns where Vasco actually spoke.
        if (L.vasco.trim()) {
          propsRef.current.onNewVoiceTurnSaved?.(L.user.trim() || '🎙️', { role: 'assistant', content: L.vasco.trim() });
          L.closed = true;
        }
        break;
      case 'reconnect':
        reconnectLive();
        break;
      default:
    }
  };

  // Gapless playback of Vasco's 24 kHz speech chunks.
  const playPcm = (buf) => {
    if (liveRef.current.drop) return;
    const ctx = playCtxRef.current;
    if (!ctx || !buf.byteLength) return;
    const i16 = new Int16Array(buf.byteLength % 2 ? buf.slice(0, buf.byteLength - 1) : buf);
    const ab = ctx.createBuffer(1, i16.length, 24000);
    const ch = ab.getChannelData(0);
    for (let i = 0; i < i16.length; i++) ch[i] = i16[i] / 32768;
    const src = ctx.createBufferSource();
    src.buffer = ab;
    src.connect(ctx.destination);
    const t = Math.max(ctx.currentTime + 0.04, nextPlayRef.current);
    src.start(t);
    nextPlayRef.current = t + ab.duration;
    const set = playSourcesRef.current;
    set.add(src);
    src.onended = () => {
      set.delete(src);
      if (!set.size && phaseRef.current === 'speaking') setPhaseBoth('listening');
    };
    if (phaseRef.current !== 'speaking') setPhaseBoth('speaking');
  };

  const stopLivePlayback = () => {
    for (const src of playSourcesRef.current) {
      src.onended = null;
      try {
        src.stop();
      } catch {}
    }
    playSourcesRef.current.clear();
    nextPlayRef.current = 0;
  };

  const closeLive = () => {
    const ws = wsRef.current;
    wsRef.current = null;
    if (ws) {
      try {
        if (ws.readyState === 1) ws.send(JSON.stringify({ type: 'stop' }));
        ws.close();
      } catch {}
    }
    stopLivePlayback();
    playCtxRef.current?.close().catch(() => {});
    playCtxRef.current = null;
  };

  const openMic = async (deviceId) => {
    if (!navigator.mediaDevices?.getUserMedia) {
      fail(micErrorMessage({ name: 'NotSupported', message: 'getUserMedia indisponible' }));
      return;
    }
    // Release the previous device, if any.
    if (procRef.current) {
      procRef.current.onaudioprocess = null;
      try {
        procRef.current.disconnect();
      } catch {}
      procRef.current = null;
    }
    streamRef.current?.getTracks().forEach((t) => t.stop());
    try {
      const audio = { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 };
      if (deviceId) audio.deviceId = { exact: deviceId };
      let stream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio });
      } catch (err) {
        if (!deviceId || err.name !== 'OverconstrainedError') throw err;
        delete audio.deviceId; // saved mic unplugged: fall back to the default one
        stream = await navigator.mediaDevices.getUserMedia({ audio });
      }
      if (!activeRef.current) {
        stream.getTracks().forEach((t) => t.stop());
        return;
      }
      streamRef.current = stream;
      navigator.mediaDevices
        .enumerateDevices()
        .then((list) => setMics(list.filter((d) => d.kind === 'audioinput')))
        .catch(() => {});
      let ctx = ctxRef.current;
      if (!ctx || ctx.state === 'closed') {
        const Ctx = window.AudioContext || window.webkitAudioContext;
        const rate = stream.getAudioTracks()[0]?.getSettings?.().sampleRate;
        try {
          ctx = new Ctx(rate ? { sampleRate: rate } : undefined); // Firefox needs the mic's own rate
        } catch {
          ctx = new Ctx();
        }
        ctxRef.current = ctx;
      }
      await ctx.resume().catch(() => {});
      const src = ctx.createMediaStreamSource(stream);
      const proc = ctx.createScriptProcessor(2048, 1, 1);
      proc.onaudioprocess = (e) => onFrame(e.inputBuffer.getChannelData(0), ctx.sampleRate);
      src.connect(proc);
      proc.connect(ctx.destination); // outputs silence; needed for the callback to run
      procRef.current = proc;
      diagRef.current = { frames: 0, startedAt: performance.now(), peak: 0, peakSince: performance.now() };
      vad.current.history = [];
      listen();
    } catch (err) {
      fail(micErrorMessage(err));
    }
  };

  const chooseMic = (id) => {
    setMicId(id);
    try {
      localStorage.setItem('vasco_mic_id', id);
    } catch {}
    setHint('');
    if (engine === 'server') openMic(id);
  };

  const fail = (msg) => {
    setErrorText(msg);
    setPhaseBoth('error');
  };

  const listen = () => {
    if (!activeRef.current) return;
    const v = vad.current;
    v.recording = false;
    v.chunks = [];
    v.preRoll = [];
    v.speechFrames = 0;
    v.voicedMs = 0;
    v.manual = false;
    v.listenAt = performance.now();
    setErrorText('');
    if (mutedRef.current) return;
    if (recognitionRef.current) {
      setPhaseBoth('listening');
      try {
        recognitionRef.current.lang = browserLangRef.current;
        recognitionRef.current.start();
      } catch {}
      return;
    }
    setPhaseBoth('listening');
  };

  // ------------------------------------------------- voice activity detection
  const onFrame = (input, rate) => {
    const frame = new Float32Array(input); // the buffer is reused by the browser
    let sum = 0;
    for (let i = 0; i < frame.length; i++) sum += frame[i] * frame[i];
    const rms = Math.sqrt(sum / frame.length);
    levelRef.current = levelRef.current * 0.6 + rms * 0.4;

    const now = performance.now();
    const d = diagRef.current;
    d.frames += 1;
    if (rms > d.peak) d.peak = rms;
    if (rms > 0.0015) d.peakSince = now;

    // Background noise = a low percentile of the last ~3 s, so a fan or an amplified (AGC) mic
    // raises the threshold instead of being mistaken for endless speech.
    const v = vad.current;
    v.history.push(rms);
    if (v.history.length > NOISE_WINDOW) v.history.shift();
    const sorted = [...v.history].sort((a, b) => a - b);
    v.noise = sorted[Math.floor(sorted.length * 0.2)] || 0.004;
    const threshold = Math.max(0.006, v.noise * 2.6 + 0.003);

    if (modeRef.current === 'live') {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== 1 || mutedRef.current) return;
      // While Vasco talks, quiet sound is probably his own voice from the speakers:
      // send silence unless the user clearly speaks over him (barge-in).
      const gate = playSourcesRef.current.size > 0 && rms < Math.max(0.02, v.noise * 3.5);
      ws.send(toPcm16k(frame, rate, gate));
      return;
    }

    const p = phaseRef.current;
    if (mutedRef.current || (p !== 'listening' && p !== 'recording')) return;

    const frameMs = (frame.length / rate) * 1000;

    if (!v.recording) {
      if (now - v.listenAt < CALIBRATION_MS) {
        v.preRoll.push(frame);
        if (v.preRoll.length > Math.ceil(PRE_ROLL_MS / frameMs)) v.preRoll.shift();
        return;
      }
      if (rms > threshold) {
        v.speechFrames += 1;
      } else {
        v.speechFrames = 0;
      }
      v.preRoll.push(frame);
      const maxPre = Math.ceil(PRE_ROLL_MS / frameMs);
      if (v.preRoll.length > maxPre) v.preRoll.shift();
      if (v.speechFrames >= 2) {
        v.recording = true;
        v.chunks = [...v.preRoll];
        v.voicedMs = v.speechFrames * frameMs;
        v.lastVoice = now;
        v.startedAt = now;
        setPhaseBoth('recording');
      }
      return;
    }

    v.chunks.push(frame);
    if (rms > threshold * 0.8) {
      v.lastVoice = now;
      v.voicedMs += frameMs;
    }
    // In manual mode (sphere tapped) only a second tap or the time limit ends the recording.
    const silenceEnd = !v.manual && now - v.lastVoice > SILENCE_END_MS;
    if (silenceEnd || now - v.startedAt > MAX_UTTERANCE_MS) {
      const chunks = v.chunks;
      v.recording = false;
      v.chunks = [];
      if (!v.manual && v.voicedMs < MIN_SPEECH_MS) {
        listen();
        return;
      }
      submitAudio(encodeWav(chunks, rate));
    }
  };

  // Push-to-talk: works even when automatic detection can't tell voice from noise.
  const startManualRecording = () => {
    const v = vad.current;
    v.recording = true;
    v.manual = true;
    v.chunks = [...v.preRoll];
    v.voicedMs = 0;
    v.startedAt = performance.now();
    v.lastVoice = v.startedAt;
    setPhaseBoth('recording');
  };

  const finishRecording = () => {
    const v = vad.current;
    if (!v.recording) return;
    const chunks = v.chunks;
    v.recording = false;
    v.chunks = [];
    const total = chunks.reduce((n, c) => n + c.length, 0);
    const rate = ctxRef.current?.sampleRate || 48000;
    if (total < rate * 0.3) {
      listen();
      return;
    }
    // Pure silence: the speech model would invent a sentence, so don't send it.
    let peak = 0;
    for (const c of chunks) for (let i = 0; i < c.length; i += 4) peak = Math.max(peak, Math.abs(c[i]));
    if (peak < 0.01) {
      setCaption('ما وصلني حتى صوت من الميكرو. / Aucun son n’a été capté par le micro.');
      setHint('silent');
      listen();
      return;
    }
    submitAudio(encodeWav(chunks, rate));
  };

  // --------------------------------------------- browser recognition fallback
  const startBrowserRecognition = () => {
    const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
    const rec = new SpeechRec();
    rec.continuous = false;
    rec.interimResults = true;
    let finalText = '';
    rec.onresult = (e) => {
      let text = '';
      for (let i = 0; i < e.results.length; i++) text += e.results[i][0].transcript;
      finalText = text.trim();
      setPhaseBoth('recording');
      setUserTranscript(finalText);
    };
    rec.onend = () => {
      if (!activeRef.current || mutedRef.current) return;
      const spoken = finalText;
      finalText = '';
      if (spoken && phaseRef.current === 'recording') handleTranscript(spoken);
      else if (phaseRef.current === 'listening' || phaseRef.current === 'recording') setTimeout(listen, 300);
    };
    rec.onerror = (e) => {
      if (e.error === 'not-allowed' || e.error === 'service-not-allowed') fail(micErrorMessage({ name: 'NotAllowedError' }));
    };
    recognitionRef.current = rec;
    listen();
  };

  // ----------------------------------------------------------- conversation
  const submitAudio = async (wav) => {
    setPhaseBoth('transcribing');
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      const form = new FormData();
      form.append('audio', wav, 'speech.wav');
      const res = await apiFetch('/api/voice/transcribe', { method: 'POST', body: form, signal: ctrl.signal });
      if (!res.ok) throw new Error(await readError(res));
      const data = await res.json();
      if (!activeRef.current) return;
      if (!data.text) {
        setUserTranscript('');
        setCaption('ما سمعتكش مزيان، عاود عافاك. / Je n’ai pas bien entendu, pouvez-vous répéter ?');
        listen();
        return;
      }
      handleTranscript(data.text);
    } catch (err) {
      if (err.name === 'AbortError' || !activeRef.current) return;
      setErrorText(`${err.message || err}`);
      setTimeout(listen, 1800);
    }
  };

  const handleTranscript = async (spoken) => {
    setUserTranscript(spoken);
    setPhaseBoth('thinking');
    const { selectedProvider: provider, selectedModel: model, webSearch: web, systemPrompt: sys, onNewVoiceTurnSaved: save } =
      propsRef.current;
    const history = messagesRef.current
      .filter((m) => m.content && (m.role === 'user' || m.role === 'assistant'))
      .slice(-HISTORY_TURNS)
      .map((m) => ({ role: m.role, content: m.content }));
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      const res = await apiFetch('/api/voice/reply', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: ctrl.signal,
        body: JSON.stringify({
          provider,
          model,
          messages: [...history, { role: 'user', content: spoken }],
          system_prompt: sys || '',
          web_search: web,
          voice: genderRef.current,
          speak: false,
        }),
      });
      if (!res.ok) throw new Error(await readError(res));
      const data = await res.json();
      if (!activeRef.current) return;
      const reply = data.reply || 'سمح ليا، ما فهمتش. عاود عافاك.';
      setCaption(reply);
      save?.(spoken, { role: 'assistant', content: data.display || reply, sources: data.sources || [] });
      await speak(reply, data.lang);
    } catch (err) {
      if (err.name === 'AbortError' || !activeRef.current) return;
      setErrorText(`${err.message || err}`);
      setTimeout(listen, 2200);
    }
  };

  const stopPlayback = () => {
    const a = audioElRef.current;
    if (a) {
      a.onended = null;
      a.onerror = null;
      a.pause();
    }
    if (audioUrlRef.current) URL.revokeObjectURL(audioUrlRef.current);
    audioUrlRef.current = null;
    if ('speechSynthesis' in window) window.speechSynthesis.cancel();
  };

  const speak = async (text, lang) => {
    setPhaseBoth('speaking');
    const done = () => {
      if (activeRef.current && phaseRef.current === 'speaking') listen();
    };
    try {
      const res = await apiFetch('/api/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, voice: genderRef.current }),
      });
      if (!res.ok) throw new Error('tts');
      const blob = await res.blob();
      if (!activeRef.current || phaseRef.current !== 'speaking') return;
      stopPlayback();
      const url = URL.createObjectURL(blob);
      audioUrlRef.current = url;
      const a = audioElRef.current || new Audio();
      audioElRef.current = a;
      a.src = url;
      a.onended = done;
      a.onerror = done;
      await a.play();
    } catch {
      if (!activeRef.current) return;
      if ('speechSynthesis' in window) {
        const u = new SpeechSynthesisUtterance(text);
        u.lang = lang || 'ar-MA';
        u.onend = done;
        u.onerror = done;
        window.speechSynthesis.speak(u);
      } else {
        done();
      }
    }
  };

  // --------------------------------------------------------------- controls
  const handleToggleMute = () => {
    const next = !mutedRef.current;
    mutedRef.current = next;
    setIsMuted(next);
    if (next) {
      vad.current.recording = false;
      try {
        recognitionRef.current?.abort();
      } catch {}
      if (phaseRef.current === 'listening' || phaseRef.current === 'recording') setPhaseBoth('listening');
    } else if (phaseRef.current === 'listening' || phaseRef.current === 'error') {
      listen();
    }
  };

  const handleOrbClick = () => {
    const p = phaseRef.current;
    ctxRef.current?.resume().catch(() => {});
    playCtxRef.current?.resume().catch(() => {});
    if (modeRef.current === 'live') {
      if (p === 'speaking' || p === 'thinking') {
        stopLivePlayback();
        liveRef.current.drop = true; // ignore the rest of this answer
        setPhaseBoth('listening');
      } else if (p === 'error') {
        teardown();
        start();
      }
      return;
    }
    if (!recognitionRef.current && !mutedRef.current && ctxRef.current) {
      if (p === 'listening') return startManualRecording();
      if (p === 'recording') return finishRecording();
    }
    if (p === 'speaking') {
      stopPlayback();
      listen();
    } else if (p === 'thinking' || p === 'transcribing') {
      abortRef.current?.abort();
      listen();
    } else if (p === 'error') {
      teardown();
      start();
    }
  };

  if (!isOpen) return null;

  const orbClass = isMuted
    ? 'muted'
    : phase === 'recording'
    ? 'listening'
    : phase === 'transcribing' || phase === 'connecting' || phase === 'starting'
    ? 'thinking'
    : phase;

  return (
    <div
      className="live-voice-overlay"
      onPointerDown={() => {
        ctxRef.current?.resume().catch(() => {});
        playCtxRef.current?.resume().catch(() => {});
      }}
    >
      <div className="live-voice-top">
        <div className="live-voice-caption" dir="auto">
          {caption}
        </div>
        {userTranscript && (
          <div className="live-voice-user-transcript" dir="auto">
            🗣️ « {userTranscript} »
          </div>
        )}
        {toolNote && <div className="live-voice-tool">{toolNote}</div>}
        {errorText && (
          <div className="live-voice-error" dir="auto">
            ⚠️ {errorText}
          </div>
        )}
        {!errorText && hint === 'tap' && (
          <div className="live-voice-error" dir="auto">
            👆 نقر على الشاشة باش يتفعّل الميكرو / Touchez l’écran pour activer le micro
          </div>
        )}
        {!errorText && hint === 'silent' && (
          <div className="live-voice-error" dir="auto">
            🔇 Chrome ما كيوصلو حتى صوت من هاد الميكرو، ختار الميكرو الصحيح لتحت 👇 / Aucun son reçu de ce micro : choisissez le
            bon micro ci-dessous.
          </div>
        )}
      </div>

      <div className="live-voice-center">
        <div className={`live-voice-orb-container ${orbClass}`} onClick={handleOrbClick} title="Touchez pour interrompre / réessayer">
          <div className="cloud-orb" ref={orbRef}>
            <div className="cloud-layer cloud-sky" />
            <div className="cloud-layer cloud-mist-1" />
            <div className="cloud-layer cloud-mist-2" />
            <div className="cloud-layer cloud-highlight" />
          </div>
          <div className="orb-glow-ring" />
        </div>
        {engine !== 'browser' && phase !== 'error' && (
          <div className="voice-meter" ref={meterRef} title="Niveau du micro (le trait = seuil de détection)">
            <div className="voice-meter-fill" />
            <div className="voice-meter-thr" />
          </div>
        )}
        <div className="live-voice-status">
          {isMuted ? '🔇 الميكرو مقطوع / Micro coupé' : phase === 'error' ? 'نقر على الكرة باش تعاود / Touchez la sphère pour réessayer' : (engine === 'live' && LIVE_STATUS[phase]) || STATUS[phase]}
        </div>
      </div>

      <div className="live-voice-bottom-bar">
        <div className="live-voice-options">
          {engine !== 'browser' && mics.length > 0 && (
            <select
              className={`voice-mic-select ${hint === 'silent' ? 'attention' : ''}`}
              value={micId || streamRef.current?.getAudioTracks()[0]?.getSettings?.().deviceId || ''}
              onChange={(e) => chooseMic(e.target.value)}
              title="Micro utilisé"
            >
              {mics.map((m, i) => (
                <option key={m.deviceId || i} value={m.deviceId}>
                  🎤 {m.label || `Micro ${i + 1}`}
                </option>
              ))}
            </select>
          )}
          <div className="voice-seg" role="group" aria-label="Voix de Vasco">
            {Object.entries(VOICES).map(([key, v]) => (
              <button
                key={key}
                type="button"
                className={`voice-seg-btn ${gender === key ? 'active' : ''}`}
                onClick={() => setGender(key)}
                title={key === 'male' ? 'Voix masculine' : 'Voix féminine'}
              >
                {v.icon} {v.label}
              </button>
            ))}
          </div>
          {engine === 'browser' && (
            <div className="voice-seg" role="group" aria-label="Langue parlée">
              {[
                ['ar-MA', 'دارجة'],
                ['fr-FR', 'FR'],
                ['en-US', 'EN'],
              ].map(([code, label]) => (
                <button
                  key={code}
                  type="button"
                  className={`voice-seg-btn ${browserLang === code ? 'active' : ''}`}
                  onClick={() => setBrowserLang(code)}
                >
                  {label}
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="live-voice-controls">
          <button
            type="button"
            className={`live-ctrl-btn mute-btn ${isMuted ? 'is-muted' : ''}`}
            onClick={handleToggleMute}
            title="Couper / Activer le micro"
          >
            {!isMuted ? (
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
                <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                <line x1="12" x2="12" y1="19" y2="22" />
              </svg>
            ) : (
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="2" x2="22" y1="2" y2="22" />
                <path d="M18.89 13.23A7.12 7.12 0 0 0 19 12v-2" />
                <path d="M5 10v2a7 7 0 0 0 12 5" />
                <path d="M15 9.34V5a3 3 0 0 0-5.68-1.33" />
                <path d="M9 9v3a3 3 0 0 0 5.12 2.12" />
                <line x1="12" x2="12" y1="19" y2="22" />
              </svg>
            )}
          </button>
          <button type="button" className="live-ctrl-btn close-voice-btn" onClick={onClose} title="Quitter le mode vocal">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
      </div>
    </div>
  );
}
