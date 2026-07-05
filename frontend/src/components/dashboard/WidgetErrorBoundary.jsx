import React from 'react';
import { ErrorState } from '../ui/ErrorState';

/**
 * Isolates a widget's render errors so one broken widget never takes down the
 * whole dashboard.
 */
export class WidgetErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error) {
    console.error('Widget crashed:', error);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="bg-slate-800/40 border border-slate-700/50 rounded-xl p-5 h-full">
          <ErrorState message="This widget failed to render." onRetry={() => this.setState({ hasError: false })} />
        </div>
      );
    }
    return this.props.children;
  }
}

export default WidgetErrorBoundary;
