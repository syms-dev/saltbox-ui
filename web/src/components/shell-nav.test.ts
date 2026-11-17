import { describe, expect, it } from 'vitest';

import { isCurrent, NAV } from './shell';

describe('the menu', () => {
  it('marks the page being shown, with each app page under Installed', () => {
    expect(isCurrent('/', '/')).toBe(true);
    expect(isCurrent('/', '/apps')).toBe(false);
    expect(isCurrent('/apps', '/apps')).toBe(true);
    expect(isCurrent('/apps', '/apps/saltbox/sonarr')).toBe(true);
    expect(isCurrent('/apps', '/apps/discover')).toBe(false);
    expect(isCurrent('/apps/discover', '/apps/discover')).toBe(true);
    expect(isCurrent('/activity', '/activity/job7')).toBe(true);
    expect(isCurrent('/config', '/config/accounts')).toBe(true);
    expect(isCurrent('/connect', '/config/accounts')).toBe(false);
  });

  it('lists every page once', () => {
    const pages = NAV.flatMap((group) => group.items.map((item) => item.to));
    expect(new Set(pages).size).toBe(pages.length);
    expect(pages).toEqual([
      '/',
      '/updates',
      '/activity',
      '/apps',
      '/apps/discover',
      '/library',
      '/connect',
      '/health',
      '/storage',
      '/network',
      '/backups',
      '/automation',
      '/config',
    ]);
  });
});
