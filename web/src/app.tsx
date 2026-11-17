import { Navigate, Route, Routes, useLocation, useParams } from 'react-router-dom';

import { Shell } from './components/shell';
import { ActivityPage } from './pages/activity-page';
import { AppPage } from './pages/app-page';
import { AutomationPage } from './pages/automation-page';
import { AppsPage } from './pages/apps-page';
import { ConfigPage } from './pages/config-page';
import { ConnectPage } from './pages/connect-page';
import { HomePage } from './pages/home-page';
import { JobPage } from './pages/job-page';
import { LibraryPage } from './pages/library-page';
import { NotFoundPage } from './pages/not-found-page';
import { BackupsPage, HealthPage, NetworkPage, StoragePage } from './pages/server-pages';
import { UpdatesPage } from './pages/updates-page';

export const ROUTES = {
  ACTIVITY: '/activity',
  APPS: '/apps',
  AUTOMATION: '/automation',
  CONFIG: '/config',
  CONNECT: '/connect',
  DISCOVER: '/apps/discover',
  HOME: '/',
  LIBRARY: '/library',
  HEALTH: '/health',
  app: (repo: string, role: string) => `/apps/${repo}/${role}` as const,
  configFile: (name: string) => `/config/${name}` as const,
  job: (id: string) => `/activity/${id}` as const,
} as const;

/** Links from before the redesign (/jobs/<id>) keep working. */
function OldJobLink(): React.ReactNode {
  const { id = '' } = useParams();
  return <Navigate replace to={ROUTES.job(id)} />;
}

/** /system#problems and #drives now live on Health; #saltbox-ui on Updates. */
function OldSystemLink(): React.ReactNode {
  const { hash } = useLocation();
  return <Navigate replace to={hash === '#saltbox-ui' ? `/updates${hash}` : `/health${hash}`} />;
}

export function App(): React.ReactNode {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route element={<HomePage />} index />
        <Route element={<AppsPage tab="installed" />} path="apps" />
        <Route element={<AppsPage tab="discover" />} path="apps/discover" />
        <Route element={<AppPage />} path="apps/:repo/:role" />
        <Route element={<ActivityPage />} path="activity" />
        <Route element={<JobPage />} path="activity/:id" />
        <Route element={<Navigate replace to={ROUTES.ACTIVITY} />} path="jobs" />
        <Route element={<OldJobLink />} path="jobs/:id" />
        <Route element={<UpdatesPage />} path="updates" />
        <Route element={<HealthPage />} path="health" />
        <Route element={<StoragePage />} path="storage" />
        <Route element={<NetworkPage />} path="network" />
        <Route element={<BackupsPage />} path="backups" />
        {/* System was split into the pages above; old links land on Health. */}
        <Route element={<OldSystemLink />} path="system" />
        <Route element={<LibraryPage />} path="library" />
        <Route element={<ConnectPage />} path="connect" />
        <Route element={<AutomationPage />} path="automation" />
        <Route element={<Navigate replace to="/config/accounts" />} path="config" />
        <Route element={<ConfigPage />} path="config/:name" />
        <Route element={<NotFoundPage />} path="*" />
      </Route>
    </Routes>
  );
}
