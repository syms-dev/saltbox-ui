import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { MediaSnapshot } from '../api/types';
import { renderAt, stubApi } from '../test-utils';
import { MediaRow } from './home-media';

const withUpcoming = (upcoming: NonNullable<MediaSnapshot['upcoming']>): MediaSnapshot => ({
  checked_at: 1,
  upcoming,
});

describe('Coming up', () => {
  it('says which calendar could not be read instead of claiming nothing is on', async () => {
    stubApi({});
    renderAt(
      <MediaRow
        media={withUpcoming({
          available: true,
          errors: ['sonarr: Bad Request - Invalid Hostname'],
          items: [],
        })}
      />,
    );
    expect(await screen.findByText('Could not read sonarr: Bad Request - Invalid Hostname')).toBeInTheDocument();
    expect(screen.queryByText(/Nothing monitored/)).not.toBeInTheDocument();
  });

  it('still says when there is genuinely nothing this week', async () => {
    stubApi({});
    renderAt(
      <MediaRow
        media={{
          ...withUpcoming({ available: true, errors: [], items: [] }),
          plex: { available: true, sessions: [] } as unknown as NonNullable<MediaSnapshot['plex']>,
        }}
      />,
    );
    expect(await screen.findByText('Nothing monitored airs or releases this week.')).toBeInTheDocument();
  });
});
