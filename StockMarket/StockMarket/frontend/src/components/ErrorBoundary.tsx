import React, { Component, ErrorInfo, ReactNode } from 'react';

interface Props {
    children: ReactNode;
}

interface State {
    hasError: boolean;
    error: Error | null;
}

class ErrorBoundary extends Component<Props, State> {
    public state: State = {
        hasError: false,
        error: null
    };

    public static getDerivedStateFromError(error: Error): State {
        return { hasError: true, error };
    }

    public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
        console.error('Uncaught error:', error, errorInfo);
    }

    public render() {
        if (this.state.hasError) {
            return (
                <div style={{ padding: 20, color: 'white', backgroundColor: '#330000' }}>
                    <h1>Something went wrong.</h1>
                    <pre>{this.state.error?.toString()}</pre>
                    <pre>{this.state.error?.stack}</pre>
                </div>
            );
        }

        return this.props.children;
    }
}

export default ErrorBoundary;
