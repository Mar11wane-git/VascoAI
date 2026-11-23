// Helpers for the profile space.

const rtf = typeof Intl !== 'undefined' && Intl.RelativeTimeFormat ? new Intl.RelativeTimeFormat('fr', { numeric: 'auto' }) : null;

export function timeAgo(ts) {
  if (!ts) return '—';
  const diff = ts - Date.now() / 1000;
  const units = [
    ['year', 31536000], ['month', 2592000], ['week', 604800],
    ['day', 86400], ['hour', 3600], ['minute', 60],
  ];
  for (const [unit, sec] of units) {
    if (Math.abs(diff) >= sec) return rtf ? rtf.format(Math.round(diff / sec), unit) : new Date(ts * 1000).toLocaleString('fr-FR');
  }
  return 'à l’instant';
}

export function formatDate(ts, opts = { day: 'numeric', month: 'long', year: 'numeric' }) {
  if (!ts) return '—';
  return new Date(ts * 1000).toLocaleDateString('fr-FR', opts);
}

export function ageFrom(isoDate) {
  if (!isoDate) return null;
  const b = new Date(isoDate);
  const now = new Date();
  let age = now.getFullYear() - b.getFullYear();
  if (now.getMonth() < b.getMonth() || (now.getMonth() === b.getMonth() && now.getDate() < b.getDate())) age--;
  return age;
}

export function parseUserAgent(ua = '') {
  const browser =
    /Edg\//.test(ua) ? 'Edge' :
    /OPR\//.test(ua) ? 'Opera' :
    /Firefox\//.test(ua) ? 'Firefox' :
    /Chrome\//.test(ua) ? 'Chrome' :
    /Safari\//.test(ua) ? 'Safari' : '';
  const os =
    /Windows/.test(ua) ? 'Windows' :
    /Android/.test(ua) ? 'Android' :
    /iPhone|iPad|iPod/.test(ua) ? 'iOS' :
    /Mac OS X/.test(ua) ? 'macOS' :
    /Linux/.test(ua) ? 'Linux' : '';
  const mobile = /Mobile|Android|iPhone|iPad/.test(ua);
  return { browser, os, mobile, label: [browser, os].filter(Boolean).join(' · ') || 'Appareil inconnu' };
}

/** Center-crops an image file to a square and returns a compact JPEG data URL. */
export async function resizeAvatar(file, size = 320) {
  if (!file.type.startsWith('image/')) throw new Error('Choisissez une image (PNG, JPEG, WebP).');
  if (file.size > 15 * 1024 * 1024) throw new Error('Image trop lourde (15 Mo maximum).');
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise((resolve, reject) => {
      const i = new Image();
      i.onload = () => resolve(i);
      i.onerror = () => reject(new Error('Image illisible.'));
      i.src = url;
    });
    const side = Math.min(img.naturalWidth, img.naturalHeight);
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = size;
    const ctx = canvas.getContext('2d');
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(img, (img.naturalWidth - side) / 2, (img.naturalHeight - side) / 2, side, side, 0, 0, size, size);
    return canvas.toDataURL('image/jpeg', 0.88);
  } finally {
    URL.revokeObjectURL(url);
  }
}

export function passwordStrength(pwd = '') {
  let score = 0;
  if (pwd.length >= 8) score++;
  if (pwd.length >= 12) score++;
  if (/[a-z]/.test(pwd) && /[A-Z]/.test(pwd)) score++;
  if (/\d/.test(pwd)) score++;
  if (/[^A-Za-z0-9]/.test(pwd)) score++;
  const level = Math.min(4, score);
  return {
    level,
    label: ['Très faible', 'Faible', 'Moyen', 'Fort', 'Excellent'][level],
  };
}

export const COVER_THEMES = {
  nebula: 'linear-gradient(120deg, #2e1065 0%, #6d28d9 38%, #4f46e5 68%, #0891b2 100%)',
  ocean: 'linear-gradient(120deg, #1e3a8a 0%, #0e7490 52%, #0f766e 100%)',
  aurora: 'linear-gradient(120deg, #4c1d95 0%, #2563eb 50%, #06b6d4 100%)',
  sunset: 'linear-gradient(120deg, #9a3412 0%, #db2777 55%, #7c3aed 100%)',
  forest: 'linear-gradient(120deg, #14532d 0%, #0f766e 55%, #65a30d 100%)',
  midnight: 'linear-gradient(120deg, #0b1220 0%, #1e293b 55%, #334155 100%)',
  rose: 'linear-gradient(120deg, #831843 0%, #be185d 55%, #f472b6 100%)',
};

export const COVER_LABELS = {
  nebula: 'Nébuleuse',
  ocean: 'Océan', aurora: 'Aurore', sunset: 'Crépuscule', forest: 'Forêt', midnight: 'Minuit', rose: 'Rose',
};

export function listTimezones() {
  try {
    if (Intl.supportedValuesOf) return Intl.supportedValuesOf('timeZone');
  } catch (_) {}
  return ['Africa/Casablanca', 'Europe/Paris', 'Europe/London', 'America/New_York', 'Asia/Dubai', 'UTC'];
}
