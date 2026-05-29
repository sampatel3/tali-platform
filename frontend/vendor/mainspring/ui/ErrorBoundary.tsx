/** App-level error boundary — generalised app infra. */
import { Component, ReactNode } from "react";

type Fallback = ReactNode | ((error: Error) => ReactNode);

interface Props {
  children: ReactNode;
  /**
   * Optional scoped fallback. Lets a caller wrap a single panel without
   * taking over the whole screen (e.g. a failed-scoring pane). A function
   * receives the caught error; a node renders as-is. When absent, the
   * default full-screen reload card is shown.
   */
  fallback?: Fallback;
}

export class ErrorBoundary extends Component<Props, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidCatch(error: Error) {
    console.error("UI error:", error);
  }
  render() {
    if (this.state.error) {
      if (this.props.fallback !== undefined) {
        return typeof this.props.fallback === "function"
          ? this.props.fallback(this.state.error)
          : this.props.fallback;
      }
      return (
        <div className="min-h-screen grid place-items-center bg-bg text-cloud p-6">
          <div className="max-w-md text-center">
            <h1 className="font-display text-2xl font-bold text-cloud tracking-tight">Something went wrong.</h1>
            <p className="mt-2 text-mute text-sm">{this.state.error.message}</p>
            <button className="btn-primary mt-5 px-5 py-2" onClick={() => location.reload()}>Reload</button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
