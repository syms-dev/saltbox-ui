import { ArrowUpCircle, CircleCheck, LoaderCircle, RefreshCw } from 'lucide-react';
import { Link } from 'react-router-dom';

import { useApps, useCheckUpdates, useJobs, useUpdates } from '../api/queries';
import { AppAvatar } from '../components/app-avatar';
import { UpdateVersions } from '../components/app-links';
import { ErrorMessage, Loading } from '../components/query-state';
import { SaltboxUpdates } from '../components/saltbox-updates';
import { SelfUpdateCard } from '../components/self-update';
import { formatRelative } from '../format';
import { useJobActions } from '../lib/actions';
import { PageHead } from './server-pages';
import styles from './updates-page.module.css';

/** Every app with a newer image, what it brings, and a button for each and for all. */
function AppUpdates(): React.ReactNode {
  const apps = useApps();
  const updates = useUpdates();
  const check = useCheckUpdates();
  const actions = useJobActions();
  const busy = useJobs().data?.busy ?? false;
  if (apps.isPending) return <Loading what="apps" />;
  if (apps.isError) return <ErrorMessage error={apps.error} prefix="Apps" />;
  const pending = apps.data.filter((app) => app.installed && app.update_available);
  const checking = check.isPending || updates.data?.running === true;
  return (
    <section aria-labelledby="app-updates-title" className="card">
      <div className={styles['head']}>
        <h2 className={styles['title']} id="app-updates-title">
          Apps
        </h2>
        <span className="muted small">
          {checking
            ? 'Checking…'
            : updates.data?.checked_at == null
              ? 'Not checked yet'
              : `Checked ${formatRelative(updates.data.checked_at)}`}
        </span>
        <span className="spacer" />
        <button className="small" disabled={checking} onClick={() => check.mutate()} type="button">
          {checking ? (
            <LoaderCircle aria-hidden="true" className="spin" size={15} />
          ) : (
            <RefreshCw aria-hidden="true" size={15} />
          )}
          Check now
        </button>
        {pending.length > 1 ? (
          <button
            className="small primary"
            disabled={busy}
            onClick={() => void actions.updateAll(pending)}
            type="button"
          >
            Update all ({pending.length})
          </button>
        ) : null}
      </div>
      {check.isError ? <ErrorMessage error={check.error} /> : null}
      {pending.length === 0 ? (
        <p className={styles['clear']}>
          <CircleCheck aria-hidden="true" size={18} /> Every app is on its newest image.
        </p>
      ) : (
        <ul aria-label="Apps with an update" className={styles['list']}>
          {pending.map((app) => (
            <li className={styles['item']} key={`${app.repo}/${app.role}`}>
              <AppAvatar role={app.role} size="sm" title={app.title} />
              <span className={styles['name']}>
                <Link to={`/apps/${app.repo}/${app.role}`}>{app.title}</Link>
                <UpdateVersions app={app} />
              </span>
              <button className="small" disabled={busy} onClick={() => void actions.updateApp(app)} type="button">
                <ArrowUpCircle aria-hidden="true" size={15} /> Update
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Everything that can be updated, in one place: the apps, Saltbox itself and this web app. */
export function UpdatesPage(): React.ReactNode {
  return (
    <div className="stack">
      <PageHead title="Updates">
        New app images, new Saltbox commits and new versions of Saltbox UI. Open an app to read what its update brings.
      </PageHead>
      <AppUpdates />
      <SaltboxUpdates />
      <SelfUpdateCard />
    </div>
  );
}
