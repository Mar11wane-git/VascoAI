/**
 * Plays and exports the AI reels made by the create_video tool, entirely in the browser (free):
 *   AI images (Pollinations) + voice-over (/api/tts) + Ken Burns motion, crossfades and captions,
 *   drawn on a <canvas>; "Télécharger" records the canvas and the voice with MediaRecorder (MP4 when supported).
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Download, Loader2, Pause, Play, RotateCcw } from 'lucide-react';
import { apiFetch } from '../lib/api.js';
import './reel.css';

const FADE = 0.45;      // crossfade between scenes (s)
const TAIL = 0.35;      // pause after each narration (s)
const MIN_SCENE = 2.4;  // shortest scene (s)
const TITLE_TIME = 2.0; // title banner duration (s)
const ARABIC = /[؀-ۿ]/;

let sharedCtx = null;
function audioCtx() {
  if (!sharedCtx) sharedCtx = new (window.AudioContext || window.webkitAudioContext)();
  return sharedCtx;
}

function loadImage(url, tries = 4) {
  return new Promise((resolve) => {
    let n = 0;
    const attempt = () => {
      const img = new Image();
      img.crossOrigin = 'anonymous'; // needed to record the canvas
      img.onload = () => resolve(img);
      img.onerror = () => (++n < tries ? setTimeout(attempt, 2000 * n) : resolve(null));
      img.src = n ? `${url}&retry=${n}` : url;
    };
    attempt();
  });
}

async function loadVoice(ctx, text, voice) {
  for (let i = 0; i < 3; i++) {
    try {
      const res = await apiFetch('/api/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, voice }),
      });
      if (res.ok) return await ctx.decodeAudioData(await res.arrayBuffer());
    } catch (_) { /* retry */ }
    await new Promise((r) => setTimeout(r, 800 * (i + 1)));
  }
  return null;
}

/** Splits a narration into short caption chunks, timed in proportion to their length. */
function captionChunks(text, duration) {
  const words = text.split(/\s+/).filter(Boolean);
  const chunks = [];
  let cur = [];
  for (const w of words) {
    cur.push(w);
    if (cur.length >= 4 || cur.join(' ').length > 24 || /[.!?؟،,]$/.test(w)) {
      chunks.push(cur.join(' '));
      cur = [];
    }
  }
  if (cur.length) chunks.push(cur.join(' '));
  const total = chunks.reduce((a, c) => a + c.length, 0) || 1;
  let t = 0;
  return chunks.map((c) => {
    const d = (c.length / total) * duration;
    const chunk = { text: c, start: t, end: t + d };
    t += d;
    return chunk;
  });
}

function wrapLines(ctx, text, maxWidth) {
  const words = text.split(' ');
  const lines = [];
  let line = '';
  for (const w of words) {
    const test = line ? `${line} ${w}` : w;
    if (line && ctx.measureText(test).width > maxWidth) {
      lines.push(line);
      line = w;
    } else line = test;
  }
  if (line) lines.push(line);
  return lines;
}

function drawCover(ctx, img, W, H, i, p) {
  const zoomIn = i % 2 === 0;
  const k = zoomIn ? 1 + 0.12 * p : 1.12 - 0.12 * p;
  const base = Math.max(W / img.width, H / img.height) * k;
  const w = img.width * base;
  const h = img.height * base;
  const dir = (i % 3) - 1; // -1, 0, 1: pan left, none, right
  const x = (W - w) / 2 + dir * (w - W) * 0.5 * (p - 0.5);
  const y = (H - h) / 2 + (zoomIn ? -1 : 1) * (h - H) * 0.25 * (p - 0.5);
  ctx.drawImage(img, x, y, w, h);
}

function drawScene(ctx, seg, i, t, W, H) {
  const p = Math.min(1, Math.max(0, t / seg.dur));
  if (seg.img) drawCover(ctx, seg.img, W, H, i, p);
  else {
    const g = ctx.createLinearGradient(0, 0, W, H);
    g.addColorStop(0, '#2b1d5c');
    g.addColorStop(1, '#0d0b1a');
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, W, H);
  }
}

function drawFrame(ctx, tl, t, W, H, title) {
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, W, H);
  const found = tl.segs.findIndex((s) => t < s.start + s.dur);
  const idx = found === -1 ? tl.segs.length - 1 : found;
  const seg = tl.segs[idx];
  const local = t - seg.start;
  if (idx > 0 && local < FADE) {
    const prev = tl.segs[idx - 1];
    drawScene(ctx, prev, idx - 1, t - prev.start, W, H);
    ctx.globalAlpha = local / FADE;
    drawScene(ctx, seg, idx, local, W, H);
    ctx.globalAlpha = 1;
  } else drawScene(ctx, seg, idx, local, W, H);

  // Shade under the captions so white text stays readable on any image.
  const shade = ctx.createLinearGradient(0, H * 0.5, 0, H);
  shade.addColorStop(0, 'rgba(0,0,0,0)');
  shade.addColorStop(1, 'rgba(0,0,0,0.6)');
  ctx.fillStyle = shade;
  ctx.fillRect(0, H * 0.5, W, H * 0.5);

  const fontFamily = '"Segoe UI", "Noto Sans Arabic", system-ui, -apple-system, sans-serif';
  // Title banner
  if (title && t < TITLE_TIME) {
    const a = Math.min(1, t / 0.25, (TITLE_TIME - t) / 0.4);
    const size = Math.round(Math.min(W, H) * 0.062);
    ctx.font = `800 ${size}px ${fontFamily}`;
    ctx.direction = ARABIC.test(title) ? 'rtl' : 'ltr';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    const lines = wrapLines(ctx, title, W * 0.8);
    const boxH = lines.length * size * 1.3 + size * 0.9;
    const top = H * 0.07;
    ctx.globalAlpha = a;
    ctx.fillStyle = 'rgba(255, 196, 0, 0.95)';
    const boxW = Math.min(W * 0.9, Math.max(...lines.map((l) => ctx.measureText(l).width)) + size * 1.4);
    ctx.beginPath();
    (ctx.roundRect ? ctx.roundRect((W - boxW) / 2, top, boxW, boxH, size * 0.4) : ctx.rect((W - boxW) / 2, top, boxW, boxH));
    ctx.fill();
    ctx.fillStyle = '#111';
    lines.forEach((l, k) => ctx.fillText(l, W / 2, top + size * 0.45 + size * 1.3 * (k + 0.5)));
    ctx.globalAlpha = 1;
  }

  // Captions
  const chunk = seg.chunks.find((c) => local >= c.start && local < c.end + 0.05);
  if (chunk) {
    const size = Math.round(Math.min(W, H) * (H > W ? 0.078 : 0.06));
    const pop = Math.min(1, (local - chunk.start) / 0.12);
    ctx.save();
    ctx.translate(W / 2, H * (H > W ? 0.74 : 0.82));
    ctx.scale(0.85 + 0.15 * pop, 0.85 + 0.15 * pop);
    ctx.font = `900 ${size}px ${fontFamily}`;
    ctx.direction = ARABIC.test(chunk.text) ? 'rtl' : 'ltr';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.lineJoin = 'round';
    const lines = wrapLines(ctx, chunk.text, W * 0.86);
    lines.forEach((l, k) => {
      const y = (k - (lines.length - 1) / 2) * size * 1.2;
      ctx.lineWidth = size * 0.2;
      ctx.strokeStyle = '#000';
      ctx.strokeText(l, 0, y);
      ctx.fillStyle = k === 0 ? '#fff' : '#ffd400';
      ctx.fillText(l, 0, y);
    });
    ctx.restore();
  }

  // Watermark
  ctx.font = `700 ${Math.round(Math.min(W, H) * 0.03)}px ${fontFamily}`;
  ctx.direction = 'ltr';
  ctx.textAlign = 'right';
  ctx.textBaseline = 'bottom';
  ctx.fillStyle = 'rgba(255,255,255,0.7)';
  ctx.fillText('Vasco AI', W - W * 0.03, H - H * 0.02);
}

/**
 * MediaRecorder writes fragmented MP4 whose header says "duration 0", which some apps (galleries, Instagram)
 * reject or show as 0:00. Writes the real duration into mvhd / tkhd / mdhd / mehd, in place.
 */
function fixMp4Duration(buf, seconds) {
  const v = new DataView(buf);
  let movieScale = 1000;
  const put = (off, ver, value) => {
    const n = Math.round(value);
    if (ver === 1) {
      v.setUint32(off, Math.floor(n / 2 ** 32));
      v.setUint32(off + 4, n >>> 0);
    } else v.setUint32(off, Math.min(n, 0xffffffff));
  };
  const walk = (start, end) => {
    let p = start;
    while (p + 8 <= end) {
      let size = v.getUint32(p);
      const type = String.fromCharCode(v.getUint8(p + 4), v.getUint8(p + 5), v.getUint8(p + 6), v.getUint8(p + 7));
      let hdr = 8;
      if (size === 1) { size = v.getUint32(p + 8) * 2 ** 32 + v.getUint32(p + 12); hdr = 16; }
      if (size === 0) size = end - p;
      if (size < hdr || p + size > end) return;
      const b = p + hdr; // start of the box body (version byte)
      const ver = v.getUint8(b);
      if (['moov', 'trak', 'mdia', 'mvex'].includes(type)) walk(b, p + size);
      else if (type === 'mvhd') {
        movieScale = v.getUint32(ver === 1 ? b + 20 : b + 12);
        put(ver === 1 ? b + 24 : b + 16, ver, seconds * movieScale);
      } else if (type === 'tkhd') put(ver === 1 ? b + 28 : b + 20, ver, seconds * movieScale);
      else if (type === 'mdhd') {
        const scale = v.getUint32(ver === 1 ? b + 20 : b + 12);
        put(ver === 1 ? b + 24 : b + 16, ver, seconds * scale);
      } else if (type === 'mehd') put(b + 4, ver, seconds * movieScale);
      p += size;
    }
  };
  try { walk(0, buf.byteLength); } catch (_) { /* leave the file as recorded */ }
  return buf;
}

/* ---------- Fragmented MP4 -> regular MP4 (moov first, one sample table per track) ---------- */

const u32 = (n) => [(n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255];
const str4 = (s) => [...s].map((c) => c.charCodeAt(0));
function box(type, ...parts) {
  const body = parts.flatMap((p) => (p instanceof Uint8Array ? [p] : [Uint8Array.from(p)]));
  const size = 8 + body.reduce((a, p) => a + p.length, 0);
  const out = new Uint8Array(size);
  out.set([...u32(size), ...str4(type)], 0);
  let o = 8;
  for (const p of body) { out.set(p, o); o += p.length; }
  return out;
}
const fullBox = (type, version, flags, ...parts) => box(type, [version, (flags >> 16) & 255, (flags >> 8) & 255, flags & 255], ...parts);

function readBoxes(bytes, start, end) {
  const v = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const list = [];
  let p = start;
  while (p + 8 <= end) {
    let size = v.getUint32(p);
    let hdr = 8;
    if (size === 1) { size = v.getUint32(p + 8) * 2 ** 32 + v.getUint32(p + 12); hdr = 16; }
    if (size === 0) size = end - p;
    if (size < hdr || p + size > end) throw new Error('bad box');
    list.push({ type: String.fromCharCode(...bytes.subarray(p + 4, p + 8)), start: p, body: p + hdr, end: p + size });
    p += size;
  }
  return list;
}

/** Rebuilds the MediaRecorder fragmented MP4 as a regular MP4. Returns null if the file is not fragmented. */
function flattenMp4(buf) {
  const bytes = new Uint8Array(buf);
  const v = new DataView(buf);
  const top = readBoxes(bytes, 0, bytes.length);
  const ftyp = top.find((b) => b.type === 'ftyp');
  const moov = top.find((b) => b.type === 'moov');
  if (!ftyp || !moov || !top.some((b) => b.type === 'moof')) return null;
  const kids = (b) => readBoxes(bytes, b.body, b.end);
  const child = (b, t) => kids(b).find((k) => k.type === t);
  const raw = (b) => bytes.subarray(b.start, b.end);

  const moovKids = kids(moov);
  const trex = {};
  const mvex = moovKids.find((b) => b.type === 'mvex');
  if (mvex) for (const t of kids(mvex).filter((b) => b.type === 'trex')) {
    trex[v.getUint32(t.body + 4)] = { dur: v.getUint32(t.body + 12), size: v.getUint32(t.body + 16), flags: v.getUint32(t.body + 20) };
  }
  const tracks = {};
  const traks = moovKids.filter((b) => b.type === 'trak');
  for (const trak of traks) {
    const tkhd = child(trak, 'tkhd');
    const id = v.getUint32(tkhd.body + (bytes[tkhd.body] === 1 ? 20 : 12));
    tracks[id] = { trak, samples: [] };
  }

  // Collect every sample (offset, size, duration, flags, composition offset) from the fragments.
  for (const moof of top.filter((b) => b.type === 'moof')) {
    for (const traf of kids(moof).filter((b) => b.type === 'traf')) {
      const tfhd = child(traf, 'tfhd');
      const tf = v.getUint32(tfhd.body) & 0xffffff;
      const id = v.getUint32(tfhd.body + 4);
      const d = trex[id] || { dur: 0, size: 0, flags: 0 };
      let o = tfhd.body + 8;
      let base = moof.start;
      if (tf & 0x1) { base = v.getUint32(o) * 2 ** 32 + v.getUint32(o + 4); o += 8; }
      if (tf & 0x2) o += 4;
      let defDur = d.dur; let defSize = d.size; let defFlags = d.flags;
      if (tf & 0x8) { defDur = v.getUint32(o); o += 4; }
      if (tf & 0x10) { defSize = v.getUint32(o); o += 4; }
      if (tf & 0x20) { defFlags = v.getUint32(o); o += 4; }
      const track = tracks[id];
      if (!track) continue;
      for (const trun of kids(traf).filter((b) => b.type === 'trun')) {
        const ver = bytes[trun.body];
        const rf = v.getUint32(trun.body) & 0xffffff;
        const count = v.getUint32(trun.body + 4);
        let q = trun.body + 8;
        let pos = base;
        if (rf & 0x1) { pos = base + v.getInt32(q); q += 4; }
        let firstFlags = null;
        if (rf & 0x4) { firstFlags = v.getUint32(q); q += 4; }
        for (let i = 0; i < count; i++) {
          let dur = defDur; let size = defSize; let flags = i === 0 && firstFlags !== null ? firstFlags : defFlags; let cto = 0;
          if (rf & 0x100) { dur = v.getUint32(q); q += 4; }
          if (rf & 0x200) { size = v.getUint32(q); q += 4; }
          if (rf & 0x400) { flags = v.getUint32(q); q += 4; }
          if (rf & 0x800) { cto = ver === 0 ? v.getUint32(q) : v.getInt32(q); q += 4; }
          track.samples.push({ pos, size, dur, sync: !(flags & 0x10000), cto });
          pos += size;
        }
      }
    }
  }

  const mvhd = moovKids.find((b) => b.type === 'mvhd');
  const movieScale = v.getUint32(mvhd.body + (bytes[mvhd.body] === 1 ? 20 : 12));
  const order = Object.keys(tracks).map(Number).filter((id) => tracks[id].samples.length);
  if (!order.length) return null;
  // Chrome sometimes writes audio frames with a 0 duration; give them the track's usual frame duration.
  for (const id of order) {
    const counts = {};
    for (const s of tracks[id].samples) if (s.dur) counts[s.dur] = (counts[s.dur] || 0) + 1;
    const usual = Number(Object.keys(counts).sort((a, b) => counts[b] - counts[a])[0] || 0);
    if (usual) for (const s of tracks[id].samples) if (!s.dur) s.dur = usual;
  }

  const build = (chunkOffsets) => {
    let movieDur = 0;
    const trakBoxes = order.map((id, ti) => {
      const { trak, samples } = tracks[id];
      const mdia = child(trak, 'mdia');
      const mdhd = child(mdia, 'mdhd');
      const scale = v.getUint32(mdhd.body + (bytes[mdhd.body] === 1 ? 20 : 12));
      const total = samples.reduce((a, s) => a + s.dur, 0);
      const inMovie = Math.round((total / scale) * movieScale);
      movieDur = Math.max(movieDur, inMovie);
      const minf = child(mdia, 'minf');
      const stbl = child(minf, 'stbl');
      const stts = [];
      for (const s of samples) {
        const last = stts[stts.length - 1];
        if (last && last[1] === s.dur) last[0]++; else stts.push([1, s.dur]);
      }
      const ctts = [];
      for (const s of samples) {
        const last = ctts[ctts.length - 1];
        if (last && last[1] === s.cto) last[0]++; else ctts.push([1, s.cto]);
      }
      const hasCtts = samples.some((s) => s.cto !== 0);
      const allSync = samples.every((s) => s.sync);
      const newStbl = box('stbl',
        raw(child(stbl, 'stsd')),
        fullBox('stts', 0, 0, u32(stts.length), stts.flatMap(([c, d]) => [...u32(c), ...u32(d)])),
        ...(hasCtts ? [fullBox('ctts', 1, 0, u32(ctts.length), ctts.flatMap(([c, o]) => [...u32(c), ...u32(o >>> 0)]))] : []),
        ...(allSync ? [] : [fullBox('stss', 0, 0, ...(() => {
          const idx = samples.map((s, i) => (s.sync ? i + 1 : 0)).filter(Boolean);
          return [u32(idx.length), idx.flatMap(u32)];
        })())]),
        fullBox('stsc', 0, 0, u32(1), u32(1), u32(samples.length), u32(1)),
        fullBox('stsz', 0, 0, u32(0), u32(samples.length), samples.flatMap((s) => u32(s.size))),
        fullBox('stco', 0, 0, u32(1), u32(chunkOffsets[ti])));
      const newMinf = box('minf', ...kids(minf).filter((b) => b.type !== 'stbl').map(raw), newStbl);
      const mdhdCopy = raw(mdhd).slice();
      const mv = new DataView(mdhdCopy.buffer);
      if (mdhdCopy[8] === 1) { mv.setUint32(32, Math.floor(total / 2 ** 32)); mv.setUint32(36, total >>> 0); } else mv.setUint32(24, total);
      const newMdia = box('mdia', mdhdCopy, ...kids(mdia).filter((b) => !['mdhd', 'minf'].includes(b.type)).map(raw), newMinf);
      const tkhd = child(trak, 'tkhd');
      const tkhdCopy = raw(tkhd).slice();
      const tv = new DataView(tkhdCopy.buffer);
      if (tkhdCopy[8] === 1) { tv.setUint32(36, Math.floor(inMovie / 2 ** 32)); tv.setUint32(40, inMovie >>> 0); } else tv.setUint32(28, inMovie);
      return box('trak', tkhdCopy, ...kids(trak).filter((b) => !['tkhd', 'mdia'].includes(b.type)).map(raw), newMdia);
    });
    const mvhdCopy = raw(mvhd).slice();
    const hv = new DataView(mvhdCopy.buffer);
    if (mvhdCopy[8] === 1) { hv.setUint32(32, Math.floor(movieDur / 2 ** 32)); hv.setUint32(36, movieDur >>> 0); } else hv.setUint32(24, movieDur);
    return box('moov', mvhdCopy, ...trakBoxes);
  };

  const sizes = order.map((id) => tracks[id].samples.reduce((a, s) => a + s.size, 0));
  const ftypBytes = raw(ftyp);
  const moovLen = build(order.map(() => 0)).length;
  const dataStart = ftypBytes.length + moovLen + 8;
  const offsets = [];
  let acc = dataStart;
  for (const s of sizes) { offsets.push(acc); acc += s; }
  const newMoov = build(offsets);
  const mdatSize = 8 + sizes.reduce((a, b) => a + b, 0);
  const out = new Uint8Array(ftypBytes.length + newMoov.length + mdatSize);
  out.set(ftypBytes, 0);
  out.set(newMoov, ftypBytes.length);
  out.set([...u32(mdatSize), ...str4('mdat')], ftypBytes.length + newMoov.length);
  let w = dataStart;
  for (const id of order) for (const s of tracks[id].samples) { out.set(bytes.subarray(s.pos, s.pos + s.size), w); w += s.size; }
  return out.buffer;
}

function pickMime() {
  const types = [
    'video/mp4;codecs=avc1.42E01E,mp4a.40.2', 'video/mp4;codecs=avc1,mp4a.40.2', 'video/mp4',
    'video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm',
  ];
  return types.find((t) => window.MediaRecorder?.isTypeSupported?.(t)) || '';
}

export default function ReelPlayer({ video }) {
  const W = video.width || 720;
  const H = video.height || 1280;
  const wrapRef = useRef(null);
  const canvasRef = useRef(null);
  const tlRef = useRef(null);
  const runRef = useRef(null); // {src, raf, start, recorder}
  const [phase, setPhase] = useState('idle'); // idle | loading | ready | playing | recording | error
  const [progress, setProgress] = useState('');
  const [recPct, setRecPct] = useState(0);

  const paintPoster = useCallback((t = 0) => {
    const c = canvasRef.current;
    if (c && tlRef.current) drawFrame(c.getContext('2d'), tlRef.current, t, W, H, video.title);
  }, [W, H, video.title]);

  const loadingRef = useRef(false);
  const load = useCallback(async () => {
    if (tlRef.current || loadingRef.current) return;
    loadingRef.current = true;
    setPhase('loading');
    const ctx = audioCtx();
    const scenes = video.scenes || [];
    let doneImg = 0;
    let doneVoice = 0;
    const report = () => setProgress(`Tsawer ${doneImg}/${scenes.length} · Sout ${doneVoice}/${scenes.length}`);
    report();
    const voices = Promise.all(scenes.map((s) => loadVoice(ctx, s.narration, video.voice || 'male')
      .then((b) => { doneVoice++; report(); return b; })));
    const imgs = [];
    for (const s of scenes) { // one at a time: the free image service limits parallel requests
      imgs.push(await loadImage(s.image));
      doneImg++;
      report();
    }
    const buffers = await voices;
    loadingRef.current = false;
    if (!imgs.some(Boolean)) {
      setPhase('error');
      setProgress("Ma 9dertch njib tsawer. 3awed jarreb mn b3d chwiya.");
      return;
    }
    let t = 0;
    const segs = scenes.map((s, i) => {
      const voiceDur = buffers[i]?.duration || Math.max(2, s.narration.length / 14);
      const dur = Math.max(MIN_SCENE, voiceDur + TAIL + (i === 0 && video.title ? 0.4 : 0));
      const lead = i === 0 && video.title ? 0.4 : 0;
      const seg = { start: t, dur, img: imgs[i], audio: buffers[i], lead,
                    chunks: captionChunks(s.narration, voiceDur).map((c) => ({ ...c, start: c.start + lead, end: c.end + lead })) };
      t += dur;
      return seg;
    });
    const total = t + 0.2;
    const sr = ctx.sampleRate;
    const track = ctx.createBuffer(1, Math.ceil(total * sr), sr);
    const out = track.getChannelData(0);
    segs.forEach((seg) => {
      if (!seg.audio) return;
      const off = Math.round((seg.start + seg.lead) * sr);
      const ch = seg.audio.getChannelData(0);
      out.set(ch.subarray(0, Math.max(0, Math.min(ch.length, out.length - off))), off);
    });
    tlRef.current = { segs, total, track };
    setPhase('ready');
    setProgress('');
    requestAnimationFrame(() => paintPoster(0.6));
  }, [video, paintPoster]);

  // Load once, when the player scrolls into view (not for every old message in the history).
  const loadRef = useRef(load);
  loadRef.current = load;
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return undefined;
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        io.disconnect();
        loadRef.current();
      }
    }, { rootMargin: '200px' });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  const stop = useCallback(() => {
    const r = runRef.current;
    if (!r) return;
    cancelAnimationFrame(r.raf);
    try { r.src.stop(); } catch (_) { /* already stopped */ }
    runRef.current = null;
  }, []);

  useEffect(() => () => {
    const r = runRef.current;
    if (r?.recorder && r.recorder.state !== 'inactive') r.recorder.stop();
    stop();
  }, [stop]);

  const run = useCallback(async ({ record = false } = {}) => {
    const tl = tlRef.current;
    if (!tl) return;
    stop();
    const ctx = audioCtx();
    try { if (navigator.audioSession) navigator.audioSession.type = 'playback'; } catch (_) { /* optional */ }
    await ctx.resume();
    const src = ctx.createBufferSource();
    src.buffer = tl.track;
    src.connect(ctx.destination);
    let recorder = null;
    let chunks = [];
    let mime = '';
    if (record) {
      const dest = ctx.createMediaStreamDestination();
      src.connect(dest);
      const stream = canvasRef.current.captureStream(30);
      dest.stream.getAudioTracks().forEach((tr) => stream.addTrack(tr));
      mime = pickMime();
      recorder = new MediaRecorder(stream, { ...(mime ? { mimeType: mime } : {}), videoBitsPerSecond: 5_000_000 });
      recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
      recorder.onstop = async () => {
        const type = recorder.mimeType || mime || 'video/webm';
        const ext = type.includes('mp4') ? 'mp4' : 'webm';
        const name = `${(video.title || 'vasco-video').replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-|-$/g, '') || 'vasco-video'}.${ext}`;
        let blob = new Blob(chunks, { type });
        chunks = [];
        if (ext === 'mp4') {
          const data = await blob.arrayBuffer();
          let flat = null;
          try { flat = flattenMp4(data); } catch (_) { flat = null; }
          blob = new Blob([flat || fixMp4Duration(data, tl.total)], { type: 'video/mp4' });
        }
        const file = new File([blob], name, { type });
        const touch = window.matchMedia?.('(pointer: coarse)').matches;
        if (touch && navigator.canShare?.({ files: [file] })) {
          try { await navigator.share({ files: [file], title: video.title || 'Vasco' }); } catch (_) { /* cancelled */ }
        } else {
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = name;
          document.body.appendChild(a);
          a.click();
          a.remove();
          setTimeout(() => URL.revokeObjectURL(url), 60_000);
        }
        setPhase('ready');
        setRecPct(0);
      };
      recorder.start(1000);
    }
    const start = ctx.currentTime + 0.08;
    src.start(start);
    const r = { src, start, recorder, raf: 0 };
    runRef.current = r;
    setPhase(record ? 'recording' : 'playing');
    const c2d = canvasRef.current.getContext('2d');
    const tick = () => {
      const t = Math.max(0, ctx.currentTime - start);
      drawFrame(c2d, tl, Math.min(t, tl.total - 0.01), W, H, video.title);
      if (record) setRecPct(Math.min(99, Math.round((t / tl.total) * 100)));
      if (t >= tl.total) {
        runRef.current = null;
        if (recorder) setTimeout(() => recorder.state !== 'inactive' && recorder.stop(), 150);
        else setPhase('ready');
        return;
      }
      r.raf = requestAnimationFrame(tick);
    };
    r.raf = requestAnimationFrame(tick);
  }, [stop, W, H, video.title]);

  const portrait = H > W;
  const canRecord = typeof window !== 'undefined' && !!window.MediaRecorder
    && !!HTMLCanvasElement.prototype.captureStream;

  return (
    <div ref={wrapRef} className={`reel ${portrait ? 'reel-portrait' : 'reel-landscape'}`}>
      <div className="reel-stage" style={{ aspectRatio: `${W} / ${H}` }}>
        <canvas ref={canvasRef} width={W} height={H} className="reel-canvas"
                onClick={() => (phase === 'playing' ? (stop(), setPhase('ready')) : phase === 'ready' && run())} />
        {(phase === 'idle' || phase === 'loading' || phase === 'error') && (
          <div className="reel-overlay">
            {phase !== 'error' && <Loader2 size={28} className="reel-spin" />}
            <div className="reel-overlay-text" dir="auto">{progress || 'Kanwajjed l-video…'}</div>
          </div>
        )}
        {phase === 'ready' && (
          <button type="button" className="reel-bigplay" onClick={() => run()} aria-label="Lecture">
            <Play size={34} fill="currentColor" />
          </button>
        )}
        {phase === 'recording' && (
          <div className="reel-rec">● REC {recPct}% · khlli had l-page m7loula</div>
        )}
      </div>
      <div className="reel-bar">
        {phase === 'playing' ? (
          <button type="button" onClick={() => { stop(); setPhase('ready'); }}><Pause size={15} /> Pause</button>
        ) : (
          <button type="button" disabled={phase !== 'ready'} onClick={() => run()}>
            {tlRef.current ? <RotateCcw size={15} /> : <Play size={15} />} Lecture
          </button>
        )}
        {canRecord && (
          <button type="button" className="reel-primary" disabled={phase !== 'ready'} onClick={() => run({ record: true })}>
            {phase === 'recording' ? <Loader2 size={15} className="reel-spin" /> : <Download size={15} />}
            {phase === 'recording' ? `Kansajjel… ${recPct}%` : 'Télécharger'}
          </button>
        )}
        {phase === 'error' && (
          <button type="button" onClick={() => load()}>
            <RotateCcw size={15} /> 3awed
          </button>
        )}
      </div>
    </div>
  );
}
