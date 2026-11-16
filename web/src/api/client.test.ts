import { afterEach, describe, expect, it, vi } from 'vitest';

import { API_ROUTES, ApiError, apiFetch, apiPost, installRequest, jobStreamUrl } from './client';

function mockFetch(status: number, body: unknown): ReturnType<typeof vi.fn> {
  const fn = vi.fn(() =>
    Promise.resolve(new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' }, status })),
  );
  vi.stubGlobal('fetch', fn);
  return fn;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('apiFetch', () => {
  it('prefixes /api and parses JSON', async () => {
    const fetchMock = mockFetch(200, { version: '1' });
    await expect(apiFetch<{ version: string }>(API_ROUTES.META)).resolves.toEqual({ version: '1' });
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/meta');
    expect(new Headers(init.headers).get('Accept')).toBe('application/json');
  });

  it('turns FastAPI error details into ApiError', async () => {
    mockFetch(409, { detail: 'a job is already running' });
    const error = await apiFetch(API_ROUTES.JOBS).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(409);
    expect((error as ApiError).message).toBe('a job is already running');
  });

  it('falls back to the status line when the body is not JSON', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(new Response('nope', { status: 502, statusText: 'Bad Gateway' }))),
    );
    await expect(apiFetch(API_ROUTES.META)).rejects.toThrow('502 Bad Gateway');
  });

  it('sends JSON bodies with the content type', async () => {
    const fetchMock = mockFetch(202, { id: 'x' });
    await apiPost(API_ROUTES.JOBS, installRequest(['sonarr']));
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.method).toBe('POST');
    expect(init.body).toBe(JSON.stringify({ kind: 'install', tags: ['sonarr'] }));
    expect(new Headers(init.headers).get('Content-Type')).toBe('application/json');
  });
});

describe('jobStreamUrl', () => {
  it('matches the page scheme', () => {
    expect(jobStreamUrl('abc', { host: 'saltbox.example.com', protocol: 'https:' })).toBe(
      'wss://saltbox.example.com/api/jobs/abc/stream',
    );
    expect(jobStreamUrl('abc', { host: 'localhost:5173', protocol: 'http:' })).toBe(
      'ws://localhost:5173/api/jobs/abc/stream',
    );
  });
});

describe('API_ROUTES', () => {
  it('keeps a name from the address bar inside its own path segment', () => {
    // A link someone sends can put anything in the address: none of it may become a
    // query string (asking for secrets to be revealed) or reach another route.
    expect(API_ROUTES.configFile('accounts?reveal=true')).toBe('/config/accounts%3Freveal%3Dtrue');
    expect(API_ROUTES.appDetail('saltbox', '../../state/facts?secrets=true')).toBe(
      '/apps/saltbox/..%2F..%2Fstate%2Ffacts%3Fsecrets%3Dtrue',
    );
    expect(API_ROUTES.job('x/../../host/reboot')).toBe('/jobs/x%2F..%2F..%2Fhost%2Freboot');
    expect(API_ROUTES.jobCancel('a#b')).toBe('/jobs/a%23b/cancel');
    expect(API_ROUTES.containerAction('sonarr', 'restart/../../x')).toBe('/containers/sonarr/restart%2F..%2F..%2Fx');
    expect(API_ROUTES.appUninstall('saltbox', 'sonarr')).toBe('/apps/saltbox/sonarr/uninstall');
  });
});
