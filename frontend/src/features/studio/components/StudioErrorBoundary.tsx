import { Component, type ErrorInfo, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { AlertCircle, ArrowLeft } from 'lucide-react';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class StudioErrorBoundary extends Component<Props, State> {
  public state: State = {
    hasError: false,
    error: null,
  };

  public static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('Studio Error Boundary caught an error:', error, errorInfo);
  }

  public render() {
    if (this.state.hasError) {
      return (
        <div className="mx-auto my-12 flex max-w-xl flex-col items-center rounded-xl border border-red-200 bg-red-50/50 p-8 text-center shadow-xs">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-red-100 text-red-600 mb-4">
            <AlertCircle className="h-6 w-6" />
          </div>
          <h2 className="text-xl font-bold text-[var(--color-primary)]">
            Studio encountered an issue
          </h2>
          <p className="mt-2 text-sm text-[var(--color-text-muted)] max-w-md">
            An unexpected error occurred while rendering the studio dashboard. You can inspect the
            verified vs asserted ledger directly.
          </p>
          {this.state.error && (
            <p className="mt-4 max-w-full overflow-x-auto rounded-md border border-red-200 bg-white p-3 font-mono text-xs text-red-700">
              {this.state.error.message}
            </p>
          )}
          <div className="mt-6 flex items-center gap-4">
            <button
              type="button"
              onClick={() => this.setState({ hasError: false, error: null })}
              className="rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-[var(--color-primary)] hover:bg-gray-50 transition"
            >
              Try Again
            </button>
            <Link
              to="/ledger"
              className="inline-flex items-center gap-2 rounded-lg bg-[var(--color-primary)] px-4 py-2 text-sm font-medium text-white hover:opacity-90 transition"
            >
              <ArrowLeft className="h-4 w-4" />
              Go to Ledger
            </Link>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

