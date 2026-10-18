import { Component, ErrorInfo, ReactNode } from "react";

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

/** Keeps a rendering bug in one view from blanking the whole window. */
export class ErrorBoundary extends Component<Readonly<ErrorBoundaryProps>, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Tesseract view crashed", error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="crash" role="alert">
        <h1>This view stopped working</h1>
        <p>Tesseract hit an unexpected error while drawing this screen. Your settings are safe on disk.</p>
        <pre>{error.message}</pre>
        <button type="button" className="primary-button" onClick={() => window.location.reload()}>
          Reload Tesseract
        </button>
      </div>
    );
  }
}
