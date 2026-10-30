import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AppShell } from './components/AppShell';
import { ConfigErrorScreen } from './components/ConfigErrorScreen';
import { getApiConfig } from './api/client';
import { CurrencyProvider } from './context/CurrencyContext';

// Lazy load feature routes for performance (Suspense-first approach)
const DashboardRoute = React.lazy(() => import('./features/dashboard/components/DashboardRoute'));
const RootCauseRoute = React.lazy(() => import('./features/root-cause/components/RootCauseRoute'));
const BaselineRoute = React.lazy(() => import('./features/baseline/components/BaselineRoute'));
const PtpRoute = React.lazy(() => import('./features/ptp/components/PtpRoute'));
const AuditTrailRoute = React.lazy(() => import('./features/audit-trail/components/AuditTrailRoute'));
const LedgerRoute = React.lazy(() => import('./features/ledger/components/LedgerRoute'));
const LiveRoute = React.lazy(() => import('./features/live/components/LiveRoute'));

// Global query client with retry backoff for transient failures
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failureCount, error: any) => {
        const isHttpError = typeof error?.status === 'number';
        const isTransient = !isHttpError || error.status >= 500;
        return failureCount < 2 && isTransient;
      },
      retryDelay: (attemptIndex) => Math.min(1000 * 2 ** attemptIndex, 30000),
      staleTime: 1000 * 60,
      refetchOnWindowFocus: false,
    },
  },
});

function App() {
  const { error: configError } = getApiConfig();

  // In a production build, if VITE_API_BASE_URL is missing or invalid, show a full-page configuration error
  if (import.meta.env.PROD && configError) {
    return <ConfigErrorScreen error={configError} />;
  }

  return (
    <QueryClientProvider client={queryClient}>
      <CurrencyProvider>
        <BrowserRouter>
          <Routes>
            <Route element={<AppShell />}>
              <Route path="/" element={<DashboardRoute />} />
              <Route path="/live" element={<LiveRoute />} />
              <Route path="/ledger" element={<LedgerRoute />} />
              <Route path="/root-cause" element={<RootCauseRoute />} />
              <Route path="/baseline" element={<BaselineRoute />} />
              <Route path="/ptp" element={<PtpRoute />} />
              <Route path="/audit-trail" element={<AuditTrailRoute />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </CurrencyProvider>
    </QueryClientProvider>
  );
}

export default App;
