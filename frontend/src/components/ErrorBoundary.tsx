import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
  fallback?: (error: Error, reset: () => void) => ReactNode;
}

interface State {
  error: Error | null;
}

/**
 * Phase 6: catch any uncaught render error in the subtree and show a
 * fallback instead of a white screen. The reset button clears the error
 * so the user can retry without a full page reload.
 *
 * Class component because React still requires getDerivedStateFromError /
 * componentDidCatch for error boundaries — there's no hook equivalent.
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error('ErrorBoundary caught', error, info.componentStack);
  }

  reset = () => {
    this.setState({ error: null });
  };

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    if (this.props.fallback) return this.props.fallback(error, this.reset);
    return (
      <div
        style={{
          padding: 24,
          margin: 24,
          border: '1px solid var(--color-border)',
          borderRadius: 8,
          background: 'var(--color-surface)',
          color: 'var(--color-text)',
          fontFamily: 'system-ui, sans-serif',
        }}
      >
        <h2 style={{ marginTop: 0 }}>页面渲染出错</h2>
        <pre
          style={{
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            fontSize: 13,
            color: 'var(--color-text-muted)',
            maxHeight: 300,
            overflow: 'auto',
          }}
        >
          {error.message}
          {error.stack ? `\n\n${error.stack}` : ''}
        </pre>
        <button
          type="button"
          onClick={this.reset}
          style={{
            marginTop: 12,
            padding: '6px 14px',
            border: '1px solid var(--color-border)',
            borderRadius: 6,
            background: 'transparent',
            color: 'var(--color-text)',
            cursor: 'pointer',
          }}
        >
          重试
        </button>
      </div>
    );
  }
}
