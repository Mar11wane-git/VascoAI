/*
 * Vasco voice — network worker.
 *
 * Owns the WebSocket to Gemini Live so that receiving Vasco's voice never waits for the page
 * (animations, React rendering, a slow phone). Audio is decoded here and handed straight to the
 * audio-thread player through a MessagePort; only small events (transcripts, tool calls, turn ends)
 * go to the page.
 *
 * page -> worker : {type:'connect', url, token, model, port?} | {type:'audio', buf} | {type:'json', text}
 *                  | {type:'drop'} | {type:'close'}
 * worker -> page : {type:'msg', msg} | {type:'pcm', buf} (only without a player port) | {type:'closed', code}
 */
let ws = null;
let player = null;
let drop = false;
const decoder = new TextDecoder();

function b64ToFloat32(b64) {
  const s = atob(b64);
  const n = s.length >> 1;
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    let v = s.charCodeAt(2 * i) | (s.charCodeAt(2 * i + 1) << 8);
    if (v >= 32768) v -= 65536;
    out[i] = v / 32768;
  }
  return out;
}

function b64ToBuf(b64) {
  const s = atob(b64);
  const u8 = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) u8[i] = s.charCodeAt(i);
  return u8.buffer;
}

function bufToB64(buf) {
  const u8 = new Uint8Array(buf);
  let s = '';
  for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
  return btoa(s);
}

function open(url, token, model) {
  ws = new WebSocket(`${url}?access_token=${encodeURIComponent(token)}`);
  ws.binaryType = 'arraybuffer';
  ws.onopen = () => ws.send(JSON.stringify({ setup: { model: `models/${model}` } }));
  ws.onerror = () => {};
  ws.onclose = (e) => self.postMessage({ type: 'closed', code: e.code });
  ws.onmessage = (e) => {
    let msg;
    try {
      msg = JSON.parse(typeof e.data === 'string' ? e.data : decoder.decode(e.data));
    } catch {
      return;
    }
    const sc = msg.serverContent;
    if (sc && sc.modelTurn) {
      for (const part of sc.modelTurn.parts || []) {
        const b64 = part.inlineData && part.inlineData.data;
        if (!b64 || drop) continue;
        if (player) {
          const f = b64ToFloat32(b64);
          player.postMessage(f, [f.buffer]);
        } else {
          const buf = b64ToBuf(b64);
          self.postMessage({ type: 'pcm', buf }, [buf]);
        }
      }
      delete sc.modelTurn; // the page doesn't need the audio payload
    }
    if (sc && (sc.turnComplete || sc.interrupted)) {
      drop = false;
      if (sc.turnComplete && player) player.postMessage('flush');
    }
    const useful =
      msg.setupComplete || msg.sessionResumptionUpdate || msg.goAway || msg.toolCall ||
      (sc && (sc.inputTranscription || sc.outputTranscription || sc.interrupted || sc.turnComplete));
    if (useful) self.postMessage({ type: 'msg', msg });
  };
}

self.onmessage = (e) => {
  const d = e.data || {};
  if (d.type === 'connect') {
    player = d.port || null;
    open(d.url, d.token, d.model);
  } else if (d.type === 'audio') {
    if (ws && ws.readyState === 1) {
      ws.send(JSON.stringify({ realtimeInput: { audio: { data: bufToB64(d.buf), mimeType: 'audio/pcm;rate=16000' } } }));
    }
  } else if (d.type === 'json') {
    if (ws && ws.readyState === 1) ws.send(d.text);
  } else if (d.type === 'drop') {
    drop = true;
  } else if (d.type === 'close') {
    try {
      if (ws) ws.close();
      else self.postMessage({ type: 'closed', code: 1000 });
    } catch {}
  }
};
