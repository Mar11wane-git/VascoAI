// Authenticated API helpers for the Vasco backend.

export function authHeaders(extra = {}) {
  const token = localStorage.getItem('vasco_auth_token') || '';
  return token ? { ...extra, Authorization: `Bearer ${token}` } : { ...extra };
}

export function apiFetch(url, options = {}) {
  return fetch(url, { ...options, headers: authHeaders(options.headers || {}) });
}

/**
 * Streams an agent run from POST /api/agent/stream (Server-Sent Events).
 * Calls onEvent(event) for every event; resolves when the stream ends.
 */
export async function streamAgent(body, { signal, onEvent }) {
  const res = await apiFetch('/api/agent/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });

  if (!res.ok || !res.body) {
    let detail = `Erreur HTTP ${res.status}`;
    try {
      const data = await res.json();
      detail = data.detail || detail;
    } catch (_) {}
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep;
    while ((sep = buffer.indexOf('\n\n')) >= 0) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const data = frame
        .split('\n')
        .filter((l) => l.startsWith('data:'))
        .map((l) => l.slice(5).trimStart())
        .join('\n');
      if (!data) continue;
      try {
        onEvent(JSON.parse(data));
      } catch (_) {
        /* ignore malformed frame */
      }
    }
  }
}
