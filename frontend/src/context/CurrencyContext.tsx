import React, { createContext, useContext, useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import type { components } from '@/api/schema';

type CurrencyCount = components['schemas']['CurrencyCount'];

interface CurrencyContextValue {
  currency: string;
  setCurrency: (c: string) => void;
  availableCurrencies: string[];
  showSelector: boolean;
}

const defaultEnvCurrency =
  (typeof import.meta !== 'undefined' && import.meta.env?.VITE_DEFAULT_CURRENCY) ||
  ((globalThis as any).process?.env?.VITE_DEFAULT_CURRENCY) ||
  'USD';

const CurrencyContext = createContext<CurrencyContextValue>({
  currency: defaultEnvCurrency,
  setCurrency: () => {},
  availableCurrencies: [defaultEnvCurrency],
  showSelector: false,
});

export const CurrencyProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [currency, setCurrency] = useState<string>(defaultEnvCurrency);

  const { data: currenciesData } = useQuery({
    queryKey: ['metrics', 'currencies'],
    queryFn: ({ signal }) => apiClient<CurrencyCount[]>('/metrics/currencies', { signal }),
    staleTime: 5 * 60 * 1000,
    retry: 1,
  });

  const availableCurrencies = useMemo(() => {
    if (!currenciesData || !Array.isArray(currenciesData) || currenciesData.length === 0) {
      return [currency];
    }
    return currenciesData.map((c) => c.currency);
  }, [currenciesData, currency]);

  const showSelector = availableCurrencies.length > 1;

  const value = useMemo(
    () => ({
      currency,
      setCurrency,
      availableCurrencies,
      showSelector,
    }),
    [currency, availableCurrencies, showSelector]
  );

  return <CurrencyContext.Provider value={value}>{children}</CurrencyContext.Provider>;
};

export const useCurrency = () => useContext(CurrencyContext);

export const CurrencySelector: React.FC<{ className?: string }> = ({ className }) => {
  const { currency, setCurrency, availableCurrencies, showSelector } = useCurrency();

  if (!showSelector) return null;

  return (
    <div className={`flex items-center gap-1.5 text-xs ${className ?? ''}`}>
      <span className="font-semibold text-[var(--color-text-muted)]">Currency:</span>
      <select
        value={currency}
        onChange={(e) => setCurrency(e.target.value)}
        className="rounded border border-gray-200 bg-white px-2 py-1 font-semibold text-[var(--color-primary)] outline-none focus:border-[var(--color-accent)]"
      >
        {availableCurrencies.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>
    </div>
  );
};
