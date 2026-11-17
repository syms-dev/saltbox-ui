import { useState } from 'react';

import { APP_ICONS } from '../lib/app-icons';
import styles from './app-avatar.module.css';

const HUES = 360;

function hue(text: string): number {
  let hash = 0;
  for (const char of text) hash = (hash * 31 + char.charCodeAt(0)) % HUES;
  return hash;
}

export function initials(title: string): string {
  const words = title
    .replace(/[^\p{L}\p{N} ]/gu, ' ')
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  if (words.length === 0) return '?';
  const [first = '', second = ''] = words;
  return (words.length > 1 ? first.charAt(0) + second.charAt(0) : first.slice(0, 2)).toUpperCase();
}

/** The icon bundled for an app's role (see scripts/build_icons.py); null when there is none. */
export function iconFor(role: string | undefined): string | null {
  return role !== undefined && APP_ICONS.has(role) ? `${import.meta.env.BASE_URL}icons/${role}.webp` : null;
}

type AppAvatarProps = { size?: 'lg' | 'md' | 'sm'; title: string; role?: string | undefined };

/**
 * The app's icon, bundled with this web app and served from its own origin, over a
 * coloured monogram that shows when there is no icon for the app. The icon is never
 * fetched from the app itself: the browser would send the sign-on cookie with that
 * request, handing the admin's session to every app whenever this page was opened.
 */
export function AppAvatar({ size = 'md', title, role }: AppAvatarProps): React.ReactNode {
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  const icon = failed ? null : iconFor(role);
  return (
    <span
      aria-hidden="true"
      className={`${styles['avatar']} ${styles[size]}`}
      style={{ '--hue': hue(title) } as React.CSSProperties}
    >
      {loaded ? null : initials(title)}
      {icon === null ? null : (
        <img
          alt=""
          className={loaded ? styles['icon'] : styles['hidden']}
          decoding="async"
          loading="lazy"
          onError={() => setFailed(true)}
          onLoad={(event) => {
            if (event.currentTarget.naturalWidth > 0) setLoaded(true);
            else setFailed(true);
          }}
          referrerPolicy="no-referrer"
          src={icon}
        />
      )}
    </span>
  );
}
