import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { DiscImages } from '../api/types';
import { renderAt, stubApi } from '../test-utils';
import { DiscImagesCard } from './disc-images-card';

function images(films: number): DiscImages {
  return {
    instances: [
      {
        blocked_by_profile: films > 1 ? ['Any'] : [],
        films: [
          {
            file: 'Disc One (2001).iso',
            file_id: 101,
            movie_id: 1,
            profile: 'HD Bluray + WEB',
            replaceable: true,
            size: 8_000_000_000,
            title: 'Disc One',
            year: 2001,
          },
          ...(films > 1
            ? [
                {
                  file: 'On Any (2004).iso',
                  file_id: 104,
                  movie_id: 4,
                  profile: 'Any',
                  replaceable: false,
                  size: 4_000_000_000,
                  title: 'On Any',
                  year: 2004,
                },
              ]
            : []),
        ].slice(0, films),
        name: 'radarr',
        size: 12_000_000_000,
      },
    ],
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('DiscImagesCard', () => {
  it('says nothing when there are no disc images', async () => {
    const api = stubApi({ '/discimages': () => images(0) });
    renderAt(<DiscImagesCard />);
    await waitFor(() => expect(api).toHaveBeenCalled());
    expect(screen.queryByRole('region', { name: 'Disc images in your films' })).toBeNull();
  });

  it('lists them, leaves the ones a search could not fix, and searches only after asking', async () => {
    const api = stubApi({
      '/discimages': () => images(2),
      'POST /discimages/replace': () => ({ deleted: 0, searched: 1, skipped: 1 }),
    });
    renderAt(<DiscImagesCard />);
    const card = await screen.findByRole('region', { name: 'Disc images in your films' });
    expect(card).toHaveTextContent('radarr has 2 films as disc images');
    expect(card).toHaveTextContent('On Any (2004)');
    expect(card).toHaveTextContent('Films on Any are left alone');
    fireEvent.click(within(card).getByRole('button', { name: 'Find proper copies' }));
    const dialog = await screen.findByRole('dialog', { name: 'Find proper copies in radarr?' });
    expect(dialog).toHaveTextContent('Each disc image stays until a better copy is downloaded');
    expect(api).not.toHaveBeenCalledWith('/api/discimages/replace', expect.anything());
    fireEvent.click(within(dialog).getByRole('button', { name: 'Search' }));
    await waitFor(() =>
      expect(api).toHaveBeenCalledWith(
        '/api/discimages/replace',
        expect.objectContaining({ body: JSON.stringify({ delete: false, instance: 'radarr' }) }),
      ),
    );
    expect(await screen.findByText(/Radarr is searching for 1 film/)).toBeInTheDocument();
  });

  it('asks plainly before deleting, with Cancel first', async () => {
    stubApi({ '/discimages': () => images(1) });
    renderAt(<DiscImagesCard />);
    const card = await screen.findByRole('region', { name: 'Disc images in your films' });
    fireEvent.click(within(card).getByRole('button', { name: 'Delete the images and search' }));
    const dialog = await screen.findByRole('dialog', { name: 'Delete radarr’s disc images?' });
    expect(dialog).toHaveTextContent('Until one is found, those films have no file');
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus();
  });
});
