import { HardDrive } from 'lucide-react';

import { useOverview } from '../api/queries';
import { Meter } from '../components/meter';
import { ErrorMessage, Loading } from '../components/query-state';
import { StatusDot } from '../components/status-dot';
import { formatBytes } from '../format';
import { DrivesCard } from './drives-card';
import { ProblemsCard } from './problems-card';
import { BackupsCard, ResourcesCard, TailscaleCard } from './system-sections';
import styles from './system-page.module.css';

const MOUNT_KINDS: Record<string, string> = {
  cifs: 'SMB share',
  'fuse.mergerfs': 'mergerfs pool',
  'fuse.rclone': 'rclone remote',
  nfs: 'NFS share',
  nfs4: 'NFS share',
};

/** A page's title and one line on what it is for. */
export function PageHead({ children, title }: { children?: React.ReactNode; title: string }): React.ReactNode {
  return (
    <header className={styles['head']}>
      <h1 className={styles['flush']}>{title}</h1>
      {children === undefined ? null : <p className={`muted ${styles['flush']}`}>{children}</p>}
    </header>
  );
}

/** How the server is doing: load, errors in the apps' logs, drives and Saltbox's own services. */
export function HealthPage(): React.ReactNode {
  const overview = useOverview();
  if (overview.isPending) return <Loading what="server health" />;
  if (overview.isError) return <ErrorMessage error={overview.error} prefix="Health" />;
  const { reboot_required: reboot, units } = overview.data.host;
  return (
    <div className="stack">
      <PageHead title="Health">Load, errors in the apps&apos; logs, drive health and Saltbox&apos;s services.</PageHead>
      {reboot ? <p className="note warn">Ubuntu installed updates that need a restart of the server.</p> : null}
      <ResourcesCard />
      <ProblemsCard />
      <DrivesCard />
      <section aria-labelledby="services-title" className="card">
        <h2 id="services-title">Saltbox services</h2>
        {units.length === 0 ? (
          <p className="muted small">No Saltbox-managed systemd services.</p>
        ) : (
          <ul className={styles['units']}>
            {units.map((unit) => (
              <li key={unit.unit}>
                <StatusDot
                  health={{
                    label: `${unit.active}${unit.sub === '' || unit.sub === unit.active ? '' : ` (${unit.sub})`}`,
                    tone: unit.active === 'active' ? 'ok' : unit.active === 'failed' ? 'danger' : 'muted',
                  }}
                />
                <span className={styles['unitName']}>
                  <code>{unit.unit}</code>
                  <span className="muted small">{unit.description}</span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

/** Space on each disk, and the remotes, pools and shares mounted. */
export function StoragePage(): React.ReactNode {
  const overview = useOverview();
  if (overview.isPending) return <Loading what="storage" />;
  if (overview.isError) return <ErrorMessage error={overview.error} prefix="Storage" />;
  const { disks, mounts } = overview.data.host;
  return (
    <div className="stack">
      <PageHead title="Storage">Free space on each disk, and what is mounted where.</PageHead>
      <section aria-labelledby="storage-title" className="card">
        <h2 id="storage-title">Disks</h2>
        {disks.length === 0 ? (
          <p className="muted">No disk information.</p>
        ) : (
          <ul className={styles['disks']}>
            {disks.map((disk) => (
              <li className={styles['disk']} key={disk.path}>
                <HardDrive aria-hidden="true" className="muted" size={20} />
                <div className={styles['diskBody']}>
                  <div className={styles['diskHead']}>
                    <code>{(disk.paths ?? [disk.path]).join(' · ')}</code>
                    <span className="small">
                      <strong>{formatBytes(disk.free)}</strong>{' '}
                      <span className="muted">free of {formatBytes(disk.total)}</span>
                    </span>
                  </div>
                  <Meter label={`${disk.path} ${disk.percent}% used`} percent={disk.percent} />
                  <span className="muted small">{disk.percent}% used</span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section aria-labelledby="mounts-title" className="card">
        <h2 id="mounts-title">Mounts</h2>
        {mounts.length === 0 ? (
          <p className="muted small">No rclone remotes, mergerfs pools or network shares are mounted.</p>
        ) : (
          <table>
            <tbody>
              {mounts.map((mount) => (
                <tr key={mount.target}>
                  <td>
                    <code>{mount.target}</code>
                    <div className="muted small">{mount.source}</div>
                  </td>
                  <td className="muted">{MOUNT_KINDS[mount.type] ?? mount.type}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

/** Reaching this server from elsewhere: Tailscale, for the web app and a terminal. */
export function NetworkPage(): React.ReactNode {
  return (
    <div className="stack">
      <PageHead title="Network & access">
        Reach the web app and the server from your own devices over Tailscale.
      </PageHead>
      <TailscaleCard />
    </div>
  );
}

export function BackupsPage(): React.ReactNode {
  return (
    <div className="stack">
      <PageHead title="Backups">Saltbox&apos;s own backup of your app settings, and when it last ran.</PageHead>
      <BackupsCard />
    </div>
  );
}
