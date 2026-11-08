import React from 'react';

export const StudioFooter: React.FC = () => {
  return (
    <footer className="pt-4 border-t border-gray-100 flex items-center justify-between text-xs text-[var(--color-text-muted)]">
      <span>Dashboard by AG Studio. Data: sanitized ledger from the PayPal sandbox.</span>
    </footer>
  );
};

