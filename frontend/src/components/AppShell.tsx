import React, { useState, Suspense } from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import {
  LayoutDashboard,
  TrendingDown,
  Target,
  ScrollText,
  GitPullRequest,
  Radio,
  BookOpen,
  SlidersHorizontal,
  Menu,
  X,
} from 'lucide-react';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';
import { motion, AnimatePresence } from 'framer-motion';
import { CurrencySelector } from '@/context/CurrencyContext';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const NAV_ITEMS = [
  { path: '/', label: 'Overview', icon: LayoutDashboard },
  { path: '/live', label: 'Mission Control', icon: Radio },
  { path: '/rules', label: 'Rules', icon: SlidersHorizontal },
  { path: '/ledger', label: 'Ledger', icon: BookOpen },
  { path: '/root-cause', label: 'Root Cause', icon: TrendingDown },
  { path: '/baseline', label: 'Baseline', icon: Target },
  { path: '/ptp', label: 'Promises', icon: GitPullRequest },
  { path: '/audit-trail', label: 'Audit Trail', icon: ScrollText },
];

const SuspenseLoader = () => (
  <div className="flex h-full w-full items-center justify-center p-12">
    <div className="h-6 w-6 animate-spin rounded-full border-2 border-[var(--color-accent)] border-t-transparent" />
  </div>
);

export const AppShell: React.FC = () => {
  const [mobileDrawerOpen, setMobileDrawerOpen] = useState(false);

  return (
    <div className="flex min-h-screen w-full flex-col md:flex-row bg-[var(--color-bg-base)] text-[var(--color-text-primary)] antialiased">
      {/* Mobile Top Header */}
      <div className="flex items-center justify-between border-b border-gray-200 bg-[var(--color-bg-surface)] px-4 py-3 md:hidden">
        <div className="flex items-center gap-3">
          <button
            onClick={() => setMobileDrawerOpen(true)}
            className="rounded-lg p-1.5 text-[var(--color-primary)] hover:bg-gray-100 transition"
            aria-label="Open navigation drawer"
          >
            <Menu className="h-6 w-6" />
          </button>
          <div className="flex items-center gap-2">
            <div className="h-4 w-4 bg-[var(--color-primary)] rounded-xs transform rotate-45" />
            <span className="text-lg font-bold text-[var(--color-primary)]">Vasooli</span>
          </div>
        </div>
        <CurrencySelector />
      </div>

      {/* Mobile Drawer Backdrop and Sidebar */}
      <AnimatePresence>
        {mobileDrawerOpen && (
          <div className="fixed inset-0 z-50 flex md:hidden">
            {/* Backdrop */}
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setMobileDrawerOpen(false)}
              className="fixed inset-0 bg-black/40 backdrop-blur-xs"
            />

            {/* Slide-out Drawer */}
            <motion.nav
              initial={{ x: -280 }}
              animate={{ x: 0 }}
              exit={{ x: -280 }}
              transition={{ type: 'spring', damping: 25, stiffness: 300 }}
              className="relative flex w-68 max-w-[80vw] flex-col bg-[var(--color-bg-surface)] px-4 py-6 shadow-xl"
            >
              <div className="mb-6 flex items-center justify-between px-2">
                <div className="flex items-center gap-2">
                  <div className="h-4 w-4 bg-[var(--color-primary)] rounded-xs transform rotate-45" />
                  <span className="text-lg font-bold text-[var(--color-primary)]">Vasooli</span>
                </div>
                <button
                  onClick={() => setMobileDrawerOpen(false)}
                  className="rounded-lg p-1.5 text-[var(--color-text-muted)] hover:bg-gray-100"
                  aria-label="Close navigation drawer"
                >
                  <X className="h-5 w-5" />
                </button>
              </div>

              <div className="flex flex-1 flex-col gap-1">
                {NAV_ITEMS.map((item) => (
                  <NavLink
                    key={item.path}
                    to={item.path}
                    onClick={() => setMobileDrawerOpen(false)}
                    className={({ isActive }) =>
                      cn(
                        'flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors',
                        isActive
                          ? 'bg-white text-[var(--color-primary)] font-bold shadow-xs border border-gray-100'
                          : 'text-[var(--color-text-muted)] hover:bg-gray-100/50 hover:text-[var(--color-primary)]'
                      )
                    }
                  >
                    <item.icon className="h-4 w-4" />
                    <span>{item.label}</span>
                  </NavLink>
                ))}
              </div>

              <div className="border-t border-gray-200 pt-4 px-2">
                <CurrencySelector />
              </div>
            </motion.nav>
          </div>
        )}
      </AnimatePresence>

      {/* Desktop Sidebar Navigation */}
      <nav className="hidden md:flex w-64 flex-col border-r border-gray-200 bg-[var(--color-bg-surface)] px-4 py-8 shadow-xs z-10 shrink-0">
        <div className="mb-8 px-2 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="h-4 w-4 bg-[var(--color-primary)] rounded-xs transform rotate-45" />
            <h1 className="text-lg font-semibold tracking-tight text-[var(--color-primary)]">Vasooli</h1>
          </div>
        </div>

        <div className="flex flex-1 flex-col gap-1.5">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              className={({ isActive }) =>
                cn(
                  'relative flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors',
                  isActive
                    ? 'text-[var(--color-primary)] font-semibold'
                    : 'text-[var(--color-text-muted)] hover:bg-gray-100/50 hover:text-[var(--color-primary)]'
                )
              }
            >
              {({ isActive }) => (
                <>
                  <item.icon className="h-4 w-4 z-10" />
                  <span className="z-10">{item.label}</span>
                  {isActive && (
                    <motion.div
                      layoutId="activeNavTab"
                      className="absolute inset-0 rounded-lg bg-white shadow-xs border border-gray-100"
                      transition={{ type: 'spring', stiffness: 350, damping: 30 }}
                    />
                  )}
                </>
              )}
            </NavLink>
          ))}
        </div>

        <div className="border-t border-gray-200 pt-4 px-2">
          <CurrencySelector />
        </div>
      </nav>

      {/* Main Content Area */}
      <main className="flex-1 overflow-y-auto overflow-x-hidden relative h-screen">
        <div className="mx-auto max-w-6xl p-6 md:p-8">
          <Suspense fallback={<SuspenseLoader />}>
            <Outlet />
          </Suspense>
        </div>
      </main>
    </div>
  );
};
