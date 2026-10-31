import React, { useState } from 'react';
import { KeyRound, Lock, Unlock, AlertCircle } from 'lucide-react';
import { verifyWhoami } from '../adminClient';

interface Props {
  isAdminUnlocked: boolean;
  onUnlock: (token: string) => void;
  onLock: () => void;
}

export const AdminUnlockSection: React.FC<Props> = ({
  isAdminUnlocked,
  onUnlock,
  onLock,
}) => {
  // Kept in React state ONLY (never localStorage, sessionStorage, cookies, or URLs)
  const [tokenInput, setTokenInput] = useState('');
  const [isVerifying, setIsVerifying] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const handleVerify = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!tokenInput.trim()) return;

    setIsVerifying(true);
    setErrorMsg(null);
    try {
      const res = await verifyWhoami(tokenInput);
      if (res && res.admin) {
        onUnlock(tokenInput.trim());
        setTokenInput('');
      } else {
        setErrorMsg('Unauthorized: token verification failed.');
      }
    } catch (err: any) {
      setErrorMsg(err?.message || 'Unauthorized: invalid or missing operator token.');
    } finally {
      setIsVerifying(false);
    }
  };

  const handleLock = () => {
    setTokenInput('');
    setErrorMsg(null);
    onLock();
  };

  return (
    <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <div className="flex items-center gap-3">
        <div
          className={`flex h-9 w-9 items-center justify-center rounded-lg ${
            isAdminUnlocked ? 'bg-emerald-50 text-emerald-700' : 'bg-gray-100 text-gray-600'
          }`}
        >
          {isAdminUnlocked ? <Unlock className="h-4 w-4" /> : <Lock className="h-4 w-4" />}
        </div>
        <div>
          <div className="flex items-center gap-2">
            <span className="text-xs font-bold uppercase tracking-wider text-[var(--color-accent)]">
              Access Control
            </span>
            <span
              className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                isAdminUnlocked
                  ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                  : 'bg-amber-50 text-amber-800 border border-amber-200'
              }`}
            >
              {isAdminUnlocked ? 'Admin Unlocked' : 'Preview only'}
            </span>
          </div>
          <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
            {isAdminUnlocked
              ? 'Operator permissions verified for active session (memory only).'
              : 'Public visitors have read-only preview access. Operators must authenticate to apply changes.'}
          </p>
        </div>
      </div>

      <div className="flex flex-col items-end gap-1.5">
        {isAdminUnlocked ? (
          <button
            onClick={handleLock}
            className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-gray-50 px-3 py-1.5 text-xs font-semibold text-[var(--color-primary)] hover:bg-gray-100 transition"
          >
            <Lock className="h-3.5 w-3.5" /> Lock Admin
          </button>
        ) : (
          <form onSubmit={handleVerify} className="flex items-center gap-2">
            <div className="relative">
              <KeyRound className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-gray-400" />
              <input
                type="password"
                placeholder="Paste operator token..."
                value={tokenInput}
                onChange={(e) => setTokenInput(e.target.value)}
                autoComplete="off"
                className="w-48 sm:w-56 rounded-lg border border-gray-200 bg-white py-1.5 pl-8 pr-2.5 text-xs text-[var(--color-primary)] placeholder-gray-400 focus:border-[var(--color-accent)] focus:outline-hidden"
              />
            </div>
            <button
              type="submit"
              disabled={isVerifying || !tokenInput.trim()}
              className="inline-flex items-center gap-1 rounded-lg bg-[var(--color-primary)] px-3 py-1.5 text-xs font-semibold text-white shadow-xs hover:opacity-90 disabled:opacity-50 transition"
            >
              {isVerifying ? 'Verifying...' : 'Unlock'}
            </button>
          </form>
        )}

        {errorMsg && (
          <div className="flex items-center gap-1 text-[11px] text-red-600">
            <AlertCircle className="h-3 w-3 shrink-0" />
            <span>{errorMsg}</span>
          </div>
        )}
      </div>
    </div>
  );
};

