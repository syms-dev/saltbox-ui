import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { AppAvatar } from './app-avatar';
import { OpenLinks } from './app-links';
import { ErrorBoundary } from './error-boundary';
import { FeedbackProvider, useConfirm } from './feedback';

/** What a container's labels, an app's answer or a log line can put on the page. */
describe('data that is not what the page expects', () => {
  it('an address that is not one does not take the page down', () => {
    render(
      <>
        <OpenLinks urls={['https://a b', 'not a url', 'https://sonarr.example.com']} />
        <AppAvatar role="../../etc/passwd" title="Odd" />
        <AppAvatar role="not-a-known-app" title="Radarr" />
        <AppAvatar role="plex" title="Plex" />
      </>,
    );
    expect(screen.getByRole('link', { name: 'Open sonarr.example.com' })).toBeInTheDocument();
    // Only a bundled icon, from this origin; never a path built from data, never the app's own
    // address (the browser would send the sign-on cookie to it).
    const icons = Array.from(document.querySelectorAll('img')).map((img) => img.getAttribute('src'));
    expect(icons).toEqual(['/icons/plex.webp']);
  });

  it('a page that cannot be drawn leaves a way out instead of a blank screen', () => {
    const quiet = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    function Broken(): React.ReactNode {
      throw new Error('unexpected data');
    }
    render(
      <ErrorBoundary>
        <Broken />
      </ErrorBoundary>,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('This page could not be shown');
    expect(screen.getByRole('button', { name: 'Reload' })).toBeInTheDocument();
    quiet.mockRestore();
  });

  it('markup in text is shown as text', () => {
    const hostile = '<img src=x onerror="window.pwned = true"><script>window.pwned = true</script>';
    render(<OpenLinks urls={[`https://example.com/${hostile}`]} />);
    render(<p>{hostile}</p>);
    expect(document.querySelector('img')).toBeNull();
    expect(document.querySelector('script')).toBeNull();
    expect((window as unknown as { pwned?: boolean }).pwned).toBeUndefined();
  });
});

describe('confirming something that cannot be undone', () => {
  function Ask({ tone }: { tone: 'danger' | 'primary' }): React.ReactNode {
    const confirm = useConfirm();
    return (
      <button
        onClick={() => void confirm({ body: 'sure?', confirmLabel: 'Do it', title: 'Really?', tone })}
        type="button"
      >
        ask
      </button>
    );
  }

  it('starts on Cancel, so a stray Enter does nothing', async () => {
    render(
      <FeedbackProvider>
        <Ask tone="danger" />
      </FeedbackProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'ask' }));
    const dialog = await screen.findByRole('dialog', { name: 'Really?' });
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus();
  });

  it('starts on the action when it is harmless', async () => {
    render(
      <FeedbackProvider>
        <Ask tone="primary" />
      </FeedbackProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'ask' }));
    const dialog = await screen.findByRole('dialog', { name: 'Really?' });
    expect(within(dialog).getByRole('button', { name: 'Do it' })).toHaveFocus();
  });
});
