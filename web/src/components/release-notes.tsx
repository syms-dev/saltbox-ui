import { useReleaseNotes } from '../api/queries';
import { formatRelative } from '../format';
import styles from './release-notes.module.css';

/** Links from GitHub's answer are shown only when they go to GitHub itself. */
function githubLink(url: string): string | null {
  return url.startsWith('https://github.com/') ? url : null;
}

/**
 * What an app's waiting update brings, from the app's own release notes on GitHub.
 * The notes are the maintainers' text, shown as plain text.
 */
export function ReleaseNotesCard({ repo, role }: { repo: string; role: string }): React.ReactNode {
  const notes = useReleaseNotes(repo, role, true);
  if (notes.isPending) {
    return (
      <section aria-labelledby="release-notes-title" className="card">
        <h2 id="release-notes-title">What&apos;s new</h2>
        <p className="muted">Reading the release notes on GitHub…</p>
      </section>
    );
  }
  if (notes.isError) {
    return (
      <section aria-labelledby="release-notes-title" className="card">
        <h2 id="release-notes-title">What&apos;s new</h2>
        <p className="muted">The release notes could not be read: {notes.error.message}</p>
      </section>
    );
  }
  const data = notes.data;
  if (data.project === '') return null; // not an app whose releases are on GitHub
  const all = githubLink(data.releases_url);
  return (
    <section aria-labelledby="release-notes-title" className="card">
      <div className="row">
        <h2 className={styles['title']} id="release-notes-title">
          What&apos;s new
        </h2>
        <span className="spacer" />
        {all === null ? null : (
          <a className="small" href={all} rel="noreferrer" target="_blank">
            All releases on GitHub ↗
          </a>
        )}
      </div>
      {data.error === '' ? null : <p className="muted">The release notes could not be read: {data.error}.</p>}
      {data.error === '' && data.releases.length === 0 ? (
        <p className="muted">GitHub has no release notes for this version.</p>
      ) : null}
      {data.releases.map((release, index) => {
        const link = githubLink(release.url);
        return (
          <details className={styles['release']} key={release.version} open={index === 0}>
            <summary>
              <strong>{release.version}</strong>
              {release.prerelease ? <span className="muted"> (beta)</span> : null}
              {release.published_at === '' ? null : (
                <span className="muted small"> · {formatRelative(release.published_at)}</span>
              )}
            </summary>
            <div className={styles['body']}>{release.body === '' ? 'No notes for this release.' : release.body}</div>
            {link === null ? null : (
              <a className="small" href={link} rel="noreferrer" target="_blank">
                {release.version} on GitHub ↗
              </a>
            )}
          </details>
        );
      })}
      {data.more ? <p className="muted small">Older releases in between are listed on GitHub.</p> : null}
    </section>
  );
}
