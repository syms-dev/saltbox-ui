import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { App, ContainerState } from '../api/types';
import { updateVersions, UpdateVersions } from './app-links';

const container = (overrides: Partial<ContainerState>): ContainerState => ({
  health: null,
  image: 'ghcr.io/hotio/sonarr:release',
  name: 'sonarr',
  status: 'running',
  update: 'update',
  update_detail: '',
  urls: [],
  ...overrides,
});

const app = (containers: ContainerState[], links: App['app_links'] = []): Pick<App, 'app_links' | 'containers'> => ({
  app_links: links,
  containers,
});

describe('what an update brings', () => {
  it('shows the version it moves from and to, and the releases page', () => {
    render(
      <UpdateVersions
        app={app(
          [container({ update_version: '4.0.11', version: '4.0.10' })],
          [{ name: 'Releases', purpose: 'release', url: 'https://github.com/hotio/sonarr/pkgs/container/sonarr' }],
        )}
      />,
    );
    expect(screen.getByText('4.0.10')).toBeInTheDocument();
    expect(screen.getByText('4.0.11')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Releases' })).toHaveAttribute(
      'href',
      'https://github.com/hotio/sonarr/pkgs/container/sonarr',
    );
  });

  it('says nothing it does not know, and never links anything but https', () => {
    expect(updateVersions(app([container({ update_version: '' })]))).toBeNull();
    const { container: root } = render(
      <UpdateVersions app={app([container({})], [{ name: 'x', purpose: 'release', url: 'javascript:alert(1)' }])} />,
    );
    expect(root).toBeEmptyDOMElement();
  });
});
