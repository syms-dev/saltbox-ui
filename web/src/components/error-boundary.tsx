import { Component } from 'react';

type Props = { children: React.ReactNode };
type State = { failed: boolean };

/**
 * Keeps one page that cannot be drawn from blanking the whole app. What the app shows
 * comes from the server's containers and files; something unexpected in it should cost
 * a page a reload, not the person the menu.
 */
export class ErrorBoundary extends Component<Props, State> {
  override state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  override render(): React.ReactNode {
    if (!this.state.failed) return this.props.children;
    return (
      <main className="stack" role="alert" style={{ margin: '4rem auto', maxWidth: '32rem', padding: '0 1rem' }}>
        <h1>This page could not be shown</h1>
        <p className="muted">
          Something in what the server sent was not what the page expected. The server itself is not affected.
        </p>
        <p className="row">
          <button className="primary" onClick={() => window.location.reload()} type="button">
            Reload
          </button>
          <a className="button" href="/">
            Go to Home
          </a>
        </p>
      </main>
    );
  }
}
