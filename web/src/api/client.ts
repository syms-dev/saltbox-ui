import type { JobRequest } from './types';

const BASE = '/api';

/**
 * One path segment. Names here come from the address bar (a link someone sent can put
 * anything there), so a `?` or `/` in one must stay part of the name: it never becomes
 * a query string or another path.
 */
function seg(value: string): string {
  return encodeURIComponent(value);
}

export const API_ROUTES = {
  APPS: '/apps',
  AUTOMATION: '/automation',
  AUTOMATION_MAINTENANCE: '/automation/maintenance',
  AUTOMATION_TEST: '/automation/test',
  BACKUPS: '/backups',
  BACKUPS_RUN: '/backups/run',
  CONFIG: '/config',
  CONFIG_VALIDATE: '/config/validate',
  INVENTORY: '/inventory',
  JOBS: '/jobs',
  MEDIA: '/media',
  META: '/meta',
  OVERVIEW: '/overview',
  DISC_IMAGES: '/discimages',
  DISC_IMAGES_REPLACE: '/discimages/replace',
  RECYCLARR: '/recyclarr',
  RECYCLARR_ASSIGN: '/recyclarr/assign',
  RECYCLARR_SYNC: '/recyclarr/sync',
  RECYCLARR_USAGE: '/recyclarr/usage',
  DISMISS: '/dismissals',
  DISMISS_RESTORE: '/dismissals/restore',
  DRIVES: '/drives',
  EXPLAIN: '/explain',
  PROBLEMS: '/problems',
  PROBLEMS_EXPLAIN: '/problems/explain',
  PROBLEMS_FIX: '/problems/fix',
  REBOOT: '/host/reboot',
  SEEDING: '/seeding',
  SELF: '/self',
  SELF_UPDATE: '/self/update',
  RESOURCES: '/resources',
  SALTBOX_UPDATE: '/saltbox/update',
  SETUP: '/setup',
  SETUP_APPLY: '/setup/apply',
  TAILSCALE: '/tailscale',
  TAILSCALE_SERVE: '/tailscale/serve',
  UPDATES: '/updates',
  UPDATES_APPLY: '/updates/apply',
  UPDATES_CHECK: '/updates/check',
  appUpdate: (repo: string, role: string) => `/apps/${seg(repo)}/${seg(role)}/update` as const,
  appDetail: (repo: string, role: string) => `/apps/${seg(repo)}/${seg(role)}` as const,
  appOverrides: (repo: string, role: string) => `/apps/${seg(repo)}/${seg(role)}/overrides` as const,
  appReleaseNotes: (repo: string, role: string) => `/apps/${seg(repo)}/${seg(role)}/release-notes` as const,
  configFile: (name: string) => `/config/${seg(name)}` as const,
  containerAction: (name: string, action: string) => `/containers/${seg(name)}/${seg(action)}` as const,
  containerLogs: (name: string, tail: number) => `/containers/${seg(name)}/logs?tail=${tail}` as const,
  job: (id: string) => `/jobs/${seg(id)}` as const,
  appUninstall: (repo: string, role: string) => `/apps/${seg(repo)}/${seg(role)}/uninstall` as const,
  jobAnswer: (id: string) => `/jobs/${seg(id)}/answer` as const,
  jobCancel: (id: string) => `/jobs/${seg(id)}/cancel` as const,
  jobLog: (id: string) => `/jobs/${seg(id)}/log` as const,
  jobStream: (id: string) => `/jobs/${seg(id)}/stream` as const,
} as const;

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

type ErrorBody = { detail?: unknown; error?: unknown };

function describe(body: ErrorBody | null, fallback: string): string {
  const detail = body?.detail ?? body?.error;
  if (typeof detail === 'string') return detail;
  if (detail !== undefined) return JSON.stringify(detail);
  return fallback;
}

/** Fetch JSON from the daemon. Throws `ApiError` for non-2xx responses. */
export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set('Accept', 'application/json');
  if (init?.body !== undefined && init.body !== null) headers.set('Content-Type', 'application/json');
  const response = await fetch(`${BASE}${path}`, { ...init, headers });
  if (!response.ok) {
    let body: ErrorBody | null;
    try {
      body = (await response.json()) as ErrorBody;
    } catch {
      body = null;
    }
    throw new ApiError(response.status, describe(body, `${response.status} ${response.statusText}`));
  }
  return (await response.json()) as T;
}

export function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method: 'POST' };
  if (body !== undefined) init.body = JSON.stringify(body);
  return apiFetch<T>(path, init);
}

export function apiPut<T>(path: string, body: unknown): Promise<T> {
  return apiFetch<T>(path, { body: JSON.stringify(body), method: 'PUT' });
}

export function apiPatch<T>(path: string, body: unknown): Promise<T> {
  return apiFetch<T>(path, { body: JSON.stringify(body), method: 'PATCH' });
}

/** Absolute WebSocket URL for a job's log stream, matching the page's scheme and host. */
export function jobStreamUrl(id: string, location: Pick<Location, 'host' | 'protocol'> = window.location): string {
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  return `${scheme}://${location.host}${BASE}${API_ROUTES.jobStream(id)}`;
}

export function installRequest(tags: string[], options: Partial<JobRequest> = {}): JobRequest {
  return { kind: 'install', tags, ...options };
}
