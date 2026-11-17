import {
  Activity,
  ArrowUpCircle,
  BellRing,
  Cable,
  Compass,
  FileCog,
  Globe,
  HardDrive,
  HeartPulse,
  History,
  House,
  LayoutGrid,
  Library as LibraryIcon,
  LoaderCircle,
  Menu,
  Search,
  TriangleAlert,
  X,
} from 'lucide-react';
import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, Outlet, useLocation } from 'react-router-dom';

import { ApiError, apiFetch, API_ROUTES } from '../api/client';
import { QUERY_KEYS, useJobs, useMeta, useOverview, useSelf, useUpdates } from '../api/queries';
import type { Job, Meta } from '../api/types';
import { formatDuration } from '../format';
import { CommandPalette } from './command-palette';
import { ConnectionBanner, StartupBanner, useConnection } from './connection';
import { useToast } from './feedback';
import { newerLine } from './self-update';
import styles from './shell.module.css';

const ICON = 18;

type CountKey = 'health' | 'updates';
type NavItem = { count?: CountKey; icon: React.ReactNode; label: string; to: string };
type NavGroup = { items: NavItem[]; title: string | null };

/** Every page, grouped the way people look for them. The phone's More menu shows the same. */
export const NAV: NavGroup[] = [
  {
    items: [
      { icon: <House aria-hidden="true" size={ICON} />, label: 'Home', to: '/' },
      { count: 'updates', icon: <ArrowUpCircle aria-hidden="true" size={ICON} />, label: 'Updates', to: '/updates' },
      { icon: <Activity aria-hidden="true" size={ICON} />, label: 'Activity', to: '/activity' },
    ],
    title: null,
  },
  {
    items: [
      { icon: <LayoutGrid aria-hidden="true" size={ICON} />, label: 'Installed', to: '/apps' },
      { icon: <Compass aria-hidden="true" size={ICON} />, label: 'Discover', to: '/apps/discover' },
    ],
    title: 'Apps',
  },
  {
    items: [
      { icon: <LibraryIcon aria-hidden="true" size={ICON} />, label: 'Library', to: '/library' },
      { icon: <Cable aria-hidden="true" size={ICON} />, label: 'Connect apps', to: '/connect' },
    ],
    title: 'Media',
  },
  {
    items: [
      { count: 'health', icon: <HeartPulse aria-hidden="true" size={ICON} />, label: 'Health', to: '/health' },
      { icon: <HardDrive aria-hidden="true" size={ICON} />, label: 'Storage', to: '/storage' },
      { icon: <Globe aria-hidden="true" size={ICON} />, label: 'Network & access', to: '/network' },
      { icon: <History aria-hidden="true" size={ICON} />, label: 'Backups', to: '/backups' },
      { icon: <BellRing aria-hidden="true" size={ICON} />, label: 'Automation', to: '/automation' },
    ],
    title: 'Server',
  },
  {
    items: [{ icon: <FileCog aria-hidden="true" size={ICON} />, label: 'Config files', to: '/config' }],
    title: 'Settings',
  },
];

/** The phone's bottom bar: the four places people go most, and everything else under More. */
const TABS: NavItem[] = [
  { icon: <House aria-hidden="true" size={22} />, label: 'Home', to: '/' },
  { icon: <LayoutGrid aria-hidden="true" size={22} />, label: 'Apps', to: '/apps' },
  { count: 'updates', icon: <ArrowUpCircle aria-hidden="true" size={22} />, label: 'Updates', to: '/updates' },
  { icon: <Activity aria-hidden="true" size={22} />, label: 'Activity', to: '/activity' },
];

/** Whether a menu entry is the page being shown (Installed covers each app's own page). */
export function isCurrent(to: string, path: string): boolean {
  if (to === '/') return path === '/';
  if (to === '/apps') return path === '/apps' || (path.startsWith('/apps/') && !path.startsWith('/apps/discover'));
  return path === to || path.startsWith(`${to}/`);
}

type Counts = Record<CountKey, number>;

/** What each counted menu entry has waiting: updates to run, apps that are down. */
function useCounts(): Counts {
  const overview = useOverview();
  const updates = useUpdates();
  const self = useSelf();
  const apps = overview.data?.updates.pending.length ?? 0;
  const saltbox = (updates.data?.repos ?? []).some((repo) => repo.status === 'update') ? 1 : 0;
  const ui = self.data?.update_available === true ? 1 : 0;
  const down = (overview.data?.containers.unhealthy.length ?? 0) + (overview.data?.containers.stopped.length ?? 0);
  return { health: down, updates: apps + saltbox + ui };
}

const COUNT_TITLES: Record<CountKey, (n: number) => string> = {
  health: (n) => `${n} app${n === 1 ? '' : 's'} down or unhealthy`,
  updates: (n) => `${n} update${n === 1 ? '' : 's'} available`,
};

function CountBadge({ count, kind }: { count: number; kind: CountKey }): React.ReactNode {
  if (count === 0) return null;
  return (
    <span className={`${styles['badge']} ${kind === 'health' ? styles['badgeDanger'] : ''}`}>
      <span aria-hidden="true">{count}</span>
      <span className="visually-hidden">, {COUNT_TITLES[kind](count)}</span>
    </span>
  );
}

function NavGroups({ counts, onPick }: { counts: Counts; onPick?: () => void }): React.ReactNode {
  const { pathname } = useLocation();
  return NAV.map((group) => (
    <div className={styles['group']} key={group.title ?? 'main'}>
      {group.title === null ? null : <h2 className={styles['groupTitle']}>{group.title}</h2>}
      {group.items.map((item) => {
        const current = isCurrent(item.to, pathname);
        return (
          <Link
            aria-current={current ? 'page' : undefined}
            className={`${styles['navLink']} ${current ? styles['active'] : ''}`}
            key={item.to}
            onClick={onPick}
            to={item.to}
          >
            {item.icon}
            <span>{item.label}</span>
            {item.count === undefined ? null : <CountBadge count={counts[item.count]} kind={item.count} />}
          </Link>
        );
      })}
    </div>
  ));
}

/** On phones: every page, in a sheet over the bottom bar. */
function MoreSheet({ counts, onClose }: { counts: Counts; onClose: () => void }): React.ReactNode {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current;
    if (element !== null && !element.open) element.showModal();
  }, []);
  return (
    <dialog
      aria-labelledby="more-title"
      className={styles['sheet']}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target === dialog.current) onClose(); // a tap on the backdrop
      }}
      ref={dialog}
    >
      <div className={styles['sheetHead']}>
        <h2 className={styles['sheetTitle']} id="more-title">
          All pages
        </h2>
        <button aria-label="Close" className="ghost icon" onClick={onClose} type="button">
          <X aria-hidden="true" size={20} />
        </button>
      </div>
      <nav aria-label="All pages" className={styles['sheetNav']}>
        <NavGroups counts={counts} onPick={onClose} />
      </nav>
    </dialog>
  );
}

/**
 * The foot of the sidebar: this app's name and version, and nothing else. Which commit and
 * branch Saltbox is on belongs with `sb update` on the Updates page, where it is shown.
 */
function Version({ meta }: { meta: Meta }): React.ReactNode {
  const self = useSelf();
  const status = self.data;
  return (
    <span className={styles['version']}>
      <span>Saltbox UI</span>
      <span className={styles['versionNumber']}>{meta.version}</span>
      {/* A newer version: a small mark that leads to the update on the Updates page. */}
      {status?.update_available === true ? (
        <Link
          aria-label={`Update available. ${newerLine(status)}`}
          className={styles['versionUpdate']}
          title={newerLine(status)}
          to="/updates#saltbox-ui"
        >
          <ArrowUpCircle aria-hidden="true" size={16} />
        </Link>
      ) : null}
    </span>
  );
}

/** "sb install sonarr" from a job, without the path to sb. */
export function jobTitle(job: Pick<Job, 'command_line'> & { kind?: string }): string {
  // The app updating itself runs an internal command; say what it is instead.
  if (job.kind === 'selfupdate') return 'Update Saltbox UI';
  return job.command_line.replace(/^\S*\/(?=sb\b)/, '');
}

let loadedBuild: string | undefined;

/** Whether the daemon now serves a different web app than the one this page is running. */
export function isOutdated(build: string | null | undefined): boolean {
  if (build === null || build === undefined) return false;
  loadedBuild ??= build;
  return build !== loadedBuild;
}

const IS_MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.userAgent);

function RunningJob({ job }: { job: Job }): React.ReactNode {
  const [, tick] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(timer);
  }, []);
  const waiting = job.question != null;
  return (
    <Link className={styles['jobBanner']} to={`/activity/${job.id}`}>
      <LoaderCircle aria-hidden="true" className="spin" size={16} />
      <span className={styles['jobText']}>
        {waiting ? (
          <>
            <code>{jobTitle(job)}</code> is waiting for your answer
          </>
        ) : (
          <>
            Running <code>{jobTitle(job)}</code>
          </>
        )}
      </span>
      <span className={styles['jobTime']}>{formatDuration(job.started_at, null)}</span>
      <span className={styles['jobLink']}>{waiting ? 'Answer' : 'View log'}</span>
    </Link>
  );
}

/** Tell the person when a job they may have walked away from finishes. */
function useJobFinishedToast(current: Job | null): void {
  const toast = useToast();
  const client = useQueryClient();
  const previous = useRef<Job | null>(null);
  useEffect(() => {
    const before = previous.current;
    previous.current = current;
    if (before === null || current?.id === before.id) return;
    // A job can install, update or remove apps: read what it changed now, so a finished
    // update stops offering its Update button instead of waiting for the next poll.
    for (const key of [QUERY_KEYS.APPS, QUERY_KEYS.UPDATES, QUERY_KEYS.OVERVIEW, 'media']) {
      void client.invalidateQueries({ queryKey: [key] });
    }
    void apiFetch<Job>(API_ROUTES.job(before.id))
      .then((job) => {
        const ok = job.status === 'succeeded';
        toast({
          href: `/activity/${job.id}`,
          linkLabel: ok ? 'View log' : 'See what failed',
          title: `${jobTitle(job)} ${ok ? 'finished' : job.status}`,
          tone: ok ? 'ok' : 'danger',
        });
      })
      .catch(() => undefined);
  }, [client, current, toast]);
}

export function Shell(): React.ReactNode {
  const meta = useMeta();
  const jobs = useJobs();
  const overview = useOverview();
  const connection = useConnection();
  const counts = useCounts();
  const { pathname } = useLocation();
  const [searching, setSearching] = useState(false);
  const [more, setMore] = useState(false);
  const mainRef = useRef<HTMLElement>(null);
  const firstPath = useRef(pathname);
  const current = jobs.data?.current ?? null;
  useJobFinishedToast(current);
  const closeSearch = useCallback(() => setSearching(false), []);

  useEffect(() => {
    function onKey(event: KeyboardEvent): void {
      const typing = event.target instanceof HTMLElement && /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName);
      if ((event.key === 'k' && (event.metaKey || event.ctrlKey)) || (event.key === '/' && !typing)) {
        event.preventDefault();
        setSearching(true);
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // After moving to another page, start reading at its content, not at the menu (WCAG 2.4.3),
  // and at its top, unless the link named a section further down (#drives), which scrolls itself.
  useEffect(() => {
    if (pathname === firstPath.current) return;
    firstPath.current = '';
    mainRef.current?.focus({ preventScroll: true });
    if (window.location.hash === '') window.scrollTo({ top: 0 });
  }, [pathname]);

  const domain = meta.data?.domain ?? null;
  const onTab = TABS.some((tab) => isCurrent(tab.to, pathname));

  const searchButton = (
    <button className={styles['searchButton']} onClick={() => setSearching(true)} type="button">
      <Search aria-hidden="true" size={16} />
      <span className={styles['searchLabel']}>Search</span>
      <kbd className={styles['searchKey']}>{IS_MAC ? '⌘K' : 'Ctrl K'}</kbd>
    </button>
  );

  return (
    <div className={styles['shell']}>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside aria-label="Sidebar" className={styles['sidebar']}>
        <Link className={styles['brand']} to="/">
          <span aria-hidden="true" className={styles['mark']} />
          <span className={styles['brandText']}>
            <span className={styles['brandName']}>Saltbox</span>
            <span className={styles['brandDomain']}>{domain ?? 'server'}</span>
          </span>
        </Link>
        {searchButton}
        <nav aria-label="Main" className={styles['nav']}>
          <NavGroups counts={counts} />
        </nav>
        <div className={styles['sidebarFoot']}>
          {meta.isError ? (
            <span className={styles['offline']}>
              <TriangleAlert size={14} /> Daemon unreachable
            </span>
          ) : (
            meta.data && <Version meta={meta.data} />
          )}
        </div>
      </aside>

      <header className={styles['topbar']}>
        <Link className={styles['brand']} to="/">
          <span aria-hidden="true" className={styles['mark']} />
          <span className={styles['brandName']}>{domain ?? 'Saltbox'}</span>
        </Link>
        {searchButton}
      </header>

      <main className={styles['main']} id="main" ref={mainRef} tabIndex={-1}>
        {current === null ? null : <RunningJob job={current} />}
        <ConnectionBanner />
        {isOutdated(meta.data?.build) ? (
          <p className="note" role="status">
            Saltbox UI was updated while this page was open.{' '}
            <button className="small" onClick={() => window.location.reload()} type="button">
              Reload
            </button>
          </p>
        ) : null}
        {connection.offlineSince === null && overview.data !== undefined ? (
          <StartupBanner
            running={overview.data.containers.running}
            total={overview.data.containers.total}
            uptime={overview.data.host.uptime}
          />
        ) : null}
        {meta.error instanceof ApiError && meta.error.status === 403 ? (
          <p className="note danger" role="alert">
            Access refused: {meta.error.message}.
          </p>
        ) : null}
        <div className={styles['content']}>
          <Outlet />
        </div>
      </main>

      <nav aria-label="Main" className={styles['tabbar']}>
        {TABS.map((item) => {
          const current = isCurrent(item.to, pathname);
          const count = item.count === undefined ? 0 : counts[item.count];
          return (
            <Link
              aria-current={current ? 'page' : undefined}
              className={`${styles['tab']} ${current ? styles['active'] : ''}`}
              key={item.to}
              to={item.to}
            >
              <span className={styles['tabIcon']}>
                {item.icon}
                {count > 0 ? <span aria-hidden="true" className={styles['tabDot']} /> : null}
              </span>
              <span>{item.label}</span>
              {count > 0 && item.count !== undefined ? (
                <span className="visually-hidden">, {COUNT_TITLES[item.count](count)}</span>
              ) : null}
            </Link>
          );
        })}
        <button
          aria-expanded={more}
          aria-haspopup="dialog"
          className={`${styles['tab']} ${!onTab ? styles['active'] : ''}`}
          onClick={() => setMore(true)}
          type="button"
        >
          <span className={styles['tabIcon']}>
            <Menu aria-hidden="true" size={22} />
            {counts.health > 0 ? (
              <span aria-hidden="true" className={`${styles['tabDot']} ${styles['tabDotDanger']}`} />
            ) : null}
          </span>
          <span>More</span>
        </button>
      </nav>
      {more ? <MoreSheet counts={counts} onClose={() => setMore(false)} /> : null}

      {searching ? <CommandPalette onClose={closeSearch} /> : null}
    </div>
  );
}
