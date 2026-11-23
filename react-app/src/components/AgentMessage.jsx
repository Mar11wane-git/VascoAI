import React, { useEffect, useMemo, useRef, useState } from 'react';
import { renderMarkdown } from '../lib/markdown.js';

/**
 * Reveals streamed text progressively instead of in the bursts the network delivers.
 * The reveal speed adapts to the backlog (it always catches up within ~1/4 s), and the
 * Markdown is re-rendered at most ~30 times per second.
 */
function useSmoothText(target, streaming) {
  // Messages loaded from history are shown at once; only live text is animated.
  const [shown, setShown] = useState(() => (streaming ? '' : target));
  const shownRef = useRef(shown);
  const targetRef = useRef(target);
  const rafRef = useRef(0);
  targetRef.current = target;

  useEffect(() => {
    if (rafRef.current || shownRef.current === target) return;
    let last = 0;
    let lastRender = 0;
    const tick = (now) => {
      const goal = targetRef.current;
      let cur = shownRef.current;
      if (!goal.startsWith(cur)) cur = ''; // text was replaced: restart the reveal
      const backlog = goal.length - cur.length;
      if (backlog <= 0) {
        rafRef.current = 0; // caught up: sleep until new text arrives
        return;
      }
      const dt = last ? Math.min(now - last, 64) : 16;
      // ~45 chars/s minimum, or the whole backlog in ~250 ms when far behind.
      const step = Math.max(1, Math.round(Math.max(45 / 1000, backlog / 250) * dt));
      shownRef.current = goal.slice(0, cur.length + step);
      if (now - lastRender >= 33 || shownRef.current.length === goal.length) {
        lastRender = now;
        setShown(shownRef.current);
      }
      last = now;
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
  }, [target, streaming]);

  useEffect(() => () => cancelAnimationFrame(rafRef.current), []);

  return shown;
}

function argSummary(name, args = {}) {
  if (!args || typeof args !== 'object') return '';
  if (args.query) return `« ${args.query} »`;
  if (args.url) {
    try {
      const u = new URL(args.url);
      return u.hostname.replace(/^www\./, '') + (u.pathname.length > 1 ? u.pathname.slice(0, 40) : '');
    } catch {
      return String(args.url).slice(0, 60);
    }
  }
  if (args.expression) return args.expression;
  if (args.code) return String(args.code).split('\n').find((l) => l.trim()) || 'script';
  if (args.prompt) return String(args.prompt).slice(0, 80);
  if (args.timezone) return args.timezone;
  return name === 'get_datetime' ? 'heure actuelle' : '';
}

function formatMs(ms) {
  if (ms == null) return '';
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

function Markdown({ text, streaming }) {
  const visible = useSmoothText(text || '', Boolean(streaming));
  const html = useMemo(() => renderMarkdown(visible), [visible]);
  return (
    <div className={`msg-bubble agent-text ${streaming ? 'is-streaming' : ''}`} dir="auto">
      <div dangerouslySetInnerHTML={{ __html: html }} />
    </div>
  );
}

function ThinkingBlock({ text, active }) {
  const [open, setOpen] = useState(null);
  const isOpen = open === null ? active : open;
  const words = text.trim().split(/\s+/).length;
  return (
    <div className={`agent-thinking ${active ? 'active' : ''}`}>
      <button type="button" className="agent-thinking-head" onClick={() => setOpen(!isOpen)}>
        <span className={`thinking-icon ${active ? 'pulse' : ''}`}>🧠</span>
        <span>{active ? 'Réflexion en cours…' : 'Réflexion'}</span>
        <span className="agent-muted">{words} mots</span>
        <span className={`chev ${isOpen ? 'open' : ''}`}>▾</span>
      </button>
      {isOpen && <div className="agent-thinking-body" dir="auto">{text}</div>}
    </div>
  );
}

function ToolCard({ part, onOpenImage }) {
  const [open, setOpen] = useState(false);
  const status = part.status || 'running';
  return (
    <div className={`tool-card status-${status}`}>
      <button type="button" className="tool-card-head" onClick={() => setOpen(!open)}>
        <span className="tool-card-icon">{part.icon || '🛠️'}</span>
        <span className="tool-card-label">{part.label || part.name}</span>
        <span className="tool-card-arg" dir="auto">{argSummary(part.name, part.args)}</span>
        <span className="tool-card-meta">
          {status === 'running' ? (
            <span className="tool-spinner" aria-label="En cours" />
          ) : (
            <>
              <span className="tool-card-summary">{part.summary}</span>
              <span className="tool-card-time">{formatMs(part.duration_ms)}</span>
              <span className={`tool-status-dot ${status}`}>{status === 'ok' ? '✓' : '✕'}</span>
            </>
          )}
        </span>
      </button>
      {part.image_url && status !== 'running' && (
        <img className="tool-card-image" src={part.image_url} alt={part.args?.prompt || 'image'}
             loading="lazy" onClick={() => onOpenImage?.(part.image_url)} />
      )}
      {open && (
        <div className="tool-card-body">
          <div className="tool-card-section-title">Arguments</div>
          <pre>{JSON.stringify(part.args || {}, null, 2)}</pre>
          {part.preview && (
            <>
              <div className="tool-card-section-title">Résultat</div>
              <pre dir="auto">{part.preview}</pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function Sources({ sources }) {
  if (!sources?.length) return null;
  return (
    <div className="agent-sources">
      <div className="agent-sources-title">Sources</div>
      <div className="agent-sources-list">
        {sources.map((s, i) => {
          let host = s.source;
          try {
            host = host || new URL(s.url).hostname.replace(/^www\./, '');
          } catch {}
          return (
            <a key={i} href={s.url} target="_blank" rel="noopener noreferrer" className="agent-source" title={s.title}>
              <span className="agent-source-num">{i + 1}</span>
              <span className="agent-source-text">
                <strong>{s.title}</strong>
                <small>{host}</small>
              </span>
            </a>
          );
        })}
      </div>
    </div>
  );
}

export default function AgentMessage({ msg, isLast, onCopy, onSpeak, onRegenerate, onOpenImage }) {
  const streaming = msg.status === 'streaming';
  const parts = msg.parts;

  // Legacy messages (before the agent upgrade) have no parts.
  if (!parts) {
    return (
      <>
        {msg.reasoning && <ThinkingBlock text={msg.reasoning} active={false} />}
        <Markdown text={msg.content} />
        <Sources sources={msg.sources} />
        <div className="msg-actions">
          <button className="msg-action-btn" onClick={() => onCopy(msg.content)}>📋 Copier</button>
          <button className="msg-action-btn" onClick={() => onSpeak(msg.content)}>🔊 Écouter</button>
        </div>
      </>
    );
  }

  const lastIdx = parts.length - 1;
  const showWaiting =
    streaming &&
    (Boolean(msg.statusText) || parts.length === 0 || (parts[lastIdx].type === 'tool' && parts[lastIdx].status !== 'running'));

  return (
    <>
      <div className="agent-timeline">
        {parts.map((p, i) => {
          if (p.type === 'reasoning') return <ThinkingBlock key={i} text={p.text} active={streaming && i === lastIdx} />;
          if (p.type === 'tool') return <ToolCard key={p.id || i} part={p} onOpenImage={onOpenImage} />;
          if (p.type === 'notice') return <div key={i} className="agent-notice">ℹ️ {p.text}</div>;
          return <Markdown key={i} text={p.text} streaming={streaming && i === lastIdx} />;
        })}
        {showWaiting && (
          <div className="agent-working">
            <div className="typing-indicator"><div className="typing-dot" /><div className="typing-dot" /><div className="typing-dot" /></div>
            <span>{msg.statusText || (parts.length ? 'Analyse des résultats…' : 'Vasco réfléchit…')}</span>
          </div>
        )}
        {msg.status === 'error' && <div className="agent-error">⚠️ {msg.error}</div>}
        {msg.status === 'stopped' && <div className="agent-notice">⏹ Génération arrêtée.</div>}
      </div>

      {!streaming && <Sources sources={msg.sources} />}

      {!streaming && (
        <div className="msg-actions">
          <button className="msg-action-btn" onClick={() => onCopy(msg.content)}>📋 Copier</button>
          {msg.content && <button className="msg-action-btn" onClick={() => onSpeak(msg.content)}>🔊 Écouter</button>}
          {isLast && onRegenerate && <button className="msg-action-btn" onClick={onRegenerate}>↻ Régénérer</button>}
          {msg.stats && (
            <span className="agent-stats">
              {msg.stats.steps} étape{msg.stats.steps > 1 ? 's' : ''} · {msg.stats.tool_calls} outil{msg.stats.tool_calls > 1 ? 's' : ''} · {formatMs(msg.stats.elapsed_ms)}
            </span>
          )}
        </div>
      )}
    </>
  );
}
