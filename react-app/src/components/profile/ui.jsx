import React, { useState } from 'react';
import { motion } from 'framer-motion';
import { X } from 'lucide-react';

export function Field({ label, hint, icon: Icon, children, full, counter }) {
  return (
    <label className={`pf-field ${full ? 'full' : ''}`}>
      <span className="pf-field-label">
        {Icon && <Icon size={14} />}
        {label}
        {counter && <span className="pf-counter">{counter}</span>}
      </span>
      {children}
      {hint && <span className="pf-field-hint">{hint}</span>}
    </label>
  );
}

export function Input({ prefix, ...props }) {
  if (!prefix) return <input className="pf-input" {...props} />;
  return (
    <div className="pf-input-group">
      <span className="pf-input-prefix">{prefix}</span>
      <input className="pf-input" {...props} />
    </div>
  );
}

export function Segmented({ value, options, onChange, layoutId }) {
  return (
    <div className="pf-seg" role="radiogroup">
      {options.map((o) => {
        const active = o.value === value;
        const Icon = o.icon;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={active}
            className={`pf-seg-btn ${active ? 'active' : ''}`}
            onClick={() => onChange(o.value)}
          >
            {active && <motion.span layoutId={layoutId} className="pf-seg-pill" transition={{ type: 'spring', stiffness: 500, damping: 38 }} />}
            <span className="pf-seg-label">
              {Icon && <Icon size={14} />}
              {o.label}
            </span>
          </button>
        );
      })}
    </div>
  );
}

export function Toggle({ checked, onChange, label, description, icon: Icon }) {
  return (
    <button type="button" className="pf-toggle-row" role="switch" aria-checked={checked} onClick={() => onChange(!checked)}>
      {Icon && (
        <span className="pf-toggle-icon">
          <Icon size={18} />
        </span>
      )}
      <span className="pf-toggle-text">
        <strong>{label}</strong>
        {description && <small>{description}</small>}
      </span>
      <span className={`pf-switch ${checked ? 'on' : ''}`}>
        <motion.span layout className="pf-switch-knob" transition={{ type: 'spring', stiffness: 600, damping: 34 }} />
      </span>
    </button>
  );
}

export function TagInput({ value = [], onChange, max = 12, placeholder }) {
  const [draft, setDraft] = useState('');
  const add = () => {
    const t = draft.trim().replace(/,$/, '');
    if (!t || value.length >= max || value.some((v) => v.toLowerCase() === t.toLowerCase())) {
      setDraft('');
      return;
    }
    onChange([...value, t.slice(0, 32)]);
    setDraft('');
  };
  return (
    <div className="pf-tags-input">
      {value.map((t) => (
        <motion.span key={t} layout initial={{ scale: 0.7, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} className="pf-tag">
          {t}
          <button type="button" aria-label={`Retirer ${t}`} onClick={() => onChange(value.filter((v) => v !== t))}>
            <X size={12} />
          </button>
        </motion.span>
      ))}
      {value.length < max && (
        <input
          value={draft}
          placeholder={value.length ? 'Ajouter…' : placeholder}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ',') {
              e.preventDefault();
              add();
            } else if (e.key === 'Backspace' && !draft && value.length) {
              onChange(value.slice(0, -1));
            }
          }}
          onBlur={add}
        />
      )}
    </div>
  );
}

export function StatCard({ icon: Icon, label, value, tone = 'blue', delay = 0 }) {
  return (
    <motion.div
      className={`pf-stat tone-${tone}`}
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay, duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
    >
      <span className="pf-stat-icon">
        <Icon size={18} />
      </span>
      <span className="pf-stat-value">{Number(value || 0).toLocaleString('fr-FR')}</span>
      <span className="pf-stat-label">{label}</span>
    </motion.div>
  );
}

export function CompletionRing({ percent = 0, size = 64 }) {
  const stroke = 6;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  return (
    <div className="pf-ring" style={{ width: size, height: size }}>
      <svg width={size} height={size}>
        <defs>
          <linearGradient id="pfRingGrad" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="#a78bfa" />
            <stop offset="100%" stopColor="#22d3ee" />
          </linearGradient>
        </defs>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--pf-track)" strokeWidth={stroke} />
        <motion.circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="url(#pfRingGrad)"
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={c}
          initial={{ strokeDashoffset: c }}
          animate={{ strokeDashoffset: c * (1 - percent / 100) }}
          transition={{ duration: 1, ease: [0.22, 1, 0.36, 1] }}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
      </svg>
      <span className="pf-ring-value">{percent}%</span>
    </div>
  );
}

export function ActivityChart({ daily = [] }) {
  const max = Math.max(1, ...daily.map((d) => d.messages));
  const total = daily.reduce((s, d) => s + d.messages, 0);
  return (
    <div className="pf-chart">
      <div className="pf-chart-bars">
        {daily.map((d, i) => {
          const date = new Date(d.day + 'T00:00:00');
          const label = date.toLocaleDateString('fr-FR', { weekday: 'short', day: 'numeric', month: 'short' });
          return (
            <div key={d.day} className="pf-chart-col" title={`${label} : ${d.messages} message${d.messages > 1 ? 's' : ''}`}>
              <motion.div
                className={`pf-chart-bar ${d.messages ? '' : 'empty'}`}
                initial={{ height: 0 }}
                animate={{ height: `${d.messages ? Math.max(8, (d.messages / max) * 100) : 4}%` }}
                transition={{ delay: i * 0.03, duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
              />
              <span className="pf-chart-day">{date.toLocaleDateString('fr-FR', { weekday: 'narrow' })}</span>
            </div>
          );
        })}
      </div>
      <div className="pf-chart-foot">
        <span>14 derniers jours</span>
        <strong>{total} message{total > 1 ? 's' : ''}</strong>
      </div>
    </div>
  );
}

export function Card({ title, icon: Icon, action, children, className = '', delay = 0 }) {
  return (
    <motion.section
      className={`pf-card ${className}`}
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay, duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
    >
      {(title || action) && (
        <header className="pf-card-head">
          <h3>
            {Icon && <Icon size={16} />}
            {title}
          </h3>
          {action}
        </header>
      )}
      {children}
    </motion.section>
  );
}
