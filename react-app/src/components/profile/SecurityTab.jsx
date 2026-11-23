import React, { useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
  AlertTriangle, Eye, EyeOff, KeyRound, Laptop, Loader2, LogOut, Monitor, ShieldCheck, Smartphone, Trash2,
} from 'lucide-react';
import { apiFetch } from '../../lib/api.js';
import { parseUserAgent, passwordStrength, timeAgo } from '../../lib/profileUtils.js';
import { Card, Field } from './ui.jsx';

async function jsonOrThrow(res) {
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Erreur ${res.status}`);
  return data;
}

function PasswordInput({ value, onChange, placeholder, autoComplete }) {
  const [show, setShow] = useState(false);
  return (
    <div className="pf-input-group">
      <input
        className="pf-input"
        type={show ? 'text' : 'password'}
        value={value}
        placeholder={placeholder}
        autoComplete={autoComplete}
        onChange={(e) => onChange(e.target.value)}
      />
      <button type="button" className="pf-input-suffix" onClick={() => setShow(!show)} aria-label={show ? 'Masquer' : 'Afficher'}>
        {show ? <EyeOff size={16} /> : <Eye size={16} />}
      </button>
    </div>
  );
}

export default function SecurityTab({ hasPassword, provider, showToast, onLogout, onPasswordSet }) {
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [saving, setSaving] = useState(false);
  const [sessions, setSessions] = useState(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deletePwd, setDeletePwd] = useState('');
  const [deleting, setDeleting] = useState(false);

  const strength = passwordStrength(next);

  const loadSessions = () =>
    apiFetch('/api/profile/sessions')
      .then(jsonOrThrow)
      .then((d) => setSessions(d.sessions))
      .catch(() => setSessions([]));

  useEffect(() => {
    loadSessions();
  }, []);

  const submitPassword = async (e) => {
    e.preventDefault();
    if (next.length < 6) return showToast('Le mot de passe doit contenir au moins 6 caractères.');
    if (next !== confirm) return showToast('Les deux mots de passe ne correspondent pas.');
    setSaving(true);
    try {
      const d = await jsonOrThrow(
        await apiFetch('/api/profile/password', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ current_password: current, new_password: next }),
        })
      );
      setCurrent('');
      setNext('');
      setConfirm('');
      onPasswordSet();
      showToast(
        d.sessions_closed
          ? `Mot de passe mis à jour · ${d.sessions_closed} autre(s) session(s) fermée(s)`
          : 'Mot de passe mis à jour'
      );
      loadSessions();
    } catch (err) {
      showToast(err.message);
    } finally {
      setSaving(false);
    }
  };

  const revoke = async (id) => {
    try {
      await jsonOrThrow(await apiFetch(`/api/profile/sessions/${id}`, { method: 'DELETE' }));
      setSessions((prev) => prev.filter((s) => s.id !== id));
      showToast('Session fermée');
    } catch (err) {
      showToast(err.message);
    }
  };

  const revokeOthers = async () => {
    try {
      const d = await jsonOrThrow(await apiFetch('/api/profile/sessions', { method: 'DELETE' }));
      showToast(`${d.closed} session(s) fermée(s)`);
      loadSessions();
    } catch (err) {
      showToast(err.message);
    }
  };

  const deleteAccount = async () => {
    setDeleting(true);
    try {
      await jsonOrThrow(
        await apiFetch('/api/profile/delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(hasPassword ? { password: deletePwd } : { confirm: deletePwd }),
        })
      );
      showToast('Compte supprimé. Au revoir !');
      onLogout(true);
    } catch (err) {
      showToast(err.message);
      setDeleting(false);
    }
  };

  const others = (sessions || []).filter((s) => !s.current).length;

  return (
    <div className="pf-grid-2">
      <Card title={hasPassword ? 'Changer le mot de passe' : 'Définir un mot de passe'} icon={KeyRound}>
        {!hasPassword && (
          <p className="pf-muted pf-mb">
            Votre compte utilise la connexion {provider === 'github' ? 'GitHub' : 'Google'}. Ajoutez un mot de passe pour
            pouvoir aussi vous connecter avec votre e-mail.
          </p>
        )}
        <form className="pf-form-stack" onSubmit={submitPassword}>
          {hasPassword && (
            <Field label="Mot de passe actuel">
              <PasswordInput value={current} onChange={setCurrent} autoComplete="current-password" />
            </Field>
          )}
          <Field label="Nouveau mot de passe">
            <PasswordInput value={next} onChange={setNext} autoComplete="new-password" placeholder="6 caractères minimum" />
          </Field>
          {next && (
            <div className="pf-strength">
              <div className="pf-strength-bars">
                {[0, 1, 2, 3].map((i) => (
                  <span key={i} className={i < Math.max(1, strength.level) ? `on lvl-${strength.level}` : ''} />
                ))}
              </div>
              <small>{strength.label}</small>
            </div>
          )}
          <Field label="Confirmer le mot de passe">
            <PasswordInput value={confirm} onChange={setConfirm} autoComplete="new-password" />
          </Field>
          <button className="pf-btn primary" type="submit" disabled={saving || !next}>
            {saving ? <Loader2 size={16} className="pf-spin" /> : <ShieldCheck size={16} />}
            {hasPassword ? 'Mettre à jour' : 'Définir le mot de passe'}
          </button>
        </form>
      </Card>

      <Card
        title="Sessions actives"
        icon={Monitor}
        action={
          others > 0 && (
            <button className="pf-btn ghost sm" onClick={revokeOthers}>
              <LogOut size={14} /> Fermer les autres
            </button>
          )
        }
      >
        {sessions === null ? (
          <div className="pf-skeleton-list">
            {[0, 1].map((i) => <div key={i} className="pf-skeleton" />)}
          </div>
        ) : (
          <ul className="pf-sessions">
            <AnimatePresence initial={false}>
              {sessions.map((s) => {
                const ua = parseUserAgent(s.user_agent);
                const DeviceIcon = ua.mobile ? Smartphone : ua.os ? Laptop : Monitor;
                return (
                  <motion.li
                    key={s.id}
                    layout
                    initial={{ opacity: 0, x: -8 }}
                    animate={{ opacity: 1, x: 0 }}
                    exit={{ opacity: 0, x: 16, height: 0, marginBottom: 0, paddingTop: 0, paddingBottom: 0 }}
                    className={`pf-session ${s.current ? 'current' : ''}`}
                  >
                    <span className="pf-session-icon">
                      <DeviceIcon size={18} />
                    </span>
                    <span className="pf-session-text">
                      <strong>
                        {s.user_agent ? ua.label : `Connexion ${s.method}`}
                        {s.current && <span className="pf-badge ok">Cet appareil</span>}
                      </strong>
                      <small>
                        {s.ip ? `${s.ip} · ` : ''}actif {timeAgo(s.last_seen_at)} · via {s.method}
                      </small>
                    </span>
                    {!s.current && (
                      <button className="pf-icon-btn danger" onClick={() => revoke(s.id)} title="Fermer cette session">
                        <LogOut size={15} />
                      </button>
                    )}
                  </motion.li>
                );
              })}
            </AnimatePresence>
          </ul>
        )}
      </Card>

      <Card title="Zone de danger" icon={AlertTriangle} className="pf-danger full">
        <div className="pf-danger-row">
          <div>
            <strong>Supprimer mon compte</strong>
            <p className="pf-muted">
              Votre profil, vos préférences, vos statistiques et toutes vos sessions seront définitivement effacés de la
              base de données.
            </p>
          </div>
          <button className="pf-btn danger" onClick={() => setDeleteOpen(true)}>
            <Trash2 size={16} /> Supprimer le compte
          </button>
        </div>
      </Card>

      <AnimatePresence>
        {deleteOpen && (
          <motion.div className="pf-modal-backdrop" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
                      onClick={() => !deleting && setDeleteOpen(false)}>
            <motion.div
              className="pf-modal"
              initial={{ opacity: 0, scale: 0.94, y: 12 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.96, y: 8 }}
              transition={{ type: 'spring', stiffness: 420, damping: 32 }}
              onClick={(e) => e.stopPropagation()}
            >
              <span className="pf-modal-icon"><AlertTriangle size={22} /></span>
              <h3>Supprimer définitivement votre compte ?</h3>
              <p className="pf-muted">Cette action est irréversible.</p>
              <Field label={hasPassword ? 'Confirmez avec votre mot de passe' : 'Tapez SUPPRIMER pour confirmer'}>
                <input
                  className="pf-input"
                  type={hasPassword ? 'password' : 'text'}
                  value={deletePwd}
                  autoFocus
                  onChange={(e) => setDeletePwd(e.target.value)}
                />
              </Field>
              <div className="pf-modal-actions">
                <button className="pf-btn ghost" onClick={() => setDeleteOpen(false)} disabled={deleting}>Annuler</button>
                <button className="pf-btn danger" onClick={deleteAccount} disabled={deleting || !deletePwd}>
                  {deleting ? <Loader2 size={16} className="pf-spin" /> : <Trash2 size={16} />} Supprimer
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
