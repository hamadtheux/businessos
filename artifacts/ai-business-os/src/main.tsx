import { createRoot } from 'react-dom/client';

import App from './App';
import { ErrorBoundary } from '@/components/error-boundary';

import './index.css';

createRoot(document.getElementById('root')!, {
  // Keeps caught errors off reportError(), which would raise the dev overlay.
  onCaughtError: (error, errorInfo) => {
    if (import.meta.env.DEV) {
      console.error(error, errorInfo.componentStack);
    } else {
      console.error('A rendered application component failed.');
    }
  },
}).render(
  <ErrorBoundary>
    <App />
  </ErrorBoundary>,
);
