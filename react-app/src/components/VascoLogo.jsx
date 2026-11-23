import React, { useId } from 'react';

/**
 * Vasco 3D mark: a faceted crystal "V" extruded over a glass tile, lit from the top-left,
 * with the Vasco spark in its notch. Pure SVG, crisp at every size.
 */
export default function VascoLogo({ size = 40, tile = true, className = '', title = 'Vasco' }) {
  const id = useId().replace(/[^a-zA-Z0-9]/g, '');
  const g = (name) => `${name}-${id}`;
  const url = (name) => `url(#${g(name)})`;

  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 64 64"
      fill="none"
      className={`vasco-logo ${className}`}
      role="img"
      aria-label={title}
    >
      <defs>
        <linearGradient id={g('tile')} x1="32" y1="2" x2="32" y2="62" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#2a2456" />
          <stop offset="0.55" stopColor="#141230" />
          <stop offset="1" stopColor="#08080f" />
        </linearGradient>
        <linearGradient id={g('rim')} x1="32" y1="2" x2="32" y2="62" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#ffffff" stopOpacity="0.55" />
          <stop offset="0.35" stopColor="#a78bfa" stopOpacity="0.25" />
          <stop offset="1" stopColor="#22d3ee" stopOpacity="0.18" />
        </linearGradient>
        <radialGradient id={g('glow')} cx="32" cy="36" r="26" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#8b5cf6" stopOpacity="0.55" />
          <stop offset="0.6" stopColor="#6366f1" stopOpacity="0.12" />
          <stop offset="1" stopColor="#6366f1" stopOpacity="0" />
        </radialGradient>
        <linearGradient id={g('gloss')} x1="32" y1="2" x2="32" y2="30" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#ffffff" stopOpacity="0.16" />
          <stop offset="1" stopColor="#ffffff" stopOpacity="0" />
        </linearGradient>

        {/* Crystal facets */}
        <linearGradient id={g('lOut')} x1="11" y1="14" x2="30" y2="50" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#f5f3ff" />
          <stop offset="0.45" stopColor="#c4b5fd" />
          <stop offset="1" stopColor="#8b5cf6" />
        </linearGradient>
        <linearGradient id={g('lIn')} x1="19" y1="14" x2="32" y2="50" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#a78bfa" />
          <stop offset="0.6" stopColor="#7c3aed" />
          <stop offset="1" stopColor="#4c1d95" />
        </linearGradient>
        <linearGradient id={g('rIn')} x1="45" y1="14" x2="32" y2="50" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#a5f3fc" />
          <stop offset="0.5" stopColor="#6366f1" />
          <stop offset="1" stopColor="#3730a3" />
        </linearGradient>
        <linearGradient id={g('rOut')} x1="53" y1="14" x2="34" y2="50" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#67e8f9" />
          <stop offset="0.5" stopColor="#0891b2" />
          <stop offset="1" stopColor="#1e1b4b" />
        </linearGradient>
        <linearGradient id={g('sideL')} x1="11" y1="16" x2="32" y2="56" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#4c1d95" />
          <stop offset="1" stopColor="#1e1145" />
        </linearGradient>
        <linearGradient id={g('sideR')} x1="53" y1="16" x2="32" y2="56" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#155e75" />
          <stop offset="1" stopColor="#111033" />
        </linearGradient>
        <radialGradient id={g('spark')} cx="32" cy="23" r="7" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#ffffff" />
          <stop offset="0.55" stopColor="#e0e7ff" />
          <stop offset="1" stopColor="#a5b4fc" />
        </radialGradient>
        <filter id={g('soft')} x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="2.2" />
        </filter>
        <filter id={g('drop')} x="-30%" y="-30%" width="160%" height="170%">
          <feDropShadow dx="0" dy="2.5" stdDeviation="2" floodColor="#050312" floodOpacity="0.75" />
        </filter>
      </defs>

      {tile && (
        <>
          <rect x="2" y="2" width="60" height="60" rx="17" fill={url('tile')} />
          <rect x="2" y="2" width="60" height="60" rx="17" fill={url('glow')} />
          <path d="M19 2.5h26c8.6 0 16.5 6.5 16.5 15V24C46 19 18 19 2.5 24v-6.5C2.5 9 10.4 2.5 19 2.5z" fill={url('gloss')} />
          <rect x="2.5" y="2.5" width="59" height="59" rx="16.5" stroke={url('rim')} strokeWidth="1" />
        </>
      )}

      {/* Floor reflection */}
      <ellipse cx="32" cy="55.5" rx="12" ry="2.2" fill="#8b5cf6" opacity="0.45" filter={url('soft')} />

      <g filter={url('drop')}>
        {/* Extruded depth (outer sides) */}
        <path d="M11 14 L32 52 L32 56.5 L11 18.5 Z" fill={url('sideL')} />
        <path d="M53 14 L32 52 L32 56.5 L53 18.5 Z" fill={url('sideR')} />

        {/* Front facets */}
        <path d="M11 14 H16.6 L32 52 Z" fill={url('lOut')} />
        <path d="M16.6 14 H22.2 L32 36.6 V52 Z" fill={url('lIn')} />
        <path d="M41.8 14 H47.4 L32 52 V36.6 Z" fill={url('rIn')} />
        <path d="M47.4 14 H53 L32 52 Z" fill={url('rOut')} />

        {/* Specular edges */}
        <path d="M11 14 H22.2" stroke="#ffffff" strokeOpacity="0.9" strokeWidth="0.8" strokeLinecap="round" />
        <path d="M41.8 14 H53" stroke="#e0f7ff" strokeOpacity="0.75" strokeWidth="0.8" strokeLinecap="round" />
        <path d="M16.6 14 L32 52" stroke="#ffffff" strokeOpacity="0.45" strokeWidth="0.6" />
        <path d="M47.4 14 L32 52" stroke="#ffffff" strokeOpacity="0.28" strokeWidth="0.6" />
        <path d="M22.2 14 L32 36.6 L41.8 14" stroke="#ffffff" strokeOpacity="0.35" strokeWidth="0.6" strokeLinejoin="round" />
      </g>

      {/* Vasco spark */}
      <circle cx="32" cy="23" r="6" fill="#a78bfa" opacity="0.55" filter={url('soft')} />
      <path d="M32 16.8 L33.5 21.5 L38.2 23 L33.5 24.5 L32 29.2 L30.5 24.5 L25.8 23 L30.5 21.5 Z" fill={url('spark')} />
    </svg>
  );
}
