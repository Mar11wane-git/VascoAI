// Syncs conversations between this browser (localStorage) and the user's account on the server.
//
// For every conversation we remember a fingerprint of the version last exchanged with the server
// (stored in localStorage). On sign-in, a local copy whose fingerprint still matches was not edited
// here since, so the server's copy (maybe edited on another device) replaces it; a local copy that
// changed since wins and is uploaded. After that, every change is uploaded (debounced by the caller).
import { apiFetch } from './api.js';

const MAX_INLINE_ATTACHMENT = 150_000; // bigger photo previews stay on this device only

const syncKey = (userId) => `vasco_sync_${userId}`;

export function loadSynced(userId) {
  try {
    return JSON.parse(localStorage.getItem(syncKey(userId)) || '{}') || {};
  } catch {
    return {};
  }
}

export function saveSynced(userId, map) {
  try {
    localStorage.setItem(syncKey(userId), JSON.stringify(map));
  } catch (_) {}
}

// What is sent to the server: no transient UI state, no oversized inline photos.
function cleanMessages(messages) {
  return (messages || [])
    .filter((m) => m.status !== 'streaming')
    .map((m) => {
      const { statusText, ...rest } = m;
      if (rest.attachments?.length) {
        rest.attachments = rest.attachments.map((a) =>
          a.data_url && a.data_url.length > MAX_INLINE_ATTACHMENT ? { ...a, data_url: undefined } : a
        );
      }
      return rest;
    });
}

// Short fingerprint (cyrb53 hash) of what would be uploaded.
export function fingerprint(conv) {
  const str = JSON.stringify([conv.title || '', cleanMessages(conv.messages)]);
  let h1 = 0xdeadbeef;
  let h2 = 0x41c6ce57;
  for (let i = 0; i < str.length; i++) {
    const ch = str.charCodeAt(i);
    h1 = Math.imul(h1 ^ ch, 2654435761);
    h2 = Math.imul(h2 ^ ch, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return (4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(36) + ':' + str.length;
}

export async function fetchRemote() {
  const res = await apiFetch('/api/conversations');
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json(); // { conversations: [...], deleted: [ids] }
}

/** Merges the server's list into the local one. Returns { merged, synced } (synced = new fingerprint map). */
export function mergeConversations(local, remote, synced) {
  const deleted = new Set(remote.deleted || []);
  const remoteById = new Map((remote.conversations || []).map((c) => [c.id, c]));
  const nextSynced = { ...synced };
  const merged = [];
  const seen = new Set();

  for (const l of local) {
    seen.add(l.id);
    const r = remoteById.get(l.id);
    const editedHere = synced[l.id] !== fingerprint(l);
    if (r) {
      if (editedHere) merged.push(l); // uploaded by the caller afterwards
      else {
        merged.push({ ...r });
        nextSynced[l.id] = fingerprint(r);
      }
    } else if (deleted.has(l.id) && !editedHere) {
      delete nextSynced[l.id]; // deleted on another device
    } else {
      merged.push(l);
    }
  }
  for (const r of remote.conversations || []) {
    if (seen.has(r.id)) continue;
    merged.push({ ...r });
    nextSynced[r.id] = fingerprint(r);
  }
  for (const id of deleted) if (!merged.some((c) => c.id === id)) delete nextSynced[id];

  // Newest first; empty "Nouvelle discussion" drafts stay on top.
  const when = (c) => (c.messages?.length ? c.updatedAt || c.createdAt || 0 : Number.MAX_SAFE_INTEGER);
  merged.sort((a, b) => when(b) - when(a));
  return { merged, synced: nextSynced };
}

/** Uploads every non-empty conversation whose fingerprint changed. Returns the updated fingerprint map. */
export async function pushChanged(conversations, synced) {
  const next = { ...synced };
  for (const c of conversations) {
    if (!c.messages?.length) continue;
    const fp = fingerprint(c);
    if (next[c.id] === fp) continue;
    const res = await apiFetch(`/api/conversations/${encodeURIComponent(c.id)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: c.title, messages: cleanMessages(c.messages), created_at: c.createdAt || 0 }),
    });
    if (res.status === 401) throw new Error('unauthorized');
    if (res.ok) next[c.id] = fp;
  }
  return next;
}

export function deleteRemote(id) {
  return apiFetch(`/api/conversations/${encodeURIComponent(id)}`, { method: 'DELETE' }).catch(() => {});
}
