import type { App, UpdateState } from '../api/types';
import { hostOf } from '../lib/status';
import { StatusBadge } from './status-badge';

type OpenLinksProps = { urls: string[] };

/** "Open" links to an app's own web pages, in a new tab. */
export function OpenLinks({ urls }: OpenLinksProps): React.ReactNode {
  if (urls.length === 0) return null;
  return (
    <span className="row">
      {urls.map((url, index) => (
        <a aria-label={`Open ${hostOf(url)}`} href={url} key={url} rel="noreferrer" target="_blank" title={url}>
          {index === 0 ? 'Open ↗' : `${hostOf(url)} ↗`}
        </a>
      ))}
    </span>
  );
}

const UPDATE_LABELS: Record<UpdateState, string> = {
  current: 'up to date',
  unknown: 'not checked',
  update: 'update available',
};

const UPDATE_TONES: Record<UpdateState, string> = {
  current: 'succeeded',
  unknown: 'pending',
  update: 'interrupted',
};

type UpdateBadgeProps = { detail?: string; state: UpdateState };

export function UpdateBadge({ detail, state }: UpdateBadgeProps): React.ReactNode {
  return (
    <span title={detail ?? ''}>
      <StatusBadge label={UPDATE_LABELS[state]} status={UPDATE_TONES[state]} />
    </span>
  );
}

/** The most urgent update state across an app's containers. */
export function overallUpdate(states: UpdateState[]): UpdateState {
  if (states.includes('update')) return 'update';
  if (states.length > 0 && states.every((state) => state === 'current')) return 'current';
  return 'unknown';
}

/** The version an update moves an app from and to, when its images say; null when not known. */
export function updateVersions(app: Pick<App, 'containers'>): { from: string; to: string } | null {
  const container = app.containers.find((c) => c.update === 'update' && (c.update_version ?? '') !== '');
  if (container === undefined) return null;
  return { from: container.version ?? '', to: container.update_version ?? '' };
}

/** "4.0.10 → 4.0.11 · Releases": what an update brings, and where its release page is. */
export function UpdateVersions({
  app,
  compact = false,
}: {
  app: Pick<App, 'app_links' | 'containers'>;
  compact?: boolean;
}): React.ReactNode {
  const versions = updateVersions(app);
  if (compact) {
    // In a narrow list row: the new version only, the whole change on hover.
    return versions === null ? null : (
      <span className="muted small" title={`${versions.from || 'now'} → ${versions.to}`}>
        <code>{versions.to}</code>
      </span>
    );
  }
  const releases = app.app_links.find((link) => link.purpose === 'release' && link.url.startsWith('https://'));
  if (versions === null && releases === undefined) return null;
  return (
    <span className="muted small">
      {versions === null ? null : (
        <>
          {versions.from === '' ? 'to' : <code>{versions.from}</code>} → <code>{versions.to}</code>
        </>
      )}
      {versions !== null && releases !== undefined ? ' · ' : null}
      {releases === undefined ? null : (
        <a href={releases.url} rel="noreferrer" target="_blank">
          Releases
        </a>
      )}
    </span>
  );
}
