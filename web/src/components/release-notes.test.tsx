import { screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ReleaseNotes } from '../api/types';
import { renderAt, stubApi } from '../test-utils';
import { ReleaseNotesCard } from './release-notes';

const NOTES: ReleaseNotes = {
  error: '',
  more: false,
  project: 'Sonarr/Sonarr',
  releases: [
    {
      body: '- Fixed: <b>imports</b> of season packs\n- New: faster search',
      name: '4.0.11.2680',
      prerelease: false,
      published_at: '2026-11-10T12:00:00Z',
      url: 'https://github.com/Sonarr/Sonarr/releases/tag/v4.0.11.2680',
      version: '4.0.11.2680',
    },
    {
      body: '',
      name: '',
      prerelease: false,
      published_at: '',
      url: 'https://evil.example/x',
      version: '4.0.10.2600',
    },
  ],
  releases_url: 'https://github.com/Sonarr/Sonarr/releases',
};

afterEach(() => vi.unstubAllGlobals());

describe('ReleaseNotesCard', () => {
  it('shows each release the update brings, newest open, as plain text', async () => {
    const api = stubApi({ '/apps/saltbox/sonarr/release-notes': NOTES });
    renderAt(<ReleaseNotesCard repo="saltbox" role="sonarr" />);
    const card = await screen.findByRole('region', { name: "What's new" });
    // The maintainers' text is text: markup in it is shown, never rendered.
    expect(await within(card).findByText(/<b>imports<\/b> of season packs/)).toBeVisible();
    expect(within(card).getByText('No notes for this release.')).toBeInTheDocument();
    expect(card.querySelector('b')).toBeNull();
    expect(within(card).getByRole('link', { name: '4.0.11.2680 on GitHub ↗' })).toHaveAttribute(
      'href',
      'https://github.com/Sonarr/Sonarr/releases/tag/v4.0.11.2680',
    );
    // A link GitHub's answer pointed elsewhere is not offered.
    expect(within(card).queryByRole('link', { name: /4.0.10.2600/ })).toBeNull();
    expect(within(card).getByRole('link', { name: 'All releases on GitHub ↗' })).toBeInTheDocument();
    expect(api).toHaveBeenCalledWith('/api/apps/saltbox/sonarr/release-notes', expect.anything());
  });

  it('says when GitHub would not answer', async () => {
    stubApi({ '/apps/saltbox/radarr/release-notes': { ...NOTES, error: 'GitHub answered HTTP 500', releases: [] } });
    renderAt(<ReleaseNotesCard repo="saltbox" role="radarr" />);
    expect(await screen.findByText('The release notes could not be read: GitHub answered HTTP 500.')).toBeVisible();
  });

  it('shows nothing for an app whose releases are not on GitHub', async () => {
    const api = stubApi({
      '/apps/saltbox/plex/release-notes': { ...NOTES, project: '', releases: [], releases_url: '' },
    });
    renderAt(<ReleaseNotesCard repo="saltbox" role="plex" />);
    await waitFor(() => {
      expect(api).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(screen.queryByText('Reading the release notes on GitHub…')).toBeNull();
    });
    expect(screen.queryByRole('region')).toBeNull();
  });
});
