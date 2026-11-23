import React, { useState, useEffect, useRef } from 'react';

export default function AuthPage({ onAuthSuccess }) {
  const [mode, setMode] = useState('login'); // 'login' | 'register'
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [oauthWaiting, setOauthWaiting] = useState(''); // '' | 'google' | 'github'

  // Real GitHub OAuth 2.0 Device Flow state
  const [ghDevice, setGhDevice] = useState(null); // { device_code, user_code, verification_uri, interval }
  const [codeCopied, setCodeCopied] = useState(false);

  const pollTimerRef = useRef(null);
  const popupRef = useRef(null);

  const clearPolling = () => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  };

  useEffect(() => {
    const handleMessage = (event) => {
      const data = event.data;
      if (!data || typeof data !== 'object') return;
      if (data.type === 'VASCO_OAUTH_SUCCESS' && data.token && data.user) {
        clearPolling();
        setOauthWaiting('');
        setGhDevice(null);
        onAuthSuccess(data.user, data.token);
      } else if (data.type === 'VASCO_OAUTH_ERROR') {
        clearPolling();
        setOauthWaiting('');
        setError(data.error || "Erreur d'authentification OAuth.");
      }
    };
    window.addEventListener('message', handleMessage);
    return () => {
      window.removeEventListener('message', handleMessage);
      clearPolling();
    };
  }, [onAuthSuccess]);

  const handleEmailSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    const endpoint = mode === 'register' ? '/api/auth/register' : '/api/auth/login';
    const payload = mode === 'register' ? { name, email, password } : { email, password };

    try {
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail || "Erreur d'authentification.");
      } else {
        onAuthSuccess(data.user, data.token);
      }
    } catch (_) {
      setError('Impossible de contacter le serveur Vasco.');
    } finally {
      setLoading(false);
    }
  };

  const startStatePolling = (stateId) => {
    clearPolling();
    pollTimerRef.current = setInterval(async () => {
      try {
        const res = await fetch(`/api/auth/oauth/status?state=${encodeURIComponent(stateId)}`);
        const data = await res.json();
        if (data.status === 'completed' && data.token && data.user) {
          clearPolling();
          setOauthWaiting('');
          onAuthSuccess(data.user, data.token);
        } else if (data.status === 'error') {
          clearPolling();
          setOauthWaiting('');
          setError(data.error || 'Authentification OAuth annulée.');
        }
      } catch (_) {}
    }, 1000);
  };

  // REAL GOOGLE OAUTH 2.0 LOGIN (https://accounts.google.com)
  const handleRealGoogleLogin = () => {
    setError('');
    setOauthWaiting('google');
    const stateId = 'st_' + Math.random().toString(36).slice(2) + '_' + Date.now();
    const width = 520;
    const height = 640;
    const left = window.screenX + (window.outerWidth - width) / 2;
    const top = window.screenY + (window.outerHeight - height) / 2;

    popupRef.current = window.open(
      `/api/auth/google/login?state=${encodeURIComponent(stateId)}`,
      'vasco_google_oauth',
      `width=${width},height=${height},left=${left},top=${top},status=no,menubar=no,toolbar=no`
    );

    startStatePolling(stateId);
  };

  // REAL GITHUB OAUTH 2.0 LOGIN (https://github.com/login/oauth or https://github.com/login/device)
  const handleRealGitHubLogin = async () => {
    setError('');
    setOauthWaiting('github');
    const stateId = 'st_' + Math.random().toString(36).slice(2) + '_' + Date.now();

    try {
      const res = await fetch(`/api/auth/github/device/start?state=${encodeURIComponent(stateId)}`, {
        method: 'POST',
      });
      const data = await res.json();
      if (!res.ok) {
        setOauthWaiting('');
        setError(data.detail || 'Impossible de démarrer GitHub OAuth.');
        return;
      }

      const width = 540;
      const height = 660;
      const left = window.screenX + (window.outerWidth - width) / 2;
      const top = window.screenY + (window.outerHeight - height) / 2;

      if (data.mode === 'web_redirect') {
        popupRef.current = window.open(
          data.authorize_url,
          'vasco_github_oauth',
          `width=${width},height=${height},left=${left},top=${top}`
        );
        startStatePolling(stateId);
        return;
      }

      // Official GitHub OAuth 2.0 Device Flow (100% real github.com account)
      setGhDevice(data);
      try {
        await navigator.clipboard.writeText(data.user_code);
        setCodeCopied(true);
      } catch (_) {
        setCodeCopied(false);
      }

      // Open official GitHub verification page in popup
      popupRef.current = window.open(
        data.verification_uri || 'https://github.com/login/device',
        'vasco_github_device',
        `width=${width},height=${height},left=${left},top=${top}`
      );

      clearPolling();
      const intervalMs = Math.max((data.interval || 5) * 1000, 3500);
      pollTimerRef.current = setInterval(async () => {
        try {
          const pollRes = await fetch('/api/auth/github/device/poll', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ device_code: data.device_code }),
          });
          const pollData = await pollRes.json();
          if (pollRes.ok && pollData.status === 'completed' && pollData.token && pollData.user) {
            clearPolling();
            setGhDevice(null);
            setOauthWaiting('');
            if (popupRef.current && !popupRef.current.closed) {
              try { popupRef.current.close(); } catch (_) {}
            }
            onAuthSuccess(pollData.user, pollData.token);
          } else if (!pollRes.ok) {
            clearPolling();
            setGhDevice(null);
            setOauthWaiting('');
            setError(pollData.detail || 'Autorisation GitHub refusée ou expirée.');
          }
        } catch (_) {}
      }, intervalMs);
    } catch (_) {
      setOauthWaiting('');
      setError('Erreur réseau lors de la connexion à GitHub OAuth.');
    }
  };

  const cancelGitHubDevice = () => {
    clearPolling();
    setGhDevice(null);
    setOauthWaiting('');
  };

  return (
    <div className="auth-screen">
      <div className="auth-ambient-glow glow-1" />
      <div className="auth-ambient-glow glow-2" />

      <div className="auth-card">
        {/* Vasco Brand Header */}
        <div className="auth-brand">
          <div className="auth-logo">
            <svg viewBox="0 0 36 36" fill="none" xmlns="http://www.w3.org/2000/svg">
              <defs>
                <linearGradient id="vascoAuthGrad" x1="0" y1="0" x2="36" y2="36" gradientUnits="userSpaceOnUse">
                  <stop offset="0%" stopColor="#2563eb" />
                  <stop offset="50%" stopColor="#0891b2" />
                  <stop offset="100%" stopColor="#14b8a6" />
                </linearGradient>
              </defs>
              <rect width="36" height="36" rx="10" fill="url(#vascoAuthGrad)" />
              <path d="M18 6L21.5 14.5L30 18L21.5 21.5L18 30L14.5 21.5L6 18L14.5 14.5L18 6Z" fill="white" />
              <circle cx="18" cy="18" r="2.5" fill="#2563eb" />
            </svg>
          </div>
          <h2>{mode === 'login' ? 'Connexion à Vasco AI' : 'Créer un compte Vasco AI'}</h2>
          <p>
            {mode === 'login'
              ? 'Authentifiez-vous avec votre vrai compte Google, GitHub ou E-mail'
              : 'Rejoignez Vasco AI pour sauvegarder vos discussions et fichiers'}
          </p>
        </div>

        {/* Mode Switch Tabs */}
        <div className="auth-tabs">
          <button
            type="button"
            className={`auth-tab ${mode === 'login' ? 'active' : ''}`}
            onClick={() => { setMode('login'); setError(''); }}
          >
            Se connecter
          </button>
          <button
            type="button"
            className={`auth-tab ${mode === 'register' ? 'active' : ''}`}
            onClick={() => { setMode('register'); setError(''); }}
          >
            S'inscrire
          </button>
        </div>

        {/* Real Google & GitHub Icon-Only OAuth Buttons */}
        <div className="oauth-icon-row">
          {/* Real Google OAuth 2.0 Icon-Only Button */}
          <button
            type="button"
            className="oauth-icon-only-btn google-icon-btn"
            onClick={handleRealGoogleLogin}
            title="Connexion officielle avec Google (accounts.google.com)"
            aria-label="Connexion avec Google"
          >
            <svg viewBox="0 0 24 24" width="26" height="26" xmlns="http://www.w3.org/2000/svg">
              <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4" />
              <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853" />
              <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z" fill="#FBBC05" />
              <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z" fill="#EA4335" />
            </svg>
          </button>

          {/* Real GitHub OAuth 2.0 Icon-Only Button */}
          <button
            type="button"
            className="oauth-icon-only-btn github-icon-btn"
            onClick={handleRealGitHubLogin}
            title="Connexion officielle avec GitHub (github.com)"
            aria-label="Connexion avec GitHub"
          >
            <svg viewBox="0 0 24 24" width="26" height="26" fill="currentColor" xmlns="http://www.w3.org/2000/svg">
              <path
                fillRule="evenodd"
                clipRule="evenodd"
                d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.53 1.032 1.53 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z"
              />
            </svg>
          </button>
        </div>

        {oauthWaiting === 'google' && (
          <div style={{ textAlign: 'center', fontSize: '0.8rem', color: '#93c5fd', marginTop: '8px' }}>
            Fenêtre Google ouverte sur <strong>accounts.google.com</strong>... Sélectionnez votre compte Google.
          </div>
        )}

        <div className="auth-divider">
          <span>OU PAR E-MAIL</span>
        </div>

        {/* Email & Password Form */}
        <form onSubmit={handleEmailSubmit} className="auth-form">
          {mode === 'register' && (
            <div className="auth-field">
              <label>Nom complet</label>
              <input
                type="text"
                placeholder="Ex: Yassine El Amrani"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
              />
            </div>
          )}

          <div className="auth-field">
            <label>Adresse e-mail</label>
            <input
              type="email"
              placeholder="vous@exemple.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </div>

          <div className="auth-field">
            <label>Mot de passe</label>
            <input
              type="password"
              placeholder="••••••••"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>

          {error && <div className="auth-error-msg">{error}</div>}

          <button type="submit" className="auth-submit-btn" disabled={loading}>
            {loading ? 'Connexion en cours...' : mode === 'login' ? 'Se connecter à Vasco' : 'Créer mon compte'}
          </button>
        </form>
      </div>

      {/* Official GitHub OAuth 2.0 Device Flow Modal (https://github.com/login/device) */}
      {ghDevice && (
        <div className="modal-backdrop" onClick={cancelGitHubDevice}>
          <div className="modal-card" style={{ maxWidth: '440px' }} onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div className="oauth-modal-brand">
                <svg viewBox="0 0 24 24" width="28" height="28" fill="white">
                  <path fillRule="evenodd" clipRule="evenodd" d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.53 1.032 1.53 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z" />
                </svg>
                <div>
                  <h2>Autorisation GitHub Officielle</h2>
                  <p>Connectez votre vrai compte sur github.com</p>
                </div>
              </div>
              <button type="button" className="icon-btn" onClick={cancelGitHubDevice}>✕</button>
            </div>

            <div className="modal-body" style={{ textAlign: 'center', padding: '24px 20px' }}>
              <p style={{ fontSize: '0.88rem', color: '#d1d5db', marginBottom: '12px' }}>
                Collez ce code d'activation dans la fenêtre <strong>github.com/login/device</strong> qui vient de s'ouvrir :
              </p>

              <div
                style={{
                  fontSize: '1.9rem',
                  fontWeight: 800,
                  letterSpacing: '0.18em',
                  padding: '16px 20px',
                  borderRadius: '14px',
                  background: '#191d2b',
                  border: '1.5px solid var(--accent-primary)',
                  color: '#ffffff',
                  fontFamily: "'JetBrains Mono', monospace",
                  marginBottom: '10px',
                  userSelect: 'all',
                }}
              >
                {ghDevice.user_code}
              </div>

              {codeCopied && (
                <div style={{ fontSize: '0.78rem', color: '#10b981', marginBottom: '14px' }}>
                  ✓ Code copié automatiquement dans votre presse-papiers (`Ctrl+V` sur GitHub)
                </div>
              )}

              <div style={{ display: 'flex', gap: '10px', justifyContent: 'center', marginTop: '8px' }}>
                <button
                  type="button"
                  className="secondary-btn"
                  onClick={() => {
                    navigator.clipboard.writeText(ghDevice.user_code);
                    setCodeCopied(true);
                  }}
                >
                  📋 Copier le code
                </button>
                <a
                  href={ghDevice.verification_uri || 'https://github.com/login/device'}
                  target="_blank"
                  rel="noreferrer"
                  className="primary-btn"
                  style={{ textDecoration: 'none', display: 'inline-flex', alignItems: 'center' }}
                >
                  Ouvrir github.com ↗
                </a>
              </div>

              <div style={{ marginTop: '18px', fontSize: '0.8rem', color: '#9ca3af' }}>
                ⏳ En attente de votre validation sur GitHub... La connexion se fera automatiquement.
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
