import React, { useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, LayoutGroup, motion } from 'framer-motion';
import {
  ArrowLeft, AtSign, BadgeCheck, Briefcase, Building2, CalendarDays, Camera, Check, Clock, Globe, Github,
  Image as ImageIcon, Languages, LayoutDashboard, Link2, Linkedin, Loader2, LogIn, Mail, MapPin, MessageSquare,
  MessagesSquare, Mic, Moon, Palette, Paperclip, PenLine, Phone, RotateCcw, Save, Shield, SlidersHorizontal,
  Sparkles, Sun, Trash2, Twitter, UserRound, Volume2, Wand2, Wrench, KeyRound, Cake, Activity,
} from 'lucide-react';
import { apiFetch } from '../../lib/api.js';
import {
  COVER_LABELS, COVER_THEMES, ageFrom, formatDate, listTimezones, resizeAvatar, timeAgo,
} from '../../lib/profileUtils.js';
import {
  ActivityChart, Card, CompletionRing, Field, Input, Segmented, StatCard, TagInput, Toggle,
} from './ui.jsx';
import SecurityTab from './SecurityTab.jsx';

const FORM_KEYS = [
  'name', 'username', 'first_name', 'last_name', 'headline', 'bio', 'phone', 'birth_date', 'gender', 'country',
  'city', 'job_title', 'company', 'website', 'github', 'linkedin', 'twitter', 'interests', 'cover_theme',
  'language', 'timezone', 'pref_theme', 'pref_voice', 'pref_auto_tts', 'pref_web_search', 'system_prompt',
];

const LANGUAGES = { fr: 'Français', darija: 'Darija (الدارجة)', ar: 'العربية', en: 'English' };
const GENDERS = { homme: 'Homme', femme: 'Femme', autre: 'Autre' };
const PROVIDERS = { google: 'Google', github: 'GitHub', email: 'E-mail' };
const COUNTRIES = ['Maroc', 'France', 'Belgique', 'Suisse', 'Canada', 'Espagne', 'Algérie', 'Tunisie', 'Sénégal', 'Émirats arabes unis', 'Arabie saoudite', 'États-Unis', 'Royaume-Uni', 'Allemagne', 'Italie'];
const ACTIVITY_ICONS = { login: LogIn, register: Sparkles, profile_update: PenLine, password: KeyRound, avatar: Camera };

const TABS = [
  { id: 'overview', label: 'Aperçu', icon: LayoutDashboard },
  { id: 'info', label: 'Informations', icon: UserRound },
  { id: 'prefs', label: 'Préférences', icon: SlidersHorizontal },
  { id: 'security', label: 'Sécurité', icon: Shield },
];

function formFrom(data) {
  return Object.fromEntries(
    FORM_KEYS.map((k) => [k, k === 'name' || k === 'username' ? data.user[k] || '' : data.profile[k] ?? ''])
  );
}

function socialUrl(kind, value) {
  if (!value) return '';
  if (/^https?:\/\//.test(value)) return value;
  const handle = value.replace(/^@/, '');
  if (kind === 'github') return `https://github.com/${handle}`;
  if (kind === 'twitter') return `https://x.com/${handle}`;
  if (kind === 'linkedin') return `https://www.linkedin.com/in/${handle}`;
  return value;
}

async function jsonOrThrow(res) {
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Erreur ${res.status}`);
  return data;
}

export default function ProfilePage({ user, onClose, onUserUpdate, onPrefsChange, onLogout, showToast, conversationsCount }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [form, setForm] = useState(null);
  const [saved, setSaved] = useState(null);
  const [tab, setTab] = useState('overview');
  const [saving, setSaving] = useState(false);
  const [avatarBusy, setAvatarBusy] = useState(false);
  const [activity, setActivity] = useState(null);
  const fileRef = useRef(null);
  const timezones = useMemo(listTimezones, []);

  const applyData = (d) => {
    setData(d);
    const f = formFrom(d);
    setForm(f);
    setSaved(f);
    onUserUpdate?.(d.user);
  };

  const load = () => {
    setError('');
    apiFetch('/api/profile')
      .then(jsonOrThrow)
      .then(applyData)
      .catch((e) => setError(e.message));
    apiFetch('/api/profile/activity')
      .then(jsonOrThrow)
      .then((d) => setActivity(d.activity))
      .catch(() => setActivity([]));
  };

  useEffect(load, []);

  const dirtyKeys = useMemo(() => {
    if (!form || !saved) return [];
    return FORM_KEYS.filter((k) => JSON.stringify(form[k]) !== JSON.stringify(saved[k]));
  }, [form, saved]);

  const set = (key) => (value) => setForm((f) => ({ ...f, [key]: value?.target ? value.target.value : value }));

  const save = async () => {
    if (!dirtyKeys.length) return;
    setSaving(true);
    try {
      const payload = Object.fromEntries(dirtyKeys.map((k) => [k, form[k]]));
      const d = await jsonOrThrow(
        await apiFetch('/api/profile', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        })
      );
      applyData(d);
      onPrefsChange?.(d.profile);
      showToast('Profil enregistré');
      apiFetch('/api/profile/activity').then(jsonOrThrow).then((a) => setActivity(a.activity)).catch(() => {});
    } catch (e) {
      showToast(e.message);
    } finally {
      setSaving(false);
    }
  };

  const uploadAvatar = async (file) => {
    if (!file) return;
    setAvatarBusy(true);
    try {
      const dataUrl = await resizeAvatar(file);
      const d = await jsonOrThrow(
        await apiFetch('/api/profile/avatar', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ data_url: dataUrl }),
        })
      );
      setData(d);
      onUserUpdate?.(d.user);
      showToast('Photo de profil mise à jour');
    } catch (e) {
      showToast(e.message);
    } finally {
      setAvatarBusy(false);
    }
  };

  const removeAvatar = async () => {
    setAvatarBusy(true);
    try {
      const d = await jsonOrThrow(
        await apiFetch('/api/profile/avatar', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ data_url: '' }),
        })
      );
      setData(d);
      onUserUpdate?.(d.user);
    } catch (e) {
      showToast(e.message);
    } finally {
      setAvatarBusy(false);
    }
  };

  if (error) {
    return (
      <div className="pf-page pf-center">
        <div className="pf-error-card">
          <Shield size={28} />
          <h3>Profil indisponible</h3>
          <p>{error}</p>
          <div className="pf-modal-actions">
            <button className="pf-btn ghost" onClick={onClose}><ArrowLeft size={16} /> Retour</button>
            <button className="pf-btn primary" onClick={load}><RotateCcw size={16} /> Réessayer</button>
          </div>
        </div>
      </div>
    );
  }

  if (!data || !form) {
    return (
      <div className="pf-page">
        <div className="pf-hero pf-hero-skeleton"><div className="pf-cover" /></div>
        <div className="pf-container"><div className="pf-skeleton tall" /></div>
      </div>
    );
  }

  const u = data.user;
  const p = data.profile;
  const stats = data.stats;
  const location = [p.city, p.country].filter(Boolean).join(', ');
  const fullName = [p.first_name, p.last_name].filter(Boolean).join(' ');
  const age = ageFrom(p.birth_date);
  const ProviderIcon = u.provider === 'github' ? Github : u.provider === 'google' ? Globe : Mail;
  const hasCustomAvatar = (u.avatar || '').startsWith('data:');

  return (
    <div className="pf-page">
      {/* ================= HERO ================= */}
      <div className="pf-hero">
        <motion.div
          className="pf-cover"
          style={{ background: COVER_THEMES[form.cover_theme] || COVER_THEMES.nebula }}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.5 }}
        >
          <span className="pf-cover-blob b1" />
          <span className="pf-cover-blob b2" />
          <span className="pf-cover-grid" />
          <button className="pf-back" onClick={onClose}>
            <ArrowLeft size={16} /> <span>Retour au chat</span>
          </button>
        </motion.div>

        <div className="pf-container pf-hero-body">
          <motion.div
            className="pf-avatar-wrap"
            initial={{ scale: 0.8, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ type: 'spring', stiffness: 260, damping: 22, delay: 0.1 }}
          >
            <img src={u.avatar} alt={u.name} className="pf-avatar" referrerPolicy="no-referrer" />
            <button className="pf-avatar-edit" onClick={() => fileRef.current?.click()} disabled={avatarBusy} title="Changer la photo">
              {avatarBusy ? <Loader2 size={18} className="pf-spin" /> : <Camera size={18} />}
            </button>
            {hasCustomAvatar && !avatarBusy && (
              <button className="pf-avatar-remove" onClick={removeAvatar} title="Retirer la photo">
                <Trash2 size={13} />
              </button>
            )}
            <input
              ref={fileRef}
              type="file"
              accept="image/png,image/jpeg,image/webp"
              hidden
              onChange={(e) => {
                uploadAvatar(e.target.files?.[0]);
                e.target.value = '';
              }}
            />
          </motion.div>

          <div className="pf-identity">
            <motion.h1 initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.15 }}>
              {u.name}
              <BadgeCheck size={22} className="pf-verified" />
            </motion.h1>
            <motion.div className="pf-handle" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.2 }}>
              {u.username && <span>@{u.username}</span>}
              <span className="pf-provider"><ProviderIcon size={13} /> {PROVIDERS[u.provider] || u.provider}</span>
            </motion.div>
            {p.headline && <p className="pf-headline">{p.headline}</p>}
            <div className="pf-meta">
              {location && <span><MapPin size={14} /> {location}</span>}
              {(p.job_title || p.company) && (
                <span><Briefcase size={14} /> {[p.job_title, p.company].filter(Boolean).join(' · ')}</span>
              )}
              <span><CalendarDays size={14} /> Membre depuis {formatDate(u.created_at, { month: 'long', year: 'numeric' })}</span>
            </div>
          </div>

          <motion.button
            className="pf-completion"
            onClick={() => setTab('info')}
            initial={{ opacity: 0, x: 12 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.25 }}
            title={data.completion.missing.length ? `À compléter : ${data.completion.missing.join(', ')}` : 'Profil complet'}
          >
            <CompletionRing percent={data.completion.percent} />
            <span>
              <strong>Profil {data.completion.percent === 100 ? 'complet' : 'complété'}</strong>
              <small>{data.completion.missing.length ? `${data.completion.missing.length} élément(s) à ajouter` : 'Bravo !'}</small>
            </span>
          </motion.button>
        </div>

        {/* Tabs */}
        <div className="pf-container">
          <LayoutGroup id="pf-tabs">
            <nav className="pf-tabs" role="tablist">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  role="tab"
                  aria-selected={tab === t.id}
                  className={`pf-tab ${tab === t.id ? 'active' : ''}`}
                  onClick={() => setTab(t.id)}
                >
                  <t.icon size={16} />
                  <span>{t.label}</span>
                  {tab === t.id && <motion.span layoutId="pf-tab-line" className="pf-tab-line" />}
                </button>
              ))}
            </nav>
          </LayoutGroup>
        </div>
      </div>

      {/* ================= CONTENT ================= */}
      <div className="pf-container pf-content">
        <AnimatePresence mode="wait">
          <motion.div
            key={tab}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
          >
            {tab === 'overview' && (
              <>
                <div className="pf-stats">
                  <StatCard icon={MessageSquare} label="Messages envoyés" value={stats.messages_sent} tone="blue" />
                  <StatCard icon={MessagesSquare} label="Discussions" value={conversationsCount} tone="cyan" delay={0.04} />
                  <StatCard icon={Wrench} label="Outils utilisés" value={stats.tool_calls} tone="teal" delay={0.08} />
                  <StatCard icon={Mic} label="Échanges vocaux" value={stats.voice_turns} tone="violet" delay={0.12} />
                  <StatCard icon={Paperclip} label="Fichiers analysés" value={stats.files_uploaded} tone="amber" delay={0.16} />
                  <StatCard icon={ImageIcon} label="Images créées" value={stats.images_generated} tone="pink" delay={0.2} />
                </div>

                <div className="pf-grid-main">
                  <div className="pf-col">
                    <Card title="Activité" icon={Activity} delay={0.05}
                          action={<span className="pf-muted sm">Dernière activité {timeAgo(stats.last_active_at)}</span>}>
                      <ActivityChart daily={stats.daily} />
                    </Card>

                    <Card title="À propos" icon={UserRound} delay={0.1}
                          action={<button className="pf-btn ghost sm" onClick={() => setTab('info')}><PenLine size={14} /> Modifier</button>}>
                      {p.bio ? <p className="pf-bio" dir="auto">{p.bio}</p> : (
                        <button className="pf-empty" onClick={() => setTab('info')}>
                          <PenLine size={16} /> Ajoutez une bio pour vous présenter
                        </button>
                      )}
                      <dl className="pf-details">
                        <div><dt><Mail size={14} /> E-mail</dt><dd>{u.email}</dd></div>
                        {fullName && <div><dt><UserRound size={14} /> Nom complet</dt><dd>{fullName}</dd></div>}
                        {p.phone && <div><dt><Phone size={14} /> Téléphone</dt><dd>{p.phone}</dd></div>}
                        {p.birth_date && (
                          <div><dt><Cake size={14} /> Naissance</dt>
                            <dd>{new Date(p.birth_date + 'T00:00:00').toLocaleDateString('fr-FR', { day: 'numeric', month: 'long', year: 'numeric' })}{age != null && ` · ${age} ans`}</dd>
                          </div>
                        )}
                        {p.gender && <div><dt><UserRound size={14} /> Genre</dt><dd>{GENDERS[p.gender]}</dd></div>}
                        {p.company && <div><dt><Building2 size={14} /> Entreprise</dt><dd>{p.company}</dd></div>}
                        <div><dt><Languages size={14} /> Langue</dt><dd>{LANGUAGES[p.language] || p.language}</dd></div>
                        <div><dt><Clock size={14} /> Fuseau</dt><dd>{p.timezone}</dd></div>
                        <div><dt><LogIn size={14} /> Dernière connexion</dt><dd>{timeAgo(u.last_login_at)}</dd></div>
                      </dl>
                      {p.interests.length > 0 && (
                        <div className="pf-chips">
                          {p.interests.map((t) => <span key={t} className="pf-chip">{t}</span>)}
                        </div>
                      )}
                      {(p.website || p.github || p.linkedin || p.twitter) && (
                        <div className="pf-socials">
                          {p.website && <a href={p.website} target="_blank" rel="noopener noreferrer"><Link2 size={15} /> {p.website.replace(/^https?:\/\//, '')}</a>}
                          {p.github && <a href={socialUrl('github', p.github)} target="_blank" rel="noopener noreferrer"><Github size={15} /> {p.github}</a>}
                          {p.linkedin && <a href={socialUrl('linkedin', p.linkedin)} target="_blank" rel="noopener noreferrer"><Linkedin size={15} /> LinkedIn</a>}
                          {p.twitter && <a href={socialUrl('twitter', p.twitter)} target="_blank" rel="noopener noreferrer"><Twitter size={15} /> {p.twitter}</a>}
                        </div>
                      )}
                    </Card>
                  </div>

                  <div className="pf-col">
                    {data.completion.missing.length > 0 && (
                      <Card title="Complétez votre profil" icon={Sparkles} delay={0.08} className="pf-complete-card">
                        <p className="pf-muted pf-mb">Un profil complet aide Vasco à personnaliser ses réponses.</p>
                        <ul className="pf-missing">
                          {data.completion.missing.map((m) => (
                            <li key={m}><button onClick={() => setTab('info')}><span className="pf-dot" /> {m}</button></li>
                          ))}
                        </ul>
                      </Card>
                    )}
                    <Card title="Activité récente" icon={Clock} delay={0.12}>
                      {activity === null ? (
                        <div className="pf-skeleton-list">{[0, 1, 2].map((i) => <div key={i} className="pf-skeleton" />)}</div>
                      ) : activity.length === 0 ? (
                        <p className="pf-muted">Aucune activité pour le moment.</p>
                      ) : (
                        <ol className="pf-timeline">
                          {activity.slice(0, 8).map((a, i) => {
                            const Icon = ACTIVITY_ICONS[a.kind] || Activity;
                            return (
                              <motion.li key={i} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.15 + i * 0.04 }}>
                                <span className={`pf-tl-icon k-${a.kind}`}><Icon size={14} /></span>
                                <span className="pf-tl-text">
                                  <strong>{a.label}</strong>
                                  <small>{timeAgo(a.at)}{a.ip ? ` · ${a.ip}` : ''}</small>
                                </span>
                              </motion.li>
                            );
                          })}
                        </ol>
                      )}
                    </Card>
                  </div>
                </div>
              </>
            )}

            {tab === 'info' && (
              <div className="pf-grid-2">
                <Card title="Identité" icon={UserRound}>
                  <div className="pf-form-grid">
                    <Field label="Nom affiché" full><Input value={form.name} onChange={set('name')} maxLength={120} /></Field>
                    <Field label="Nom d’utilisateur" icon={AtSign} full hint="3 à 40 caractères : lettres, chiffres, _ . -">
                      <Input prefix="@" value={form.username} onChange={set('username')} maxLength={40} placeholder="votre_pseudo" />
                    </Field>
                    <Field label="Prénom"><Input value={form.first_name} onChange={set('first_name')} maxLength={80} /></Field>
                    <Field label="Nom"><Input value={form.last_name} onChange={set('last_name')} maxLength={80} /></Field>
                    <Field label="Titre" full><Input value={form.headline} onChange={set('headline')} maxLength={140} placeholder="Ex. Développeur full-stack passionné d’IA" /></Field>
                    <Field label="Bio" full counter={`${form.bio.length}/1000`}>
                      <textarea className="pf-input" rows={4} dir="auto" value={form.bio} onChange={set('bio')} maxLength={1000} placeholder="Parlez un peu de vous…" />
                    </Field>
                    <Field label="Date de naissance" icon={Cake}>
                      <Input type="date" value={form.birth_date} onChange={set('birth_date')} max={new Date().toISOString().slice(0, 10)} />
                    </Field>
                    <Field label="Genre">
                      <Segmented layoutId="pf-gender" value={form.gender} onChange={set('gender')}
                                 options={[{ value: 'homme', label: 'Homme' }, { value: 'femme', label: 'Femme' }, { value: 'autre', label: 'Autre' }]} />
                    </Field>
                  </div>
                </Card>

                <div className="pf-col">
                  <Card title="Coordonnées & travail" icon={Briefcase}>
                    <div className="pf-form-grid">
                      <Field label="Téléphone" icon={Phone}><Input type="tel" value={form.phone} onChange={set('phone')} placeholder="+212 6 12 34 56 78" /></Field>
                      <Field label="Ville" icon={MapPin}><Input value={form.city} onChange={set('city')} placeholder="Casablanca" /></Field>
                      <Field label="Pays" full>
                        <Input list="pf-countries" value={form.country} onChange={set('country')} placeholder="Maroc" />
                        <datalist id="pf-countries">{COUNTRIES.map((c) => <option key={c} value={c} />)}</datalist>
                      </Field>
                      <Field label="Poste" icon={Briefcase}><Input value={form.job_title} onChange={set('job_title')} placeholder="Ingénieur logiciel" /></Field>
                      <Field label="Entreprise / École" icon={Building2}><Input value={form.company} onChange={set('company')} /></Field>
                    </div>
                  </Card>
                  <Card title="Liens & réseaux" icon={Link2}>
                    <div className="pf-form-grid">
                      <Field label="Site web" icon={Globe} full><Input value={form.website} onChange={set('website')} placeholder="https://monsite.com" /></Field>
                      <Field label="GitHub" icon={Github}><Input prefix="@" value={form.github} onChange={set('github')} /></Field>
                      <Field label="X / Twitter" icon={Twitter}><Input prefix="@" value={form.twitter} onChange={set('twitter')} /></Field>
                      <Field label="LinkedIn" icon={Linkedin} full><Input value={form.linkedin} onChange={set('linkedin')} placeholder="URL ou identifiant" /></Field>
                    </div>
                  </Card>
                  <Card title="Centres d’intérêt" icon={Sparkles}>
                    <TagInput value={form.interests || []} onChange={set('interests')} placeholder="IA, football, cuisine… (Entrée pour ajouter)" />
                  </Card>
                </div>
              </div>
            )}

            {tab === 'prefs' && (
              <div className="pf-grid-2">
                <Card title="Apparence" icon={Palette}>
                  <Field label="Thème de l’interface">
                    <Segmented layoutId="pf-theme" value={form.pref_theme} onChange={set('pref_theme')}
                               options={[{ value: 'dark', label: 'Sombre', icon: Moon }, { value: 'light', label: 'Clair', icon: Sun }]} />
                  </Field>
                  <div className="pf-field">
                    <span className="pf-field-label">Couverture du profil</span>
                    <div className="pf-covers">
                      {Object.entries(COVER_THEMES).map(([key, bg]) => (
                        <button
                          key={key}
                          type="button"
                          className={`pf-cover-swatch ${form.cover_theme === key ? 'active' : ''}`}
                          style={{ background: bg }}
                          onClick={() => set('cover_theme')(key)}
                          title={COVER_LABELS[key]}
                        >
                          {form.cover_theme === key && <Check size={16} />}
                          <span>{COVER_LABELS[key]}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                </Card>

                <Card title="Langue & région" icon={Languages}>
                  <div className="pf-form-grid">
                    <Field label="Langue préférée" full>
                      <Segmented layoutId="pf-lang" value={form.language} onChange={set('language')}
                                 options={Object.entries(LANGUAGES).map(([value, label]) => ({ value, label: label.split(' ')[0] }))} />
                    </Field>
                    <Field label="Fuseau horaire" icon={Clock} full>
                      <select className="pf-input" value={form.timezone} onChange={set('timezone')}>
                        {!timezones.includes(form.timezone) && <option value={form.timezone}>{form.timezone}</option>}
                        {timezones.map((tz) => <option key={tz} value={tz}>{tz.replace(/_/g, ' ')}</option>)}
                      </select>
                    </Field>
                  </div>
                </Card>

                <Card title="Agent & voix" icon={Wand2} className="full">
                  <div className="pf-toggles">
                    <Toggle icon={Volume2} checked={!!form.pref_auto_tts} onChange={set('pref_auto_tts')}
                            label="Lecture automatique" description="Vasco lit ses réponses à voix haute" />
                    <Toggle icon={Globe} checked={!!form.pref_web_search} onChange={set('pref_web_search')}
                            label="Recherche Web par défaut" description="Activer « Web Live » à chaque nouvelle session" />
                  </div>
                  <Field label="Voix de Vasco">
                    <Segmented layoutId="pf-voice" value={form.pref_voice} onChange={set('pref_voice')}
                               options={[{ value: 'male', label: 'Masculine' }, { value: 'female', label: 'Féminine' }]} />
                  </Field>
                  <Field label="Instructions personnalisées" full counter={`${form.system_prompt.length}/4000`}
                         hint="Vasco suit ces consignes dans toutes vos conversations.">
                    <textarea className="pf-input" rows={5} dir="auto" maxLength={4000} value={form.system_prompt}
                              onChange={set('system_prompt')} placeholder="Ex. Réponds toujours de façon concise, avec des exemples en Darija." />
                  </Field>
                </Card>
              </div>
            )}

            {tab === 'security' && (
              <SecurityTab
                hasPassword={u.has_password}
                provider={u.provider}
                showToast={showToast}
                onLogout={onLogout}
                onPasswordSet={() => setData((d) => ({ ...d, user: { ...d.user, has_password: true } }))}
              />
            )}
          </motion.div>
        </AnimatePresence>
      </div>

      {/* ================= SAVE BAR ================= */}
      <AnimatePresence>
        {dirtyKeys.length > 0 && tab !== 'security' && (
          <motion.div
            className="pf-savebar"
            initial={{ y: 80, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: 80, opacity: 0 }}
            transition={{ type: 'spring', stiffness: 380, damping: 30 }}
          >
            <span className="pf-savebar-dot" />
            <span className="pf-savebar-text">
              <span className="pf-long">
                {dirtyKeys.length} modification{dirtyKeys.length > 1 ? 's' : ''} non enregistrée{dirtyKeys.length > 1 ? 's' : ''}
              </span>
              <span className="pf-short">{dirtyKeys.length} modif.</span>
            </span>
            <button className="pf-btn ghost sm" onClick={() => setForm(saved)} disabled={saving}>
              <RotateCcw size={14} /> Annuler
            </button>
            <button className="pf-btn primary sm" onClick={save} disabled={saving}>
              {saving ? <Loader2 size={14} className="pf-spin" /> : <Save size={14} />} Enregistrer
            </button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
